"""
AI Interviews service layer: serialization, state transitions, report generation.

Kept out of the router so the workflow (finalize -> report -> report_ready) is one
function that both the worker callback and the user-facing retry endpoint call.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.database.connection import SessionLocal
from app.models.interview import InterviewSession, InterviewStatus
from app.utils.ai_client import get_ai_base_url, post_json

logger = logging.getLogger(__name__)

# Frontend + AI service use the same weights (src/features/ai-interviews/utils.ts).
SCORE_WEIGHTS: Dict[str, float] = {
    "relevance": 0.30,
    "evidence": 0.25,
    "structure": 0.15,
    "role_alignment": 0.20,
    "communication": 0.10,
}
SCORE_KEYS = tuple(SCORE_WEIGHTS.keys())

# How long a room may sit in `processing` without the worker posting a transcript
# before we give up on it (the worker crashed, or the agent never joined).
PROCESSING_STALE_AFTER = timedelta(minutes=4)
# An in_progress interview that outlives its own duration by this much was
# abandoned mid-room (tab closed, network died) without either side reporting.
IN_PROGRESS_GRACE = timedelta(minutes=15)

# Below this many candidate turns there is nothing worth scoring.
MIN_ANSWER_TURNS = 1
MIN_ANSWER_CHARS = 40

STRIP_FROM_SNAPSHOT = ("email", "phone", "linkedin", "portfolio", "job_description")


def now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: Optional[datetime]) -> Optional[datetime]:
    """
    Normalize a stored timestamp to tz-aware UTC before arithmetic.

    `DateTime(timezone=True)` does not guarantee an aware value on the way back
    (it depends on the driver/backend), and subtracting a naive from an aware
    datetime raises. Same guard as `usage_limits.enforce_usage_limit`.
    """
    if dt is None:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def question_target(duration_minutes: int) -> int:
    """Main questions the interviewer aims for (follow-ups are extra)."""
    return {10: 3, 15: 4, 20: 5}.get(int(duration_minutes), 4)


def sanitize_resume_snapshot(ai_request: Dict[str, Any]) -> Dict[str, Any]:
    """Drop contact details — the interviewer never needs them and they'd only leak into prompts/logs."""
    return {k: v for k, v in ai_request.items() if k not in STRIP_FROM_SNAPSHOT}


def clamp_score(value: Any) -> int:
    try:
        return max(0, min(100, int(round(float(value)))))
    except (TypeError, ValueError):
        return 0


def weighted_overall(scores: Dict[str, Any]) -> int:
    return int(round(sum(clamp_score(scores.get(k, 0)) * w for k, w in SCORE_WEIGHTS.items())))


def _iso(dt: Optional[datetime]) -> Optional[str]:
    dt = _aware(dt)
    return dt.isoformat() if dt else None


def serialize_session(s: InterviewSession, *, include_transcript: bool = False) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "id": s.id,
        "status": s.status,
        "interview_type": s.interview_type,
        "role_title": s.role_title,
        "seniority": s.seniority,
        "duration_minutes": s.duration_minutes,
        "resume_id": s.resume_id,
        "resume_title": s.resume_title,
        "job_description": s.job_description,
        "question_target": question_target(s.duration_minutes),
        "questions": s.questions or [],
        "answers": s.answers or [],
        "report": s.report,
        "error": s.error,
        "ended_reason": s.ended_reason,
        "started_at": _iso(s.started_at),
        "processing_started_at": _iso(s.processing_started_at),
        "completed_at": _iso(s.completed_at),
        "created_at": _iso(s.created_at),
        "updated_at": _iso(s.updated_at),
    }
    if include_transcript:
        out["transcript"] = s.transcript or []
    return out


def reconcile_stale(db: Session, s: InterviewSession) -> None:
    """
    Self-heal sessions whose worker never reported back. Called on every read so
    the frontend's refresh/poll always lands on a terminal, actionable state.
    """
    current = now()
    if s.status == InterviewStatus.processing and s.transcript is None:
        started = _aware(s.processing_started_at or s.updated_at)
        if started and current - started > PROCESSING_STALE_AFTER:
            s.status = InterviewStatus.failed
            s.error = "The interviewer disconnected before the conversation was saved. Please start a new interview."
            s.completed_at = current
            db.commit()
    elif s.status == InterviewStatus.in_progress:
        started = _aware(s.started_at)
        if started and current - started > timedelta(minutes=s.duration_minutes) + IN_PROGRESS_GRACE:
            s.status = InterviewStatus.abandoned
            s.ended_reason = s.ended_reason or "candidate_left"
            s.completed_at = current
            db.commit()


def usable_answer_turns(transcript: List[Dict[str, Any]]) -> int:
    return sum(
        1
        for t in transcript
        if t.get("role") == "user" and len((t.get("text") or "").strip()) >= MIN_ANSWER_CHARS
    )


