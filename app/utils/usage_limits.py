"""
Hidden per-user weekly AI usage caps (anti-abuse).

These caps stop a user from running hundreds of AI generations a day and burning
the LLM budget. They are intentionally invisible to the user: we never expose a
"token balance". When a cap is hit we return a structured HTTP 429 the frontend
turns into a friendly "try again in a few days" message.

This is entirely separate from `users.credits_remaining` (a display-only balance
set on plan purchase). Nothing here touches that field.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import HTTPException, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.config import Roles, usage_limit_settings
from app.models.ai_usage import AIUsageEvent
from app.models.pricing_plan import PricingPlan
from app.models.user import User


def _plan_slug_for(db: Session, user: User) -> Optional[str]:
    """Resolve the user's plan slug, or None if they have no plan."""
    if not user.plan_id:
        return None
    return db.query(PricingPlan.slug).filter(PricingPlan.id == user.plan_id).scalar()


def weekly_limit(feature: str, plan_slug: Optional[str]) -> Optional[int]:
    """
    Effective cap for (feature, plan). Returns None when the feature is uncapped
    (base limit <= 0), which disables enforcement for it.
    """
    base = usage_limit_settings.base_limits.get(feature)
    if not base or base <= 0:
        return None
    multiplier = usage_limit_settings.plan_multipliers.get(plan_slug or "", 1)
    return base * max(multiplier, 1)


def enforce_usage_limit(db: Session, user: User, feature: str) -> None:
    """
    Check the user's rolling-window usage for `feature` and, if under the cap,
    record one usage event. Raises HTTP 429 (structured detail) when the cap is
    reached. Admins bypass entirely.

    Call this AFTER cheap input validation but BEFORE invoking the AI service, so
    malformed requests don't consume a slot. Recording up front (rather than after
    the AI responds) is deliberate: it's the standard rate-limit behaviour and
    keeps streaming and non-streaming endpoints consistent.
    """
    if (user.role or Roles.user) == Roles.admin:
        return

    plan_slug = _plan_slug_for(db, user)
    limit = weekly_limit(feature, plan_slug)
    if limit is None:
        return  # feature uncapped

    now = datetime.now(timezone.utc)
    window_start = now - timedelta(days=usage_limit_settings.window_days)

    used = (
        db.query(func.count(AIUsageEvent.id))
        .filter(
            AIUsageEvent.user_id == user.id,
            AIUsageEvent.feature == feature,
            AIUsageEvent.created_at >= window_start,
        )
        .scalar()
        or 0
    )

    if used >= limit:
        # A slot frees up when the oldest in-window event rolls off the window.
        oldest = (
            db.query(func.min(AIUsageEvent.created_at))
            .filter(
                AIUsageEvent.user_id == user.id,
                AIUsageEvent.feature == feature,
                AIUsageEvent.created_at >= window_start,
            )
            .scalar()
        )
        # Normalize to tz-aware UTC so the ISO string always carries an offset —
        # otherwise a naive value (e.g. from a DB column without tz) would be read
        # as local time by the frontend's `new Date(resets_at)`.
        if oldest is not None and oldest.tzinfo is None:
            oldest = oldest.replace(tzinfo=timezone.utc)
        resets_at = (oldest or now) + timedelta(days=usage_limit_settings.window_days)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "code": "usage_limit_reached",
                "message": (
                    "You've reached your weekly limit for this feature. "
                    "Please try again in a few days."
                ),
                "feature": feature,
                "resets_at": resets_at.isoformat(),
            },
        )

    db.add(AIUsageEvent(user_id=user.id, feature=feature, created_at=now))
    db.commit()
