"""
Q&A answers generation router.

Streams the AI service response straight through to the client.
"""

from __future__ import annotations

import json
from typing import Generator, List, Optional

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


router = APIRouter(prefix="/qa-answers", tags=["Q&A Answers"])


class QAAnswersRequest(BaseModel):
    resume_id: Optional[int] = None
    resume_text: Optional[str] = None
    job_description: str
    tone: Optional[str] = "professional"
    company: Optional[str] = None
    role: Optional[str] = None
    interview_type: Optional[str] = "screening"
    focus: Optional[str] = None
    question_count: Optional[int] = 10
    questions: Optional[List[str]] = None


def _stream_from_ai(payload: dict) -> Generator[bytes, None, None]:
    url = f"{get_ai_base_url()}/generate_qa_answers"
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
        "interview_type": (body.interview_type or "screening").lower(),
        "question_count": int(body.question_count or 10),
    }
    if body.company:
        payload["company"] = body.company
    if body.role:
        payload["role"] = body.role
    if body.focus:
        payload["focus"] = body.focus
    if body.questions:
        payload["questions"] = body.questions

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