def apply_finalize(db: Session, s: InterviewSession, transcript: List[Dict[str, Any]], ended_reason: str) -> bool:
    """
    Store the worker's transcript and decide the next state. Returns True when a
    report should be generated. Idempotent: a second finalize for a session that
    already has a transcript is ignored.
    """
    if s.status in (InterviewStatus.report_ready, InterviewStatus.deleted):
        return False
    if s.transcript is not None and s.status != InterviewStatus.failed:
        return False

    s.transcript = transcript
    s.ended_reason = ended_reason
    if usable_answer_turns(transcript) < MIN_ANSWER_TURNS:
        s.status = InterviewStatus.abandoned
        s.completed_at = now()
        db.commit()
        return False

    s.status = InterviewStatus.processing
    s.processing_started_at = now()
    s.error = None
    db.commit()
    return True


def _validate_report_payload(data: Dict[str, Any]) -> Dict[str, Any]:
    """Coerce the AI service response into the exact shape the frontend renders; never trust it blindly."""
    if not isinstance(data, dict):
        raise ValueError("AI service returned a non-object report")

    def str_list(v: Any, limit: int = 8, max_len: int = 400) -> List[str]:
        if not isinstance(v, list):
            return []
        return [str(x).strip()[:max_len] for x in v if str(x).strip()][:limit]

    def scores_of(v: Any) -> Dict[str, int]:
        v = v if isinstance(v, dict) else {}
        return {k: clamp_score(v.get(k, 0)) for k in SCORE_KEYS}

    questions_out: List[Dict[str, Any]] = []
    for i, q in enumerate(data.get("questions") or []):
        if not isinstance(q, dict):
            continue
        prompt = str(q.get("prompt") or "").strip()
        if not prompt:
            continue
        questions_out.append(
            {
                "id": str(q.get("id") or f"q{i + 1}"),
                "prompt": prompt[:600],
                "category": str(q.get("category") or "interview")[:60],
                "is_follow_up": bool(q.get("is_follow_up", False)),
            }
        )
    known_ids = {q["id"] for q in questions_out}

    answers_out: List[Dict[str, Any]] = []
    for i, a in enumerate(data.get("answers") or []):
        if not isinstance(a, dict):
            continue
        qid = str(a.get("question_id") or "")
        if qid not in known_ids:
            continue
        ev = a.get("evaluation") if isinstance(a.get("evaluation"), dict) else a
        answers_out.append(
            {
                "id": f"ans{i + 1}",
                "question_id": qid,
                "transcript": str(a.get("transcript") or "").strip()[:6000],
                "evaluation": {
                    "scores": scores_of(ev.get("scores")),
                    "evidence": str(ev.get("evidence") or "").strip()[:800],
                    "worked": str_list(ev.get("worked")),
                    "improvements": str_list(ev.get("improvements")),
                    "improved_outline": str_list(ev.get("improved_outline"), limit=6),
                },
            }
        )
    if not answers_out:
        raise ValueError("AI service returned no scored answers")

    report_in = data.get("report") if isinstance(data.get("report"), dict) else {}
    scores = report_in.get("scores")
    if not isinstance(scores, dict) or not scores:
        # Fall back to an equal average of the per-answer scores.
        scores = {
            k: round(sum(a["evaluation"]["scores"][k] for a in answers_out) / len(answers_out))
            for k in SCORE_KEYS
        }
    scores = scores_of(scores)
    report_out = {
        "overall_score": weighted_overall(scores),
        "scores": scores,
        "summary": str(report_in.get("summary") or "").strip()[:600],
        "strengths": str_list(report_in.get("strengths")),
        "improvements": str_list(report_in.get("improvements")),
        "action_plan": str_list(report_in.get("action_plan")),
        "generated_at": now().isoformat(),
        "schema_version": "1.0",
        "evaluation_version": str(data.get("evaluation_version") or "interview-eval-v1")[:40],
    }
    return {"questions": questions_out, "answers": answers_out, "report": report_out}


def build_report_payload(s: InterviewSession) -> Dict[str, Any]:
    return {
        "role_title": s.role_title,
        "interview_type": s.interview_type,
        "seniority": s.seniority,
        "duration_minutes": s.duration_minutes,
        "resume": s.resume_snapshot,
        "job_description": s.job_description,
        "transcript": [
            {"role": t.get("role"), "text": t.get("text")}
            for t in (s.transcript or [])
            if t.get("role") in ("assistant", "user") and (t.get("text") or "").strip()
        ],
    }


def generate_report(session_id: str) -> None:
    """
    Background job: transcript -> AI service /interview/report -> report_ready.
    Opens its own DB session because it runs after the request has returned.
    """
    db = SessionLocal()
    try:
        s = db.get(InterviewSession, session_id)
        if not s or s.status != InterviewStatus.processing or not s.transcript:
            return
        try:
            raw = post_json(f"{get_ai_base_url()}/interview/report", build_report_payload(s))
            result = _validate_report_payload(raw)
        except Exception as e:  # noqa: BLE001 — any failure must land in a retryable state
            logger.warning("interview %s report generation failed: %s", session_id, e)
            s.status = InterviewStatus.failed
            s.error = "We couldn't build your report this time. Your conversation is saved — try again in a moment."
            db.commit()
            return
        s.questions = result["questions"]
        s.answers = result["answers"]
        s.report = result["report"]
        s.error = None
        s.status = InterviewStatus.report_ready
        s.completed_at = now()
        db.commit()
    finally:
        db.close()
