"""
AI Interviews — live voice mock interviews over LiveKit.

Browser-facing router (`/interviews`, JWT + interview credits) and a worker-facing
router (`/internal/interviews`, shared secret) that the `interview_agent` uses to
fetch its briefing and post the transcript back when the room ends.

Flow: POST /interviews (setup + résumé snapshot; needs >= 1 credit) -> POST
/{id}/start (spends the credit, LiveKit token with agent dispatch) -> [room] ->
POST /internal/{id}/finalize (transcript) -> background report -> GET /{id} polls
until report_ready.

Access is prepaid credits, not a subscription — see app.utils.interview_credits.
"""

from __future__ import annotations

import hmac
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Query, Response, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.config import Roles, livekit_settings
from app.core.security import get_current_user, require_roles
from app.database.connection import get_db
from app.models.interview import InterviewSession, InterviewStatus
from app.models.resume import Resume
from app.models.user import User
from app.schemas.interview_schema import (
    InterviewCreate,
    InterviewFinalize,
    InterviewListResponse,
    InterviewStartResponse,
    LiveConnection,
)
from app.utils.interview_credits import charge_for_interview, require_available_credit
from app.utils.interviews import (
    apply_finalize,
    generate_report,
    now,
    question_target,
    reconcile_stale,
    sanitize_resume_snapshot,
    serialize_session,
)
from app.utils.livekit_tokens import interview_room_name, mint_interview_token, participant_identity
from app.utils.resume_ai_adapter import backend_content_to_ai_request


router = APIRouter(prefix="/interviews", tags=["AI Interviews"])
internal_router = APIRouter(prefix="/internal/interviews", tags=["AI Interviews (worker)"])

# Upper bound on live interviews a single user can have open at once.
MAX_OPEN_SESSIONS = 2


def _state_conflict(s: InterviewSession, message: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={"code": "state_conflict", "message": message, "current_status": s.status},
    )


def _get_owned(db: Session, user: User, session_id: str) -> InterviewSession:
    # 404 (not 403) for someone else's id — no enumeration.
    s = (
        db.query(InterviewSession)
        .filter(
            InterviewSession.id == session_id,
            InterviewSession.user_id == user.id,
            InterviewSession.status != InterviewStatus.deleted,
        )
        .first()
    )
    if not s:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail={"code": "interview_not_found", "message": "Interview not found"})
    reconcile_stale(db, s)
    return s


# --- browser-facing ------------------------------------------------------------

@router.post("", status_code=status.HTTP_201_CREATED)
def create_interview(
    body: InterviewCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(Roles.user, Roles.admin)),
):
    if not livekit_settings.configured:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "interviews_unavailable", "message": "Live interviews are not available right now."},
        )
    # 402 interview_credits_required now, rather than after setup + mic test.
    # Nothing is spent until start.
    require_available_credit(user)

    open_count = (
        db.query(func.count(InterviewSession.id))
        .filter(
            InterviewSession.user_id == user.id,
            InterviewSession.status.in_([InterviewStatus.ready, InterviewStatus.in_progress]),
        )
        .scalar()
        or 0
    )
    if open_count >= MAX_OPEN_SESSIONS:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "too_many_open_interviews",
                "message": "Finish or delete your open interviews before starting another one.",
            },
        )

    resume_title: Optional[str] = None
    snapshot: Optional[dict] = None
    if body.resume_id is not None:
        r = (
            db.query(Resume)
            .filter(
                Resume.user_id == user.id,
                Resume.user_resume_id == body.resume_id,
                Resume.is_deleted == False,  # noqa: E712
            )
            .first()
        )
        if not r:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail={"code": "resume_not_found", "message": "Resume not found"})
        resume_title = r.title
        snapshot = sanitize_resume_snapshot(backend_content_to_ai_request(r.content or {}))

    s = InterviewSession(
        user_id=user.id,
        status=InterviewStatus.ready,
        interview_type=body.interview_type,
        role_title=body.role_title,
        seniority=body.seniority,
        duration_minutes=int(body.duration_minutes),
        resume_id=body.resume_id,
        resume_title=resume_title,
        resume_snapshot=snapshot,
        job_description=body.job_description,
    )
    db.add(s)
    db.commit()
    db.refresh(s)
    s.room_name = interview_room_name(s.id)
    db.commit()
    return serialize_session(s)


