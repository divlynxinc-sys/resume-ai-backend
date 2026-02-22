from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database.connection import Base


class ResumeATSScore(Base):
    """Stores ATS score data received from AI for a resume."""
    __tablename__ = "resume_ats_scores"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    resume_id: Mapped[int] = mapped_column(ForeignKey("resumes.id", ondelete="CASCADE"), index=True, nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    overall_score: Mapped[int] = mapped_column(Integer, nullable=False)  # e.g. 86
    max_score: Mapped[int] = mapped_column(Integer, default=100, nullable=False)
    category_scores: Mapped[dict | None] = mapped_column(JSONB, nullable=True)  # {keyword_match: {...}, ...}
    recommendations: Mapped[list | None] = mapped_column(JSONB, nullable=True)  # ["Add keyword...", ...]
    analyzed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
