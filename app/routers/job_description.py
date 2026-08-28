"""
Job-description-from-a-link — shared by every "paste or add a link" field in
the app: cover letter, recruiter outreach (hr-email), interview answers
(qa-answers), the ATS checker, the résumé builder, and AI Interviews.

Gated by login only (not `require_paid_plan()`): this makes no LLM call, so it
has no per-feature usage cost, and two of its callers (ATS checker, résumé
builder) are free-tier features. Login is still required to prevent anonymous
abuse of the outbound fetch. See `app/utils/job_description_fetch.py` for the
SSRF guard on the actual request.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.core.security import get_current_user
from app.models.user import User
from app.schemas.job_description_schema import (
    MAX_JOB_DESCRIPTION_CHARS,
    JobDescriptionUrlRequest,
    JobDescriptionUrlResponse,
)
from app.utils.job_description_fetch import fetch_job_description_from_url

router = APIRouter(prefix="/job-description", tags=["Job Description"])


@router.post("/from-url", response_model=JobDescriptionUrlResponse)
def job_description_from_url(body: JobDescriptionUrlRequest, user: User = Depends(get_current_user)):
    text, final_url = fetch_job_description_from_url(body.url)
    truncated = len(text) > MAX_JOB_DESCRIPTION_CHARS
    return JobDescriptionUrlResponse(
        job_description=text[:MAX_JOB_DESCRIPTION_CHARS],
        source_url=final_url,
        truncated=truncated,
    )
