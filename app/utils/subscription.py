"""
Subscription entitlement + money-back policy helpers.

Local DB is the source of truth for *access*; Polar is the source of truth for
*billing*. These helpers read the `users.subscription_*` columns to decide who
has paid access, whether a cancel qualifies for a refund, and whether a canceled
user may re-subscribe for free. See app.core.config.SubscriptionState.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from app.core.config import Roles, SubscriptionState, refund_settings
from app.models.user import User


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: Optional[datetime]) -> Optional[datetime]:
    """Normalize a possibly-naive DB datetime to tz-aware UTC for safe compares."""
    if dt is None:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def has_paid_access(user: User) -> bool:
    """
    True when the user is entitled to paid features right now.

    Admins always pass. Otherwise the subscription must be `active` and, if a
    period end is known, not yet elapsed. `canceled_reserved` / `canceled_refunded`
    / `expired` all read as NO access (the reserved state is a free-reactivate
    offer, not live access).
    """
    if (user.role or Roles.user) == Roles.admin:
        return True
    if not user.plan_id:
        return False
    if user.subscription_state != SubscriptionState.active:
        return False
    period_end = _aware(user.subscription_period_end)
    if period_end is not None and _utcnow() >= period_end:
        return False
    return True


def refund_eligible_now(user: User, plan_slug: Optional[str]) -> bool:
    """
    True when canceling *now* would trigger a 100% money-back refund — i.e. the
    user is still within the plan's money-back window, measured from the current
    period start. Unknown start date (legacy subs) => not eligible.
    """
    window_days = refund_settings.window_for(plan_slug)
    if window_days <= 0:
        return False
    started = _aware(user.subscription_started_at)
    if started is None:
        return False
    elapsed_days = (_utcnow() - started).total_seconds() / 86400.0
    return elapsed_days <= window_days


def can_reactivate_free(user: User) -> bool:
    """
    True when a canceled user may re-subscribe for free: they cancelled past the
    money-back window (reserved) and the original period hasn't ended yet.
    """
    if user.subscription_state != SubscriptionState.canceled_reserved:
        return False
    period_end = _aware(user.subscription_period_end)
    if period_end is None:
        return False
    return _utcnow() < period_end
