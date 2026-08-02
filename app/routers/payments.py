import logging
from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.config import Roles, SubscriptionState, polar_settings, refund_settings
from app.core.security import require_roles
from app.database.connection import get_db
from app.models.pricing_plan import PricingPlan
from app.models.user import User
from app.utils.polar_client import get_polar, get_product_id_for_slug
from app.utils.subscription import can_reactivate_free, has_paid_access, refund_eligible_now

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/payments", tags=["Payments"])

# Mirrors the webhook handler — keep these in sync.
SUBSCRIPTION_CREDIT_TOPUP = 999_999
ACTIVE_SUBSCRIPTION_STATUSES = {"active", "trialing"}


class PolarCheckoutRequest(BaseModel):
    plan_slug: str = Field(..., description="Slug of the pricing plan, e.g. 'weekly'")


def _resolve_success_url(request: Request) -> str:
    """
    Build the post-checkout redirect URL, preferring the origin the checkout was
    started from (so a checkout begun on localhost returns to localhost, prod to
    prod) when it's allowlisted. Falls back to the configured `success_url`.

    This is what closes the dev gap: without it, every checkout redirects to the
    single hard-coded `POLAR_SUCCESS_URL`, so a local tester lands on the deployed
    site's /success page — which syncs the deployed DB, never their local one.
    """
    origin = (request.headers.get("origin") or "").rstrip("/")
    if origin and origin in polar_settings.allowed_success_origins:
        return f"{origin}/success?checkout_id={{CHECKOUT_ID}}"
    return polar_settings.success_url


class PolarCheckoutResponse(BaseModel):
    checkout_url: str
    checkout_id: str


class PolarSyncResponse(BaseModel):
    synced: bool = Field(..., description="True if an active subscription was found and applied")
    current_plan: Optional[str] = Field(None, description="Plan name now applied to the user, or None")
    plan_slug: Optional[str] = None
    credits_remaining: int = 0


class PolarSubscriptionDetails(BaseModel):
    """Subscription state for the account settings UI (local-first, money-back aware)."""
    has_subscription: bool
    subscription_id: Optional[str] = None
    plan_name: Optional[str] = None
    plan_slug: Optional[str] = None
    status: Optional[str] = None
    current_period_end: Optional[datetime] = None
    cancel_at_period_end: bool = False
    # Money-back / reservation extensions:
    state: Optional[str] = None
    # True while canceling now would trigger a 100% refund (within money-back window).
    refund_eligible_now: bool = False
    # Money-back window length (days) for the current plan — for cancel-modal copy.
    refund_window_days: int = 0
    # True when a canceled user may re-subscribe for free (reserved, not yet expired).
    can_reactivate_free: bool = False
    # When the reservation / access ends (original period end).
    reserved_until: Optional[datetime] = None


class PolarCancelResponse(BaseModel):
    refunded: bool
    message: str
    state: str
    reserved_until: Optional[datetime] = None


class PolarReactivateResponse(BaseModel):
    reactivated: bool
    message: str
    current_plan: Optional[str] = None
    plan_slug: Optional[str] = None
    reserved_until: Optional[datetime] = None


class PolarSwitchRequest(BaseModel):
    plan_slug: str = Field(..., description="Slug of the plan to switch to")


class PolarPortalResponse(BaseModel):
    portal_url: str


