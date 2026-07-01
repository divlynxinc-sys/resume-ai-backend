"""
Q&A Answers generation router.

Streams the AI service's `/generate_qa_answers` response straight through to the
client so users see tokens as they're produced. Mirrors the cover-letter router.
"""

from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.config import UsageFeature
from app.core.security import get_current_user
from app.database.connection import get_db
from app.models.resume import Resume
from app.models.user import User
from app.utils.ai_client import stream_from_ai_service
from app.utils.resume_ai_adapter import backend_content_to_ai_request
from app.utils.usage_limits import enforce_usage_limit


router = APIRouter(prefix="/qa-answers", tags=["Q&A Answers"])


class QAAnswersRequest(BaseModel):
    resume_id: Optional[int] = None
    resume_text: Optional[str] = None
    job_description: str
    tone: Optional[str] = "professional"
    company: Optional[str] = None
    role: Optional[str] = None
    interview_type: Optional[str] = "behavioral"
    focus: Optional[str] = None
    question_count: Optional[int] = 6
    questions: Optional[List[str]] = None


@router.post("/generate")
def generate_qa_answers(
    body: QAAnswersRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if not (body.job_description or "").strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="job_description is required",
        )

    payload: dict = {
        "job_description": body.job_description.strip(),
        "tone": (body.tone or "professional").lower(),
        "interview_type": (body.interview_type or "behavioral").lower(),
        "question_count": body.question_count or 6,
    }
    if body.company:
        payload["company"] = body.company
    if body.role:
        payload["role"] = body.role
    if body.focus:
        payload["focus"] = body.focus
    if body.questions:
        payload["questions"] = [q for q in body.questions if q and q.strip()]

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
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Resume not found")
        ai_request = backend_content_to_ai_request(r.content or {})
        ai_request.setdefault("job_description", "")
        payload["resume"] = ai_request
    elif body.resume_text and body.resume_text.strip():
        payload["resume_text"] = body.resume_text.strip()
    # resume is optional for Q&A — the model can answer from the job description alone.

    # Hidden weekly anti-abuse cap (raises 429 when exceeded). Admins bypass.
    enforce_usage_limit(db, user, UsageFeature.qa_answers)

    # Prime the first chunk inside the handler so a connection error surfaces as a
    # clean 502 *before* the streaming response (and 200 status) has started.
    gen = stream_from_ai_service("/generate_qa_answers", payload)
    try:
        first = next(gen)
    except StopIteration:
        first = b""
    except RuntimeError as e:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(e))

    def body_stream():
        if first:
            yield first
        yield from gen

    return StreamingResponse(
        body_stream(),
        media_type="text/plain; charset=utf-8",
        headers={"X-Accel-Buffering": "no"},
    )
