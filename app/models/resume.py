from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.connection import Base


class Resume(Base):
    __tablename__ = "resumes"
    __table_args__ = (
        UniqueConstraint("user_id", "user_resume_id", name="uq_resumes_user_localid"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    # Per-user visible/local id starting from 1 and increasing per user
    user_resume_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    template_id: Mapped[int | None] = mapped_column(ForeignKey("templates.id", ondelete="SET NULL"), nullable=True)
    status: Mapped[str] = mapped_column(String(50), default="draft", nullable=False)
    content: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)

    template = relationship("Template")

