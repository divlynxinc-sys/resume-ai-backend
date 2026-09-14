"""Shared job-description contracts, used by every "paste or add a link" field in the app."""

from __future__ import annotations

from pydantic import BaseModel, Field

MAX_JOB_DESCRIPTION_CHARS = 8000


class JobDescriptionUrlRequest(BaseModel):
    url: str = Field(min_length=1, max_length=2000)


class JobDescriptionUrlResponse(BaseModel):
    job_description: str
    source_url: str
    truncated: bool
