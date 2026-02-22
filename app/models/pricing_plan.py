from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database.connection import Base


class PricingPlan(Base):
    __tablename__ = "pricing_plans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), unique=True, index=True, nullable=False)
    label: Mapped[str | None] = mapped_column(String(50), nullable=True)  # e.g. "Head Start", "Most Popular"
    price: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    credits: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)
    features: Mapped[list | None] = mapped_column(JSONB, nullable=True)  # ["50 AI Credits", "All Templates", ...]
    is_popular: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    display_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
