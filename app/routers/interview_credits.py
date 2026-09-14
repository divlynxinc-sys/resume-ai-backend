"""
AI Interview credits — balance, one-time Polar checkout, and post-checkout sync.

Open to every signed-in user (credits do not need a subscription). The webhook
(`order.paid`, see routers/webhooks.py) is the primary grant path; `/sync` is the
self-heal for when it can't reach us (local dev) or lags behind the redirect.
Both paths go through `grant_pack`, which is idempotent per Polar order.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.config import Roles, interview_credit_settings
from app.core.security import require_roles
from app.database.connection import get_db
from app.models.interview_credit import InterviewCreditTransaction
from app.models.user import User
from app.routers.payments import _items, _resolve_success_url
from app.utils.interview_credits import grant_pack, is_unlimited
from app.utils.polar_client import get_polar

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/interview-credits", tags=["AI Interview Credits"])

PackKey = Literal["interview_3", "interview_30"]
PURCHASE_MARKER = "interview_credits"


class CreditPackOut(BaseModel):
    key: str
    credits: int
    price_cents: int
    available: bool = Field(..., description="False when its Polar product isn't configured yet")


class CreditTransactionOut(BaseModel):
    kind: str
    delta: int
    balance_after: Optional[int] = None
    pack: Optional[str] = None
    created_at: datetime


class CreditBalanceOut(BaseModel):
    balance: int
    # Admins aren't charged for interviews; the UI hides purchase prompts for them.
    unlimited: bool = False
    packs: List[CreditPackOut]
    recent: List[CreditTransactionOut]


class CreditCheckoutIn(BaseModel):
    pack: PackKey


class CreditCheckoutOut(BaseModel):
    checkout_url: str
    checkout_id: str


class CreditSyncIn(BaseModel):
    checkout_id: Optional[str] = Field(default=None, max_length=255)


class CreditSyncOut(BaseModel):
    balance: int
    granted_credits: int = Field(0, description="Credits added by THIS call (0 if the webhook got there first)")
    # Only meaningful when checkout_id was sent: that checkout's order is paid and credited.
    checkout_confirmed: bool = False
    checkout_credits: int = 0


def _packs() -> List[CreditPackOut]:
    return [
        CreditPackOut(key=p.key, credits=p.credits, price_cents=p.price_cents, available=bool(p.product_id))
        for p in interview_credit_settings.packs.values()
    ]


def _balance_of(db: Session, user: User) -> int:
    return int(db.query(User.interview_credits).filter(User.id == user.id).scalar() or 0)


@router.get("", response_model=CreditBalanceOut)
def get_interview_credits(
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(Roles.user, Roles.admin)),
):
    recent = (
        db.query(InterviewCreditTransaction)
        .filter(InterviewCreditTransaction.user_id == user.id)
        .order_by(InterviewCreditTransaction.created_at.desc(), InterviewCreditTransaction.id.desc())
        .limit(20)
        .all()
    )
    return CreditBalanceOut(
        balance=_balance_of(db, user),
        unlimited=is_unlimited(user),
        packs=_packs(),
        recent=[
            CreditTransactionOut(
                kind=t.kind, delta=t.delta, balance_after=t.balance_after, pack=t.pack, created_at=t.created_at
            )
            for t in recent
        ],
    )


@router.post("/checkout", response_model=CreditCheckoutOut)
def create_interview_credits_checkout(
    payload: CreditCheckoutIn,
    request: Request,
    user: User = Depends(require_roles(Roles.user, Roles.admin)),
):
    """
    Hosted Polar checkout for one credit pack. Deliberately does NOT apply
    POLAR_DISCOUNT_ID, and disables typed discount codes: the launch offer is for
    subscription plans, and packs are already priced (30 for $90 is the bulk
    discount). Flip `allow_discount_codes` if you ever run a credit promo.
    """
    pack = interview_credit_settings.packs[payload.pack]
    if not pack.product_id:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Polar product not configured for credit pack '{pack.key}'",
        )

    checkout_request: dict = {
        "products": [pack.product_id],
        "success_url": _resolve_success_url(request, f"purchase={PURCHASE_MARKER}"),
        "customer_email": user.email,
        "external_customer_id": str(user.id),
        "allow_discount_codes": False,
        "metadata": {
            "user_id": str(user.id),
            "purchase": PURCHASE_MARKER,
            "pack": pack.key,
        },
    }
    try:
        checkout = get_polar().checkouts.create(request=checkout_request)
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc))
    except Exception as exc:  # polar SDK errors
        logger.exception("Polar credit checkout failed for user %s (%s)", user.id, pack.key)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Polar checkout failed: {exc}")

    return CreditCheckoutOut(checkout_url=checkout.url, checkout_id=checkout.id)


def _status_of(order: Any) -> str:
    raw = getattr(order, "status", None)
    value = getattr(raw, "value", raw)
    return str(value).lower() if value is not None else ""


def _is_paid(order: Any) -> bool:
    # Only "paid": a refunded order must not be (re)granted by a later sync.
    return _status_of(order) == "paid"


@router.post("/sync", response_model=CreditSyncOut)
def sync_interview_credits(
    payload: CreditSyncIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(Roles.user, Roles.admin)),
):
    """
    Pull this user's one-time Polar orders and credit any paid credit-pack order
    not yet granted. Idempotent. The /success page polls it right after checkout,
    because the order may still be settling when Polar redirects back.
    """
    try:
        polar = get_polar()
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc))

    try:
        # Newest first by default; 100 one-time orders is far beyond any real customer.
        orders = _items(
            polar.orders.list(
                external_customer_id=str(user.id),
                product_billing_type="one_time",
                limit=100,
            )
        )
    except Exception as exc:
        logger.exception("Polar: listing orders failed for user %s", user.id)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Polar request failed: {exc}")

    granted = 0
    checkout_confirmed = False
    checkout_credits = 0
    for order in orders:
        pack = interview_credit_settings.pack_for_product(getattr(order, "product_id", None))
        if not pack or not _is_paid(order):
            continue
        metadata = getattr(order, "metadata", None) or {}
        owner = metadata.get("user_id") if isinstance(metadata, dict) else None
        if owner is not None and str(owner) != str(user.id):
            # external_customer_id drifted onto the wrong Polar customer (e.g. a
            # reseeded DB). The checkout's own metadata is the stronger signal.
            logger.warning("Polar credit sync: order %s belongs to user %s, not %s — skipped", order.id, owner, user.id)
            continue
        if grant_pack(
            db,
            user_id=user.id,
            pack=pack,
            polar_order_id=str(order.id),
            amount_cents=getattr(order, "total_amount", None),
        ):
            granted += pack.credits
        if payload.checkout_id and getattr(order, "checkout_id", None) == payload.checkout_id:
            checkout_confirmed = True
            checkout_credits = pack.credits

    return CreditSyncOut(
        balance=_balance_of(db, user),
        granted_credits=granted,
        checkout_confirmed=checkout_confirmed,
        checkout_credits=checkout_credits,
    )