@router.get("", response_model=InterviewListResponse)
def list_interviews(
    limit: int = Query(default=20, ge=1, le=50),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    q = db.query(InterviewSession).filter(
        InterviewSession.user_id == user.id,
        InterviewSession.status != InterviewStatus.deleted,
    )
    total = q.count()
    items = q.order_by(InterviewSession.updated_at.desc()).offset(offset).limit(limit).all()
    for s in items:
        reconcile_stale(db, s)
    return {"items": [serialize_session(s) for s in items], "total": total}


@router.get("/{session_id}")
def get_interview(session_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    s = _get_owned(db, user, session_id)
    return serialize_session(s, include_transcript=s.status == InterviewStatus.report_ready)


@router.post("/{session_id}/start", response_model=InterviewStartResponse)
def start_interview(
    session_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(Roles.user, Roles.admin)),
):
    """
    Move ready -> in_progress and hand back a LiveKit join token. Idempotent while
    in progress (a refresh re-issues a token for the same room without spending
    another credit).
    """
    s = _get_owned(db, user, session_id)
    if s.status == InterviewStatus.ready:
        # One credit, spent once, when the interview actually starts (402 if none).
        charge_for_interview(db, user, s)
        s.status = InterviewStatus.in_progress
        s.started_at = now()
        s.room_name = s.room_name or interview_room_name(s.id)
        db.commit()
    elif s.status != InterviewStatus.in_progress:
        raise _state_conflict(s, "This interview can no longer be started.")

    try:
        token = mint_interview_token(session_id=s.id, user_id=user.id, display_name=user.name or "Candidate")
    except RuntimeError as e:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail={"code": "interviews_unavailable", "message": str(e)})

    return {
        "session": serialize_session(s),
        "connection": LiveConnection(
            url=livekit_settings.url,
            token=token,
            room_name=s.room_name,
            participant_identity=participant_identity(user.id),
        ),
    }


@router.post("/{session_id}/complete")
def complete_interview(session_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """
    Candidate left the room from the browser. The worker still posts the
    transcript via /internal/.../finalize; this just moves the UI to "processing"
    immediately (and lets the stale reconciler fail it if the worker never reports).
    """
    s = _get_owned(db, user, session_id)
    if s.status == InterviewStatus.in_progress:
        s.status = InterviewStatus.processing
        s.processing_started_at = now()
        db.commit()
    return serialize_session(s)


@router.post("/{session_id}/retry")
def retry_interview(
    session_id: str,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(Roles.user, Roles.admin)),
):
    # No charge: the interview's credit already covers its report, retries included.
    s = _get_owned(db, user, session_id)
    if s.status != InterviewStatus.failed:
        raise _state_conflict(s, "Only a failed interview can be retried.")
    if not s.transcript:
        raise _state_conflict(s, "There is no saved conversation to retry. Please start a new interview.")
    s.status = InterviewStatus.processing
    s.processing_started_at = now()
    s.error = None
    db.commit()
    background.add_task(generate_report, s.id)
    return serialize_session(s)


@router.delete("/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_interview(session_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    s = _get_owned(db, user, session_id)
    s.status = InterviewStatus.deleted
    s.deleted_at = now()
    # Nothing to retain once the user deletes: drop the conversation too.
    s.transcript = None
    s.resume_snapshot = None
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- worker-facing --------------------------------------------------------------

def _require_agent(x_interview_agent_key: Optional[str] = Header(default=None)) -> None:
    secret = livekit_settings.agent_secret
    if not secret or not x_interview_agent_key or not hmac.compare_digest(secret, x_interview_agent_key):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid agent credentials")


def _get_for_agent(db: Session, session_id: str) -> InterviewSession:
    s = db.get(InterviewSession, session_id)
    if not s or s.status == InterviewStatus.deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Interview not found")
    return s


@internal_router.get("/{session_id}/context", dependencies=[Depends(_require_agent)])
def agent_context(session_id: str, db: Session = Depends(get_db)):
    """The interviewer's briefing. Contact details were already stripped from the snapshot."""
    s = _get_for_agent(db, session_id)
    if s.status not in (InterviewStatus.ready, InterviewStatus.in_progress):
        raise _state_conflict(s, "This interview is not accepting a live session.")
    return {
        "id": s.id,
        "status": s.status,
        "role_title": s.role_title,
        "interview_type": s.interview_type,
        "seniority": s.seniority,
        "duration_minutes": s.duration_minutes,
        "question_target": question_target(s.duration_minutes),
        "candidate_name": (s.user.name or "").split(" ")[0] if s.user and s.user.name else "",
        "resume": s.resume_snapshot,
        "job_description": s.job_description,
        "started_at": s.started_at.isoformat() if s.started_at else None,
    }


@internal_router.post("/{session_id}/finalize", status_code=status.HTTP_202_ACCEPTED, dependencies=[Depends(_require_agent)])
def agent_finalize(session_id: str, body: InterviewFinalize, background: BackgroundTasks, db: Session = Depends(get_db)):
    s = _get_for_agent(db, session_id)
    transcript = [t.model_dump() for t in body.transcript]
    if apply_finalize(db, s, transcript, body.ended_reason):
        background.add_task(generate_report, s.id)
    return {"id": s.id, "status": s.status}
