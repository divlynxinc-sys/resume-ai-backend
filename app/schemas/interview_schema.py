"""Pydantic contracts for the AI Interviews router (app/routers/interviews.py)."""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator

from app.schemas.job_description_schema import MAX_JOB_DESCRIPTION_CHARS

InterviewType = Literal["general", "behavioural", "technical", "hr_screening", "leadership"]
Seniority = Literal["entry", "mid", "senior", "lead"]
DurationMinutes = Literal[10, 15, 20]


class InterviewCreate(BaseModel):
    interview_type: InterviewType
    role_title: str = Field(min_length=2, max_length=200)
    seniority: Seniority
    duration_minutes: DurationMinutes
    # Per-user résumé id (the `id` the frontend already shows), optional.
    resume_id: Optional[int] = None
    job_description: Optional[str] = Field(default=None, max_length=MAX_JOB_DESCRIPTION_CHARS)

    @field_validator("role_title")
    @classmethod
    def _strip_role(cls, v: str) -> str:
        v = " ".join(v.split())
        if len(v) < 2:
            raise ValueError("Enter the role you want to practise for.")
        return v

    @field_validator("job_description")
    @classmethod
    def _strip_jd(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        v = v.strip()
        return v or None


class LiveConnection(BaseModel):
    """What the browser needs to join the LiveKit room."""

    url: str
    token: str
    room_name: str
    participant_identity: str
    # Seconds after which the frontend should treat a still-absent agent as a failure.
    agent_join_timeout_seconds: int = 25


class InterviewStartResponse(BaseModel):
    session: Dict[str, Any]
    connection: LiveConnection


class InterviewListResponse(BaseModel):
    items: List[Dict[str, Any]]
    total: int


# --- internal (worker -> backend) ---------------------------------------------

class TranscriptTurn(BaseModel):
    role: Literal["assistant", "user"]
    text: str = Field(max_length=20000)
    # Seconds since the interview started, as measured by the worker.
    at: Optional[float] = None


class InterviewFinalize(BaseModel):
    transcript: List[TranscriptTurn] = Field(default_factory=list, max_length=600)
    ended_reason: Literal["completed", "time_limit", "candidate_left", "error"] = "completed"
    # Short, non-sensitive worker diagnostics (never shown to the user verbatim).
    note: Optional[str] = Field(default=None, max_length=500)
