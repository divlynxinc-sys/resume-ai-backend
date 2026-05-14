import logging
from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.config import Roles, polar_settings
from app.core.security import require_roles
from app.database.connection import get_db
from app.models.pricing_plan import PricingPlan
from app.models.user import User
from app.utils.polar_client import get_polar, get_product_id_for_slug

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/payments", tags=["Payments"])

# Mirrors the webhook handler — keep these in sync.
SUBSCRIPTION_CREDIT_TOPUP = 999_999
ACTIVE_SUBSCRIPTION_STATUSES = {"active", "trialing"}


class PolarCheckoutRequest(BaseModel):
    plan_slug: str = Field(..., description="Slug of the pricing plan, e.g. 'weekly'")


class PolarCheckoutResponse(BaseModel):
    checkout_url: str
    checkout_id: str


class PolarSyncResponse(BaseModel):
    synced: bool = Field(..., description="True if an active subscription was found and applied")
    current_plan: Optional[str] = Field(None, description="Plan name now applied to the user, or None")
    plan_slug: Optional[str] = None
    credits_remaining: int = 0


class PolarSubscriptionDetails(BaseModel):
    """Live subscription state straight from Polar (used by the account settings UI)."""
    has_subscription: bool
    subscription_id: Optional[str] = None
    plan_name: Optional[str] = None
    plan_slug: Optional[str] = None
    status: Optional[str] = None
    current_period_end: Optional[datetime] = None
    cancel_at_period_end: bool = False


class PolarSwitchRequest(BaseModel):
    plan_slug: str = Field(..., description="Slug of the plan to switch to")


class PolarPortalResponse(BaseModel):
    portal_url: str


@router.post("/polar/checkout", response_model=PolarCheckoutResponse)
def create_polar_checkout(
    payload: PolarCheckoutRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(Roles.user, Roles.admin)),
):
    """
    Create a Polar checkout session for the authenticated user.

    Returns a hosted checkout URL the frontend should redirect the browser to.
    The Polar webhook (/webhooks/polar) is the source of truth for activating
    the subscription once payment succeeds.
    """
    plan = (
        db.query(PricingPlan)
        .filter(PricingPlan.slug == payload.plan_slug, PricingPlan.is_active == True)  # noqa: E712
        .first()
    )
    if not plan:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plan not found")

    product_id = get_product_id_for_slug(plan.slug)
    if not product_id:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Polar product not configured for plan '{plan.slug}'",
        )

    try:
        polar = get_polar()
        checkout = polar.checkouts.create(
            request={
                "products": [product_id],
                "success_url": polar_settings.success_url,
                "customer_email": user.email,
                "external_customer_id": str(user.id),
                "metadata": {
                    "user_id": str(user.id),
                    "plan_id": str(plan.id),
                    "plan_slug": plan.slug,
                },
            }
        )
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        )
    except Exception as exc:  # polar SDK errors
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Polar checkout failed: {exc}",
        )

    return PolarCheckoutResponse(checkout_url=checkout.url, checkout_id=checkout.id)


def _slug_for_product_id(product_id: str) -> Optional[str]:
    for slug, pid in polar_settings.product_ids.items():
        if pid and pid == product_id:
            return slug
    return None


def _get_active_subscription(user_id: int) -> Optional[Any]:
    """Return the user's first active Polar subscription, or None.

    Raises HTTPException on SDK / network failures so callers can pass-through.
    """
    try:
        polar = get_polar()
        response = polar.subscriptions.list(
            external_customer_id=str(user_id),
            active=True,
            limit=10,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc))
    except Exception as exc:
        logger.exception("Polar: listing subscriptions failed for user %s", user_id)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Polar request failed: {exc}",
        )

    items = []
    if response and getattr(response, "result", None):
        items = response.result.items or []
    return next(
        (s for s in items if getattr(s, "status", None) in ACTIVE_SUBSCRIPTION_STATUSES),
        None,
    )


@router.post("/polar/sync", response_model=PolarSyncResponse)
def sync_polar_subscription(
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(Roles.user, Roles.admin)),
):
    """
    Pull the user's current subscription state from Polar and reconcile it with the
    local DB. Safe to call from the frontend right after the Polar redirect — works
    even when webhooks can't reach the backend (e.g. local dev without a tunnel).

    Idempotent: re-running keeps the same plan if nothing changed on Polar's side.
    """
    active_sub = _get_active_subscription(user.id)

    if not active_sub:
        # No active subscription on Polar's side — clear local plan if previously set.
        if user.plan_id is not None:
            user.plan_id = None
            user.credits_remaining = 0
            db.add(user)
            db.commit()
            logger.info("Polar sync: cleared plan for user %s (no active sub)", user.id)
        return PolarSyncResponse(
            synced=False,
            current_plan=None,
            plan_slug=None,
            credits_remaining=int(user.credits_remaining or 0),
        )

    product_id = getattr(active_sub, "product_id", None)
    slug = _slug_for_product_id(product_id) if product_id else None
    if not slug:
        logger.warning(
            "Polar sync: active subscription %s has product_id %s with no slug mapping",
            getattr(active_sub, "id", "?"),
            product_id,
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Active subscription found but its product is not mapped to a local plan.",
        )

    plan = db.query(PricingPlan).filter(PricingPlan.slug == slug).first()
    if not plan:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Pricing plan '{slug}' not found in DB",
        )

    user.plan_id = plan.id
    user.credits_remaining = SUBSCRIPTION_CREDIT_TOPUP
    db.add(user)
    db.commit()
    db.refresh(user)
    logger.info("Polar sync: applied plan %s for user %s", plan.slug, user.id)

    return PolarSyncResponse(
        synced=True,
        current_plan=plan.name,
        plan_slug=plan.slug,
        credits_remaining=int(user.credits_remaining or 0),
    )