@router.post("/polar/checkout", response_model=PolarCheckoutResponse)
def create_polar_checkout(
    payload: PolarCheckoutRequest,
    request: Request,
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

    checkout_request: dict = {
        "products": [product_id],
        "success_url": _resolve_success_url(request),
        "customer_email": user.email,
        "external_customer_id": str(user.id),
        "metadata": {
            "user_id": str(user.id),
            "plan_id": str(plan.id),
            "plan_slug": plan.slug,
        },
    }
    # Launch offer etc.: pre-apply a Polar discount to the checkout when configured.
    if polar_settings.discount_id:
        checkout_request["discount_id"] = polar_settings.discount_id

    try:
        polar = get_polar()
        checkout = polar.checkouts.create(request=checkout_request)
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


def _status_str(sub: Any) -> str:
    """Normalize a subscription status to a lowercase string.

    The Polar SDK returns status as an enum (e.g. SubscriptionStatus.ACTIVE), so a
    naive `status in {"active", ...}` comparison silently misses. `.value` on the
    enum (or str() fallback) gives us the plain "active"/"trialing" string.
    """
    raw = getattr(sub, "status", None)
    value = getattr(raw, "value", raw)
    return str(value).lower() if value is not None else ""


def _is_active(sub: Any) -> bool:
    return _status_str(sub) in ACTIVE_SUBSCRIPTION_STATUSES


def _items(response: Any) -> list:
    if response and getattr(response, "result", None):
        return response.result.items or []
    return []


def _get_active_subscription(user_id: int, user_email: Optional[str] = None) -> Optional[Any]:
    """Return the user's first active Polar subscription, or None.

    Looks up by `external_customer_id` first, then falls back to matching the
    Polar customer by email. The email fallback matters because sandbox/Polar data
    can drift from the local DB: a customer may carry a stale `external_id` (or a
    subscription may have `external_customer_id=None`) after the local DB is
    reseeded, so the id-based lookup misses an actually-active subscription.

    Raises HTTPException on SDK / network failures so callers can pass-through.
    """
    try:
        polar = get_polar()
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc))

    # 1) Primary: by external_customer_id (how checkouts are tagged).
    try:
        response = polar.subscriptions.list(
            external_customer_id=str(user_id),
            active=True,
            limit=10,
        )
    except Exception as exc:
        logger.exception("Polar: listing subscriptions failed for user %s", user_id)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Polar request failed: {exc}",
        )

    active = next((s for s in _items(response) if _is_active(s)), None)
    if active:
        return active

    # 2) Fallback: resolve the Polar customer by the authenticated user's own email,
    #    then check that customer's subscriptions. Safe — the user owns this email.
    if user_email:
        try:
            customers = _items(polar.customers.list(email=user_email, limit=10))
            for cust in customers:
                cust_id = getattr(cust, "id", None)
                if not cust_id:
                    continue
                sub = next(
                    (s for s in _items(polar.subscriptions.list(customer_id=cust_id, limit=20))
                     if _is_active(s)),
                    None,
                )
                if sub:
                    logger.info(
                        "Polar: matched active sub %s for user %s via email fallback "
                        "(customer %s, external_id %s)",
                        getattr(sub, "id", "?"), user_id, cust_id,
                        getattr(cust, "external_id", None),
                    )
                    return sub
        except Exception:
            # Email fallback is best-effort — don't fail the whole request on it.
            logger.exception("Polar: email fallback lookup failed for user %s", user_id)

    return None


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
    active_sub = _get_active_subscription(user.id, user.email)

    if not active_sub:
        # No active subscription on Polar's side. Don't stomp a local reservation:
        # a canceled_reserved user still has a free-reactivate window we track locally.
        if user.plan_id is not None and user.subscription_state != SubscriptionState.canceled_reserved:
            user.plan_id = None
            user.credits_remaining = 0
            user.subscription_state = SubscriptionState.expired
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
    # Only (re)activate from sync when the sub isn't locally reserved/refunded — sync
    # must not silently undo a cancellation the user just made.
    if user.subscription_state in (None, SubscriptionState.active, SubscriptionState.expired):
        user.subscription_state = SubscriptionState.active
        sub_id = getattr(active_sub, "id", None)
        if sub_id:
            user.polar_subscription_id = str(sub_id)
        period_start = getattr(active_sub, "current_period_start", None)
        period_end = getattr(active_sub, "current_period_end", None)
        if period_start is not None:
            user.subscription_started_at = period_start
        if period_end is not None:
            user.subscription_period_end = period_end
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
    Subscription state for the account settings page — local-first, so it reflects
    the money-back / reservation states Polar doesn't know about (blocked-but-reserved,
    refunded). Renders plan, period end, cancellation state, whether canceling now
    refunds, and whether a canceled user can re-subscribe for free.
    """
    # No subscription history at all.
    if not user.plan_id and user.subscription_state in (None, SubscriptionState.expired):
        return PolarSubscriptionDetails(has_subscription=False, state=user.subscription_state)

    plan = db.query(PricingPlan).filter(PricingPlan.id == user.plan_id).first() if user.plan_id else None
    slug = plan.slug if plan else None
    state = user.subscription_state
    reserved = state == SubscriptionState.canceled_reserved

    return PolarSubscriptionDetails(
        has_subscription=has_paid_access(user) or reserved,
        subscription_id=user.polar_subscription_id,
        plan_name=plan.name if plan else None,
        plan_slug=slug,
        status=state,
        current_period_end=user.subscription_period_end,
        # "Cancellation scheduled" in the UI keys off this — true once reserved.
        cancel_at_period_end=reserved,
        state=state,
        refund_eligible_now=(state == SubscriptionState.active and refund_eligible_now(user, slug)),
        refund_window_days=refund_settings.window_for(slug),
        can_reactivate_free=can_reactivate_free(user),
        reserved_until=user.subscription_period_end if reserved else None,
    )


@router.post("/polar/reactivate", response_model=PolarReactivateResponse)
def reactivate_polar_subscription(
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(Roles.user, Roles.admin)),
):
    """
    Free re-subscribe within the reserved window: a user who canceled past the
    money-back window may restore paid access for free until the original period
    end (no charge, no new checkout). The subscription still expires on its original
    date because Polar keeps cancel-at-period-end set.
    """
    if not can_reactivate_free(user):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "must_repurchase",
                "message": "Your reservation has ended. Please choose a plan to subscribe again.",
            },
        )

    user.subscription_state = SubscriptionState.active
    user.credits_remaining = SUBSCRIPTION_CREDIT_TOPUP
    db.add(user)
    db.commit()

    plan = db.query(PricingPlan).filter(PricingPlan.id == user.plan_id).first() if user.plan_id else None
    logger.info("Polar reactivate (free): user %s restored plan until period end", user.id)
    return PolarReactivateResponse(
        reactivated=True,
        message="Your plan is active again until the end of your current period.",
        current_plan=plan.name if plan else None,
        plan_slug=plan.slug if plan else None,
        reserved_until=user.subscription_period_end,
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
    active_sub = _get_active_subscription(user.id, user.email)
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
    user.subscription_state = SubscriptionState.active
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


@router.post("/polar/cancel", response_model=PolarCancelResponse)
def cancel_polar_subscription(
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(Roles.user, Roles.admin)),
):
    """
    Cancel with money-back policy:

    - Within the plan's money-back window (weekly/monthly: 1 day, 3-month: 7 days):
      issue a 100% refund via Polar and revoke the subscription immediately.
    - After the window: no refund. Paid features are blocked immediately, but the
      subscription is *reserved* — the user can re-subscribe for free (see
      /polar/reactivate) until the original period end, and it expires on that date
      (Polar keeps cancel-at-period-end set so it never renews).
    """
    if user.subscription_state != SubscriptionState.active or not user.plan_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No active subscription to cancel.",
        )

    plan = db.query(PricingPlan).filter(PricingPlan.id == user.plan_id).first()
    slug = plan.slug if plan else None
    sub_id = user.polar_subscription_id
    if not sub_id:
        # Fall back to Polar lookup if we never captured the id (legacy subs).
        active_sub = _get_active_subscription(user.id, user.email)
        sub_id = getattr(active_sub, "id", None) if active_sub else None
    if not sub_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No active subscription to cancel.",
        )

    polar = get_polar()
    within_window = refund_eligible_now(user, slug)

    # `polar_order_amount` is an int in minor units, so 0 is a *legitimate* value
    # (a 100%-discount order) and must not be read as "missing" — hence explicit
    # None checks instead of a truthiness test.
    has_order = user.polar_order_id is not None and user.polar_order_amount is not None
    manual_refund_needed = False

    if within_window and not has_order:
        # Refund-eligible, but we can't identify the charge, so we cannot refund
        # automatically. Do NOT revoke here: that would take access away *and*
        # keep the money, with no way back (canceled_refunded also disqualifies
        # free reactivate). Fall through to the reserve path instead — billing
        # stops, the user can reactivate for free — and flag it for follow-up.
        logger.error(
            "Polar cancel: user %s is inside the money-back window but has no usable "
            "order on file (order_id=%r amount=%r) — reserving instead of revoking; "
            "issue this refund manually in the Polar dashboard.",
            user.id,
            user.polar_order_id,
            user.polar_order_amount,
        )
        within_window = False
        manual_refund_needed = True

    if within_window:
        # 100% refund of the latest order, then revoke access immediately.
        try:
            # A zero-amount order (fully discounted) has nothing to refund — skip
            # the call, but still revoke: the user is owed no money.
            if user.polar_order_amount:
                polar.refunds.create(
                    request={
                        "order_id": user.polar_order_id,
                        "reason": "satisfaction_guarantee",
                        "amount": int(user.polar_order_amount),
                        "revoke_benefits": True,
                        "comment": "Automatic money-back-window refund on cancellation.",
                    }
                )
            polar.subscriptions.revoke(id=sub_id)
        except Exception as exc:
            logger.exception("Polar refund/revoke failed for user %s", user.id)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Cancellation with refund failed: {exc}",
            )

        user.subscription_state = SubscriptionState.canceled_refunded
        user.plan_id = None
        user.credits_remaining = 0
        user.subscription_period_end = None
        db.add(user)
        db.commit()
        logger.info("Polar cancel: user %s refunded 100%% and revoked (within window)", user.id)
        return PolarCancelResponse(
            refunded=True,
            state=SubscriptionState.canceled_refunded,
            message="Your plan was cancelled and you've been refunded in full.",
        )

    # Past the window: no refund. Stop renewal in Polar, block access now, reserve
    # the free-reactivate window until the original period end.
    try:
        polar.subscriptions.update(
            id=sub_id,
            subscription_update={"cancel_at_period_end": True},
        )
    except Exception as exc:
        logger.exception("Polar cancel failed for user %s", user.id)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Polar cancel failed: {exc}",
        )

    user.subscription_state = SubscriptionState.canceled_reserved
    user.credits_remaining = 0
    db.add(user)
    db.commit()
    logger.info("Polar cancel: user %s blocked now, reserved until period end (no refund)", user.id)
    return PolarCancelResponse(
        refunded=False,
        state=SubscriptionState.canceled_reserved,
        reserved_until=user.subscription_period_end,
        message=(
            "Your plan is cancelled and you won't be billed again. We couldn't "
            "process your refund automatically — please contact support and we'll "
            "sort it out. You can re-subscribe for free until your current period ends."
            if manual_refund_needed
            else "Your plan is cancelled and paid features are now locked. You can "
            "re-subscribe for free anytime until your current period ends."
        ),
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
