"""
AI Interview credits ledger — one row per change to `users.interview_credits`.

The balance lives on `users.interview_credits` (fast to read and to decrement
atomically); this table is the audit trail and the idempotency guard:

  * `(polar_order_id, kind)` is unique, so a Polar order grants its credits
    exactly once even when the webhook and the post-checkout sync race.
  * `(interview_session_id, kind)` is unique, so an interview is charged, and
    refunded, at most once.

Postgres treats NULLs as distinct in a unique constraint, so rows without an
order (interview charges) or without a session (purchases) never collide.
"""

from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database.connection import Base


class CreditTransactionKind:
    purchase = "purchase"  # +N, a paid Polar order for a credit pack
    order_refunded = "order_refunded"  # -N, that order was refunded in Polar
    interview = "interview"  # -1, an interview started
    interview_refund = "interview_refund"  # +1, the interview never ran on our side


def _now() -> datetime:
    return datetime.now(timezone.utc)


class InterviewCreditTransaction(Base):
    __tablename__ = "interview_credit_transactions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    # Signed change to the balance, and the balance right after it.
    delta: Mapped[int] = mapped_column(Integer, nullable=False)
    balance_after: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Purchases: which pack, the Polar order, and what Polar charged (minor units).
    pack: Mapped[str | None] = mapped_column(String(32), nullable=True)
    polar_order_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    amount_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Interview charges/refunds: the session (plain string, no FK — sessions are
    # soft-deleted, and the ledger must outlive any future hard delete).
    interview_session_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    note: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)

    __table_args__ = (
        UniqueConstraint("polar_order_id", "kind", name="uq_interview_credit_order_kind"),
        UniqueConstraint("interview_session_id", "kind", name="uq_interview_credit_session_kind"),
    )
