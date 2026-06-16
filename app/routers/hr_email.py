"""
HR Email Drafts generation router.

Streams the AI service's `/generate_hr_email` response straight through to the
client so users see tokens as they're produced. Mirrors the cover-letter router.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.security import get_current_user
from app.database.connection import get_db
from app.models.resume import Resume
from app.models.user import User
from app.utils.ai_client import stream_from_ai_service
from app.utils.resume_ai_adapter import backend_content_to_ai_request


router = APIRouter(prefix="/hr-email-drafts", tags=["HR Email Drafts"])


class HREmailDraftsRequest(BaseModel):
    resume_id: Optional[int] = None
    resume_text: Optional[str] = None
    job_description: Optional[str] = None
    tone: Optional[str] = "professional"
    company: Optional[str] = None
    role: Optional[str] = None
    email_type: Optional[str] = "application"
    recipient_name: Optional[str] = None
    job_link: Optional[str] = None
    date_applied: Optional[str] = None
    availability: Optional[str] = None
    extra_context: Optional[str] = None
    drafts: Optional[int] = 2


@router.post("/generate")
def generate_hr_email(
    body: HREmailDraftsRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    payload: dict = {
        "tone": (body.tone or "professional").lower(),
        "email_type": (body.email_type or "application").lower(),
        "drafts": body.drafts or 2,
    }
    for field in ("company", "role", "recipient_name", "job_link", "date_applied", "availability", "extra_context"):
        value = getattr(body, field)
        if value:
            payload[field] = value
    if body.job_description and body.job_description.strip():
        payload["job_description"] = body.job_description.strip()

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
    # resume is optional for HR emails — the model can draft from the context alone.

    # Prime the first chunk so a connection error surfaces as a clean 502 before
    # the streaming response starts.
    gen = stream_from_ai_service("/generate_hr_email", payload)
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
