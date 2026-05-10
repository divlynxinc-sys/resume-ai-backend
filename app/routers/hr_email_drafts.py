"""
HR email drafts generation router.

Streams the AI service response straight through to the client.
"""

from __future__ import annotations

import json
from typing import Generator, Optional

import urllib.error
import urllib.request

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.security import get_current_user
from app.database.connection import get_db
from app.models.resume import Resume
from app.models.user import User
from app.utils.ai_client import get_ai_base_url
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
    drafts: Optional[int] = 3


def _stream_from_ai(payload: dict) -> Generator[bytes, None, None]:
    url = f"{get_ai_base_url()}/generate_hr_email_drafts"
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        resp = urllib.request.urlopen(req)
    except urllib.error.HTTPError as e:
        try:
            body = e.read().decode("utf-8", errors="replace")
        except Exception:
            body = ""
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"AI service error {e.code}: {body or e.reason}",
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"AI service unreachable: {e}",
        )

    try:
        while True:
            chunk = resp.read(256)
            if not chunk:
                break
            yield chunk
    finally:
        try:
            resp.close()
        except Exception:
            pass


@router.post("/generate")
def generate_hr_email_drafts(
    body: HREmailDraftsRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    payload: dict = {
        "tone": (body.tone or "professional").lower(),
        "email_type": (body.email_type or "application").lower(),
        "drafts": int(body.drafts or 3),
    }

    if body.job_description and body.job_description.strip():
        payload["job_description"] = body.job_description.strip()
    if body.company:
        payload["company"] = body.company
    if body.role:
        payload["role"] = body.role
    if body.recipient_name:
        payload["recipient_name"] = body.recipient_name
    if body.job_link:
        payload["job_link"] = body.job_link
    if body.date_applied:
        payload["date_applied"] = body.date_applied
    if body.availability:
        payload["availability"] = body.availability
    if body.extra_context:
        payload["extra_context"] = body.extra_context

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
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Provide either resume_id or resume_text",
        )

    return StreamingResponse(
        _stream_from_ai(payload),
        media_type="text/plain; charset=utf-8",
        headers={"X-Accel-Buffering": "no"},
    )
