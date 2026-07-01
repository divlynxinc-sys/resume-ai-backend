"""
Cover letter generation router.

Streams the AI service's `/generate_cover_letter` response straight through to the
client so users see tokens as they're produced (perceived speed >> wall-clock speed).
"""

from __future__ import annotations

import json
import os
from typing import Generator, Optional

import urllib.error
import urllib.request

# Per-socket-operation timeout for the streaming AI call (see _stream_from_ai).
AI_STREAM_TIMEOUT_SECONDS = int(os.getenv("AI_STREAM_TIMEOUT_SECONDS", "120"))

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.config import UsageFeature
from app.core.security import get_current_user, require_paid_plan
from app.database.connection import get_db
from app.models.resume import Resume
from app.models.user import User
from app.utils.ai_client import get_ai_base_url
from app.utils.resume_ai_adapter import backend_content_to_ai_request
from app.utils.usage_limits import enforce_usage_limit


router = APIRouter(prefix="/cover-letter", tags=["Cover Letter"])


class CoverLetterRequest(BaseModel):
    resume_id: Optional[int] = None
    resume_text: Optional[str] = None
    job_description: str
    tone: Optional[str] = "professional"
    company: Optional[str] = None
    role: Optional[str] = None


def _stream_from_ai(payload: dict) -> Generator[bytes, None, None]:
    url = f"{get_ai_base_url()}/generate_cover_letter"
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        # Per-socket-operation timeout (not a total deadline): tokens stream
        # continuously once generation starts, so this only trips if the AI service
        # accepts the connection but then stalls (e.g. hung model) — which would
        # otherwise hang the request forever. Generous enough to absorb a cold model load.
        resp = urllib.request.urlopen(req, timeout=AI_STREAM_TIMEOUT_SECONDS)
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
def generate_cover_letter(
    body: CoverLetterRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_paid_plan()),
):
    if not (body.job_description or "").strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="job_description is required",
        )

    payload: dict = {
        "job_description": body.job_description.strip(),
        "tone": (body.tone or "professional").lower(),
    }
    if body.company:
        payload["company"] = body.company
    if body.role:
        payload["role"] = body.role

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
        # The AI service's ResumeRequest model requires job_description, but it's
        # only used for resume optimization — for cover letters we use the top-level
        # `job_description` field. Set it to empty if missing to satisfy validation.
        ai_request.setdefault("job_description", "")
        payload["resume"] = ai_request
    elif body.resume_text and body.resume_text.strip():
        payload["resume_text"] = body.resume_text.strip()
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Provide either resume_id or resume_text",
        )

    # Hidden weekly anti-abuse cap (raises 429 when exceeded). Admins bypass.
    enforce_usage_limit(db, user, UsageFeature.cover_letter)

    return StreamingResponse(
        _stream_from_ai(payload),
        media_type="text/plain; charset=utf-8",
        headers={"X-Accel-Buffering": "no"},
    )