@router.get("/polar/subscription", response_model=PolarSubscriptionDetails)
def get_polar_subscription(
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(Roles.user, Roles.admin)),
):
    """
    Live subscription state straight from Polar — used by the account settings page
    to render the current plan, renewal date, and whether the user already requested
    cancellation. Does NOT mutate local DB.
    """
    active_sub = _get_active_subscription(user.id)
    if not active_sub:
        return PolarSubscriptionDetails(has_subscription=False)

    product_id = getattr(active_sub, "product_id", None)
    slug = _slug_for_product_id(product_id) if product_id else None
    plan_name = None
    if slug:
        plan = db.query(PricingPlan).filter(PricingPlan.slug == slug).first()
        plan_name = plan.name if plan else None

    return PolarSubscriptionDetails(
        has_subscription=True,
        subscription_id=getattr(active_sub, "id", None),
        plan_name=plan_name,
        plan_slug=slug,
        status=getattr(active_sub, "status", None),
        current_period_end=getattr(active_sub, "current_period_end", None),
        cancel_at_period_end=bool(getattr(active_sub, "cancel_at_period_end", False)),
    )


@router.post("/polar/switch", response_model=PolarSyncResponse)
def switch_polar_plan(
    payload: PolarSwitchRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(Roles.user, Roles.admin)),
):
    """
    Switch the user's existing Polar subscription to a different product (plan),
    with proration handled by Polar (`proration_behavior=invoice` charges/credits
    the difference immediately on the next invoice). No second checkout, no
    double-charge — the same subscription is updated in place.
    """
    active_sub = _get_active_subscription(user.id)
    if not active_sub:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="No active subscription to switch from. Subscribe first.",
        )

    plan = (
        db.query(PricingPlan)
        .filter(PricingPlan.slug == payload.plan_slug, PricingPlan.is_active == True)  # noqa: E712
        .first()
    )
    if not plan:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plan not found")

    new_product_id = get_product_id_for_slug(plan.slug)
    if not new_product_id:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Polar product not configured for plan '{plan.slug}'",
        )

    current_product_id = getattr(active_sub, "product_id", None)
    if current_product_id == new_product_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You're already on this plan.",
        )

    try:
        polar = get_polar()
        polar.subscriptions.update(
            id=active_sub.id,
            subscription_update={
                "product_id": new_product_id,
                "proration_behavior": "invoice",
            },
        )
    except Exception as exc:
        logger.exception("Polar switch failed for user %s -> %s", user.id, plan.slug)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Polar plan switch failed: {exc}",
        )

    user.plan_id = plan.id
    user.credits_remaining = SUBSCRIPTION_CREDIT_TOPUP
    db.add(user)
    db.commit()
    db.refresh(user)
    logger.info("Polar switch: user %s -> plan %s", user.id, plan.slug)

    return PolarSyncResponse(
        synced=True,
        current_plan=plan.name,
        plan_slug=plan.slug,
        credits_remaining=int(user.credits_remaining or 0),
    )


@router.post("/polar/cancel", response_model=PolarSubscriptionDetails)
def cancel_polar_subscription(
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(Roles.user, Roles.admin)),
):
    """
    Cancel the user's subscription at the end of the current billing period.
    The user keeps access until `current_period_end` (the webhook clears the
    plan when Polar fires `subscription.revoked`). We don't touch plan_id here.
    """
    active_sub = _get_active_subscription(user.id)
    if not active_sub:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No active subscription to cancel.",
        )

    if getattr(active_sub, "cancel_at_period_end", False):
        # Already scheduled — return current state, no-op.
        return PolarSubscriptionDetails(
            has_subscription=True,
            subscription_id=getattr(active_sub, "id", None),
            status=getattr(active_sub, "status", None),
            current_period_end=getattr(active_sub, "current_period_end", None),
            cancel_at_period_end=True,
            plan_slug=_slug_for_product_id(getattr(active_sub, "product_id", "")),
        )

    try:
        polar = get_polar()
        updated = polar.subscriptions.update(
            id=active_sub.id,
            subscription_update={"cancel_at_period_end": True},
        )
    except Exception as exc:
        logger.exception("Polar cancel failed for user %s", user.id)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Polar cancel failed: {exc}",
        )

    logger.info("Polar cancel: user %s scheduled cancellation at period end", user.id)
    return PolarSubscriptionDetails(
        has_subscription=True,
        subscription_id=getattr(updated, "id", None),
        plan_slug=_slug_for_product_id(getattr(updated, "product_id", "")),
        status=getattr(updated, "status", None),
        current_period_end=getattr(updated, "current_period_end", None),
        cancel_at_period_end=True,
    )


@router.post("/polar/portal", response_model=PolarPortalResponse)
def create_polar_portal_session(
    user: User = Depends(require_roles(Roles.user, Roles.admin)),
):
    """
    Create a Polar customer-portal session and return the hosted URL. The user is
    redirected to a Polar-hosted page where they can update their payment method,
    view invoices, and manage benefits. Plan switching and cancellation are still
    handled in-app (`/polar/switch`, `/polar/cancel`) for better UX.
    """
    try:
        polar = get_polar()
        session = polar.customer_sessions.create(
            request={"external_customer_id": str(user.id)},
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc))
    except Exception as exc:
        logger.exception("Polar portal session failed for user %s", user.id)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Polar portal session failed: {exc}",
        )

    return PolarPortalResponse(portal_url=session.customer_portal_url)
