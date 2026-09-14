from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship
from datetime import datetime, timezone

from app.database.connection import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    google_sub: Mapped[str | None] = mapped_column(String(255), unique=True, nullable=True)
    role: Mapped[str] = mapped_column(String(50), default="user", nullable=False)
    token_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    credits_remaining: Mapped[int] = mapped_column(Integer, default=150, nullable=False)
    # Profile fields (synced from resume info)
    phone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    location: Mapped[str | None] = mapped_column(String(255), nullable=True)
    linkedin_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    portfolio_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    plan_id: Mapped[int | None] = mapped_column(ForeignKey("pricing_plans.id", ondelete="SET NULL"), nullable=True)
    # Subscription lifecycle (see app.core.config.SubscriptionState). Local source of
    # truth for paid-feature entitlement; Polar stays source of truth for billing.
    subscription_state: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # Polar identifiers needed to refund / cancel / reactivate.
    polar_subscription_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    polar_order_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Latest order total in the currency's minor unit (cents), for a 100% refund.
    polar_order_amount: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Start of the current paid period — the money-back window is measured from here.
    subscription_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Original period end — the reservation boundary for free re-subscribe / expiry.
    subscription_period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Prepaid AI Interview credits (1 = one live interview + its report). Unlike
    # credits_remaining this IS load-bearing: only app.utils.interview_credits may
    # change it, and always with a matching interview_credit_transactions row.
    interview_credits: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # OTP login fields
    otp_code: Mapped[str | None] = mapped_column(String(6), nullable=True)
    otp_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc), nullable=False)

    settings = relationship("UserSettings", back_populates="user", uselist=False, lazy="joined")
