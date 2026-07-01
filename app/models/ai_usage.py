from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.connection import Base


class AIUsageEvent(Base):
    """
    One row per successful AI generation, used to enforce hidden per-user weekly
    usage caps (anti-abuse). This is intentionally separate from the visible
    `users.credits_remaining` balance — it is never surfaced to the user.
    """

    __tablename__ = "ai_usage_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    # Feature key: resume_ai | cover_letter | qa_answers | hr_email (see UsageFeature).
    feature: Mapped[str] = mapped_column(String(50), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )

    user = relationship("User")

    # Backs the rolling-window count query: WHERE user_id=? AND feature=? AND created_at >= ?
    __table_args__ = (
        Index("ix_ai_usage_user_feature_time", "user_id", "feature", "created_at"),
    )
