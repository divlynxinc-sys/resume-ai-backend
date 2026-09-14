"""
AI Interview credits — the only code allowed to change `users.interview_credits`.

Policy (product decision 2026-09-14):
  * 1 credit = 1 live interview, report included. Report retries are free.
  * Credits are prepaid via one-time Polar packs (3 for $10, 30 for $90), open to
    every signed-in user — no subscription needed — and never expire.
  * The credit is charged when an interview first STARTS (ready -> in_progress),
    never on create, so setting up and testing the mic is free.
  * It is refunded automatically when the interview never ran on our side:
    the worker never delivered a transcript, or it reported an error. A candidate
    who leaves without answering has still used the interview.
  * Admins bypass credits entirely (consistent with the paid gate and usage caps).

Every balance change is a conditional UPDATE (so two racing requests can't both
spend the last credit or refund twice) plus a ledger row whose unique
constraints make purchases and per-session charges idempotent.
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import CreditPack, Roles, interview_credit_settings
from app.models.interview import InterviewCreditStatus, InterviewSession
from app.models.interview_credit import CreditTransactionKind, InterviewCreditTransaction
from app.models.user import User

logger = logging.getLogger(__name__)

CREDITS_REQUIRED_CODE = "interview_credits_required"


def is_unlimited(user: User) -> bool:
    return (user.role or Roles.user) == Roles.admin


def credits_required_error() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_402_PAYMENT_REQUIRED,
        detail={
            "code": CREDITS_REQUIRED_CODE,
            "message": "You need an AI interview credit to start an interview.",
        },
    )


def require_available_credit(user: User) -> None:
    """Cheap pre-check for create: the real, race-safe spend happens on start."""
    if not is_unlimited(user) and int(user.interview_credits or 0) < 1:
        raise credits_required_error()


def _balance(db: Session, user_id: int) -> int:
    return int(db.query(User.interview_credits).filter(User.id == user_id).scalar() or 0)


def _add_to_balance(db: Session, user_id: int, delta: int) -> int:
    db.query(User).filter(User.id == user_id).update(
        {User.interview_credits: User.interview_credits + delta},
        synchronize_session=False,
    )
    return _balance(db, user_id)


# --- purchases -------------------------------------------------------------------

def grant_pack(
    db: Session,
    *,
    user_id: int,
    pack: CreditPack,
    polar_order_id: str,
    amount_cents: Optional[int] = None,
) -> bool:
    """
    Credit a paid Polar order. Idempotent per order: returns False (and changes
    nothing) if this order was already granted — by the webhook or by a sync.
    Commits.
    """
    already = (
        db.query(InterviewCreditTransaction.id)
        .filter(
            InterviewCreditTransaction.polar_order_id == polar_order_id,
            InterviewCreditTransaction.kind == CreditTransactionKind.purchase,
        )
        .first()
    )
    if already:
        return False

    txn = InterviewCreditTransaction(
        user_id=user_id,
        kind=CreditTransactionKind.purchase,
        delta=pack.credits,
        pack=pack.key,
        polar_order_id=polar_order_id,
        amount_cents=amount_cents,
    )
    db.add(txn)
    try:
        # Insert BEFORE touching the balance: when the webhook and a sync race,
        # the loser blocks on the unique index here and never adds credits.
        db.flush()
    except IntegrityError:
        db.rollback()
        return False
    txn.balance_after = _add_to_balance(db, user_id, pack.credits)
    db.commit()
    logger.info("interview credits: +%s (%s) for user %s from order %s", pack.credits, pack.key, user_id, polar_order_id)
    return True


def revoke_refunded_order(db: Session, polar_order_id: str) -> int:
    """
    Take back the credits of a fully refunded order. Never drives the balance
    below zero: credits already spent on interviews stay spent. Idempotent.
    Returns how many credits were removed. Commits.
    """
    purchase = (
        db.query(InterviewCreditTransaction)
        .filter(
            InterviewCreditTransaction.polar_order_id == polar_order_id,
            InterviewCreditTransaction.kind == CreditTransactionKind.purchase,
        )
        .first()
    )
    if not purchase:
        return 0

    txn = InterviewCreditTransaction(
        user_id=purchase.user_id,
        kind=CreditTransactionKind.order_refunded,
        delta=0,
        pack=purchase.pack,
        polar_order_id=polar_order_id,
    )
    db.add(txn)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        return 0

    removable = min(purchase.delta, _balance(db, purchase.user_id))
    if removable > 0:
        updated = db.query(User).filter(User.id == purchase.user_id, User.interview_credits >= removable).update(
            {User.interview_credits: User.interview_credits - removable},
            synchronize_session=False,
        )
        if not updated:  # an interview spent a credit in between; leave it for manual review
            removable = 0
    txn.delta = -removable
    txn.balance_after = _balance(db, purchase.user_id)
    if removable < purchase.delta:
        txn.note = f"{purchase.delta - removable} credit(s) were already used"
    db.commit()
    logger.info("interview credits: -%s for user %s, order %s refunded", removable, purchase.user_id, polar_order_id)
    return removable


# --- spending ----------------------------------------------------------------------

def charge_for_interview(db: Session, user: User, session: InterviewSession) -> None:
    """
    Spend one credit on this interview. Does NOT commit — the caller commits it
    together with the ready -> in_progress transition. Raises 402 when the user
    has no credit (after rolling back). A second call for the same session, even
    a concurrent one, is a no-op.
    """
    if is_unlimited(user):
        return
    claimed = (
        db.query(InterviewSession)
        .filter(InterviewSession.id == session.id, InterviewSession.credit_status.is_(None))
        .update({InterviewSession.credit_status: InterviewCreditStatus.charged}, synchronize_session=False)
    )
    if not claimed:
        return
    debited = (
        db.query(User)
        .filter(User.id == user.id, User.interview_credits >= 1)
        .update({User.interview_credits: User.interview_credits - 1}, synchronize_session=False)
    )
    if not debited:
        db.rollback()
        raise credits_required_error()
    db.add(
        InterviewCreditTransaction(
            user_id=user.id,
            kind=CreditTransactionKind.interview,
            delta=-1,
            balance_after=_balance(db, user.id),
            interview_session_id=session.id,
        )
    )


def refund_interview_credit(db: Session, session: InterviewSession, reason: str) -> bool:
    """
    Return the credit of an interview that never ran on our side. Does NOT
    commit (callers are mid-transition). Idempotent: only a `charged` session
    is refunded, and the conditional UPDATE makes that hold under concurrency.
    """
    if session.credit_status != InterviewCreditStatus.charged:
        return False
    claimed = (
        db.query(InterviewSession)
        .filter(InterviewSession.id == session.id, InterviewSession.credit_status == InterviewCreditStatus.charged)
        .update({InterviewSession.credit_status: InterviewCreditStatus.refunded}, synchronize_session=False)
    )
    if not claimed:
        return False
    session.credit_status = InterviewCreditStatus.refunded
    db.add(
        InterviewCreditTransaction(
            user_id=session.user_id,
            kind=CreditTransactionKind.interview_refund,
            delta=1,
            balance_after=_add_to_balance(db, session.user_id, 1),
            interview_session_id=session.id,
            note=reason[:255],
        )
    )
    logger.info("interview credits: refunded session %s (%s)", session.id, reason)
    return True
