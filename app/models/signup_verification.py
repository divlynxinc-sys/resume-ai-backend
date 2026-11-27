from datetime import datetime

from sqlalchemy import JSON, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database.connection import Base


class SignupVerification(Base):
    __tablename__ = "signup_verifications"

    email: Mapped[str] = mapped_column(String(255), primary_key=True)
    pending_challenge_id: Mapped[str | None] = mapped_column(String(24))
    challenge_id: Mapped[str | None] = mapped_column(String(24))
    code_hash: Mapped[str | None] = mapped_column(String(64))
    code_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failures: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    send_times: Mapped[list[float]] = mapped_column(JSON, default=list, nullable=False)
    grant_hash: Mapped[str | None] = mapped_column(String(64))
    grant_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_activity: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
