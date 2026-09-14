"""
AI Interviews — one row per live voice mock interview.

The interview itself happens in a LiveKit room between the candidate's browser
and the `interview_agent` worker (see resumeai-AI/interview_agent). This table is
the durable record: setup, the résumé/JD snapshot the interviewer was given, the
transcript the worker posts back when the room ends, and the report the AI
service builds from that transcript.

Audio is never stored anywhere — only the text transcript.

Status machine (server-authoritative):
    ready -> in_progress -> processing -> report_ready
                        \\-> abandoned (ended with no usable answers)
                        \\-> failed    (report generation failed; retryable while
                                       a transcript exists)
    any   -> deleted (soft delete, hidden from lists)
"""

from datetime import datetime, timezone
import uuid

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.connection import Base


class InterviewStatus:
    ready = "ready"
    in_progress = "in_progress"
    processing = "processing"
    report_ready = "report_ready"
    abandoned = "abandoned"
    failed = "failed"
    deleted = "deleted"


class InterviewCreditStatus:
    """What happened to the interview credit this session consumed (NULL = none: not started yet, or an admin)."""

    charged = "charged"
    # Returned automatically because the interview never ran on our side (see
    # app.utils.interview_credits.refund_interview_credit).
    refunded = "refunded"


def _now() -> datetime:
    return datetime.now(timezone.utc)


class InterviewSession(Base):
    __tablename__ = "interview_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default=InterviewStatus.ready, index=True)

    # Setup (validated on create; enums live in app.schemas.interview_schema)
    interview_type: Mapped[str] = mapped_column(String(32), nullable=False)
    role_title: Mapped[str] = mapped_column(String(200), nullable=False)
    seniority: Mapped[str] = mapped_column(String(32), nullable=False)
    duration_minutes: Mapped[int] = mapped_column(Integer, nullable=False)

    # Per-user résumé id (resumes.user_resume_id) the candidate picked, plus a
    # snapshot of its content at creation time so later edits don't change a
    # running interview. Snapshot shape = resume_ai_adapter.backend_content_to_ai_request
    # minus contact fields.
    resume_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    resume_title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    resume_snapshot: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    job_description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # LiveKit room the interview ran in (`interview-<id>`).
    room_name: Mapped[str | None] = mapped_column(String(120), nullable=True)

    # Filled by the worker at the end of the room: [{role, text, at}], plus why it ended.
    transcript: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    ended_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)

    # Filled by report generation (AI service /interview/report).
    questions: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    answers: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    report: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    # InterviewCreditStatus. Set on the first start; only ever moved by conditional
    # UPDATEs in app.utils.interview_credits so a credit is charged/refunded once.
    credit_status: Mapped[str | None] = mapped_column(String(16), nullable=True)

    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    processing_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now, nullable=False)

    user = relationship("User")

    __table_args__ = (
        # History list: WHERE user_id=? AND status != 'deleted' ORDER BY updated_at DESC
        Index("ix_interview_sessions_user_updated", "user_id", "updated_at"),
    )
