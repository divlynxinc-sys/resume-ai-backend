import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy.orm import Session

from app.core.config import polar_settings, SubscriptionState
from app.database.connection import get_db
from app.models.pricing_plan import PricingPlan
from app.models.user import User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks", tags=["Webhooks"])


# Credits granted on subscription activation. Subscription plans are unlimited
# in the product copy, so we top users up with a high number; the webhook
# refills on each renewal cycle (subscription.active fires per renewal).
SUBSCRIPTION_CREDIT_TOPUP = 999_999


def _get_metadata(event_data) -> dict:
    """Pull metadata off either a checkout, subscription, or order payload."""
    md = getattr(event_data, "metadata", None)
    if isinstance(md, dict):
        return md
    return {}


def _resolve_user_and_plan(db: Session, event_data) -> tuple[Optional[User], Optional[PricingPlan]]:
    md = _get_metadata(event_data)

    user: Optional[User] = None
    user_id_raw = md.get("user_id") or getattr(event_data, "external_customer_id", None)
    if user_id_raw:
        try:
            user = db.query(User).filter(User.id == int(user_id_raw)).first()
        except (TypeError, ValueError):
            user = None

    plan: Optional[PricingPlan] = None
    plan_slug = md.get("plan_slug")
    plan_id_raw = md.get("plan_id")
    if plan_slug:
        plan = db.query(PricingPlan).filter(PricingPlan.slug == plan_slug).first()
    elif plan_id_raw:
        try:
            plan = db.query(PricingPlan).filter(PricingPlan.id == int(plan_id_raw)).first()
        except (TypeError, ValueError):
            plan = None

    return user, plan


@router.post("/polar")
async def polar_webhook(request: Request, db: Session = Depends(get_db)):
    """
    Receive and verify Polar webhooks (Standard Webhooks spec).

    Configure the endpoint in Polar dashboard -> Settings -> Webhooks and
    subscribe to: subscription.active, subscription.canceled, order.paid.
    Set POLAR_WEBHOOK_SECRET to the secret shown when creating the endpoint.
    """
    if not polar_settings.webhook_secret:
        logger.warning("POLAR_WEBHOOK_SECRET not set; rejecting webhook")
        return Response(status_code=status.HTTP_503_SERVICE_UNAVAILABLE)

    body = await request.body()

    try:
        from polar_sdk.webhooks import WebhookVerificationError, validate_event

        event = validate_event(
            body=body,
            headers=dict(request.headers),
            secret=polar_settings.webhook_secret,
        )
    except WebhookVerificationError:
        return Response(status_code=status.HTTP_403_FORBIDDEN)
    except ImportError:
        logger.error("polar-sdk not installed; cannot verify webhook")
        return Response(status_code=status.HTTP_503_SERVICE_UNAVAILABLE)

    event_type = getattr(event, "type", "")
    data = getattr(event, "data", None)
    if data is None:
        return Response(status_code=status.HTTP_202_ACCEPTED)

    user, plan = _resolve_user_and_plan(db, data)

    # Activation events: subscription is paid & active
    if event_type in ("subscription.active", "subscription.created", "order.paid"):
        if user and plan:
            user.plan_id = plan.id
            user.credits_remaining = SUBSCRIPTION_CREDIT_TOPUP
            user.subscription_state = SubscriptionState.active

            # Subscription id + period window (present on subscription.* events;
            # order.paid carries subscription_id). Refund window is measured from
            # subscription_started_at, expiry/reservation from subscription_period_end.
            sub_id = getattr(data, "subscription_id", None) or getattr(data, "id", None)
            if sub_id:
                user.polar_subscription_id = str(sub_id)
            period_start = getattr(data, "current_period_start", None)
            period_end = getattr(data, "current_period_end", None)
            if period_start is not None:
                user.subscription_started_at = period_start
            elif user.subscription_started_at is None:
                user.subscription_started_at = datetime.now(timezone.utc)
            if period_end is not None:
                user.subscription_period_end = period_end

            # order.paid carries the charge we may later refund 100%.
            if event_type == "order.paid":
                order_id = getattr(data, "id", None)
                order_amount = getattr(data, "total_amount", None)
                if order_id:
                    user.polar_order_id = str(order_id)
                if isinstance(order_amount, int):
                    user.polar_order_amount = order_amount

            db.add(user)
            db.commit()
            logger.info("Polar %s: activated plan %s for user %s", event_type, plan.slug, user.id)
        else:
            logger.warning("Polar %s: missing user/plan in metadata", event_type)

    # Actual revocation (immediate cancel or period end reached): drop paid access.
    # NOTE: we intentionally do NOT act on `subscription.canceled` (which fires when
    # cancel-at-period-end is merely *scheduled*) — local state already reflects that,
    # and clearing here would wrongly kill access early.
    elif event_type == "subscription.revoked":
        if user:
            user.plan_id = None
            user.credits_remaining = 0
            # Preserve an explicit refunded marker; otherwise the period simply ended.
            if user.subscription_state != SubscriptionState.canceled_refunded:
                user.subscription_state = SubscriptionState.expired
            user.subscription_period_end = None
            db.add(user)
            db.commit()
            logger.info("Polar %s: cleared plan for user %s", event_type, user.id)

    return Response(status_code=status.HTTP_202_ACCEPTED)
