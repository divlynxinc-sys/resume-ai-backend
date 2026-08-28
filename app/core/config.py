import os
from dataclasses import dataclass, field
from typing import Dict

from app.core.env import load_env

load_env()


@dataclass(frozen=True)
class JwtSettings:
    secret_key: str = os.getenv("JWT_SECRET_KEY", "CHANGE_ME_IN_PRODUCTION")
    algorithm: str = os.getenv("JWT_ALGORITHM", "HS256")
    access_expire_minutes: int = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "60"))
    refresh_expire_minutes: int = int(os.getenv("REFRESH_TOKEN_EXPIRE_MINUTES", "43200"))  # 30 days


@dataclass(frozen=True)
class PolarSettings:
    access_token: str = os.getenv("POLAR_ACCESS_TOKEN", "")
    webhook_secret: str = os.getenv("POLAR_WEBHOOK_SECRET", "")
    # "sandbox" while testing, "production" for live
    server: str = os.getenv("POLAR_SERVER", "sandbox")
    # Frontend URL Polar redirects to after a successful checkout. Used as a
    # fallback when the checkout request's Origin isn't in `allowed_success_origins`.
    success_url: str = os.getenv(
        "POLAR_SUCCESS_URL",
        "http://localhost:5173/success?checkout_id={CHECKOUT_ID}",
    )
    # Origins we're willing to redirect back to after checkout. The checkout route
    # prefers the request's own Origin (so a checkout started on localhost returns
    # to localhost, prod returns to prod) but only if it's in this allowlist —
    # otherwise it falls back to `success_url`. Prevents open-redirect abuse.
    allowed_success_origins: frozenset = field(
        default_factory=lambda: frozenset(
            o.strip().rstrip("/")
            for o in os.getenv(
                "POLAR_ALLOWED_ORIGINS",
                "http://localhost:5173,http://127.0.0.1:5173,"
                "https://jobsynk.co,https://www.jobsynk.co,"
                "https://resume-ai-frontend-beta.vercel.app",
            ).split(",")
            if o.strip()
        )
    )
    # Optional Polar discount UUID applied to every checkout (e.g. the 50%-off
    # launch offer). Leave empty to charge full price. Create the discount in the
    # Polar dashboard, then set POLAR_DISCOUNT_ID to its id.
    discount_id: str = os.getenv("POLAR_DISCOUNT_ID", "")
    # Map plan slug -> Polar product UUID. Set one env var per plan.
    product_ids: Dict[str, str] = field(
        default_factory=lambda: {
            "weekly": os.getenv("POLAR_PRODUCT_WEEKLY", ""),
            "monthly": os.getenv("POLAR_PRODUCT_MONTHLY", ""),
            "three_months": os.getenv("POLAR_PRODUCT_THREE_MONTHS", ""),
        }
    )


class Roles:
    guest = "guest"
    user = "user"
    admin = "admin"


class SubscriptionState:
    """
    Local entitlement state for a user's subscription (source of truth for access;
    Polar remains source of truth for billing/refunds). Stored on users.subscription_state.

    - active:             paying, full paid-feature access.
    - canceled_reserved:  canceled past the money-back window — paid features BLOCKED
                          now, but the user may re-subscribe for free until
                          subscription_period_end (then it expires).
    - canceled_refunded:  canceled within the money-back window — refunded and revoked
                          immediately, blocked, NO free re-subscribe.
    - expired:            period ended / revoked by Polar; must repurchase.
    """

    active = "active"
    canceled_reserved = "canceled_reserved"
    canceled_refunded = "canceled_refunded"
    expired = "expired"


class UsageFeature:
    """Keys for the hidden per-user weekly AI usage caps (see app.utils.usage_limits)."""

    resume_ai = "resume_ai"
    cover_letter = "cover_letter"
    qa_answers = "qa_answers"
    hr_email = "hr_email"
    # Live voice mock interviews (LiveKit). Counted once per interview *start*.
    ai_interviews = "ai_interviews"


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class UsageLimitSettings:
    """
    Hidden anti-abuse quota: how many AI generations a user may run per rolling
    window, per feature. Never shown to users — only enforced. All values are
    env-overridable so the caps can be tuned without a deploy.

    Effective cap = base weekly limit (per feature) * plan multiplier.
      e.g. monthly plan resume_ai => 20 * 2 = 40 / week.
    Plans not listed (incl. free / no plan / 'weekly') use a multiplier of 1.
    """

    window_days: int = _int_env("USAGE_WINDOW_DAYS", 7)

    # Base weekly cap per feature.
    base_limits: Dict[str, int] = field(
        default_factory=lambda: {
            UsageFeature.resume_ai: _int_env("USAGE_LIMIT_RESUME_AI", 20),
            UsageFeature.cover_letter: _int_env("USAGE_LIMIT_COVER_LETTER", 20),
            UsageFeature.qa_answers: _int_env("USAGE_LIMIT_QA_ANSWERS", 20),
            UsageFeature.hr_email: _int_env("USAGE_LIMIT_HR_EMAIL", 20),
            # Much lower base: one interview is ~15 min of STT + LLM + TTS.
            UsageFeature.ai_interviews: _int_env("USAGE_LIMIT_AI_INTERVIEWS", 10),
        }
    )

    # Multiplier by plan slug; longer plans get more headroom.
    plan_multipliers: Dict[str, int] = field(
        default_factory=lambda: {
            "weekly": _int_env("USAGE_MULT_WEEKLY", 1),
            "monthly": _int_env("USAGE_MULT_MONTHLY", 2),
            "three_months": _int_env("USAGE_MULT_THREE_MONTHS", 3),
        }
    )


@dataclass(frozen=True)
class RefundSettings:
    """
    Money-back / cancellation policy, per plan.

    When a user cancels a paid subscription within `refund_window_days[plan_slug]`
    of the current period starting, they get a 100% automatic refund and are
    revoked immediately. Cancel after that window: no refund, paid features are
    blocked immediately, but the user may re-subscribe for free until the original
    period end (a "reservation") — the subscription still expires on its original
    date and never renews.

    The 3-month plan's 7-day window is the "7-day money-back free trial" advertised
    on the pricing card; weekly/monthly use a 1-day window. All env-overridable.
    """

    refund_window_days: Dict[str, int] = field(
        default_factory=lambda: {
            "weekly": _int_env("REFUND_WINDOW_WEEKLY", 1),
            "monthly": _int_env("REFUND_WINDOW_MONTHLY", 1),
            "three_months": _int_env("REFUND_WINDOW_THREE_MONTHS", 7),
        }
    )

    def window_for(self, plan_slug: str | None) -> int:
        """Refund/money-back window in days for a plan (0 = no money-back)."""
        if not plan_slug:
            return 0
        return self.refund_window_days.get(plan_slug, 0)


@dataclass(frozen=True)
class LiveKitSettings:
    """
    LiveKit Cloud project used for AI Interviews (live voice). The backend only
    MINTS ROOM TOKENS with these credentials; the interviewer itself is the
    separate `resumeai-AI/interview_agent` worker, which connects to the same
    project and is dispatched by `agent_name` when a candidate joins the room.

    `agent_secret` is a shared secret the worker sends as `X-Interview-Agent-Key`
    to the `/internal/interviews/...` endpoints (fetch context, post transcript).
    Leave it empty to disable those endpoints entirely.
    """

    url: str = os.getenv("LIVEKIT_URL", "")
    api_key: str = os.getenv("LIVEKIT_API_KEY", "")
    api_secret: str = os.getenv("LIVEKIT_API_SECRET", "")
    agent_name: str = os.getenv("INTERVIEW_AGENT_NAME", "jobsynk-interviewer")
    agent_secret: str = os.getenv("INTERVIEW_AGENT_SECRET", "")
    # A join token only needs to outlive the interview itself.
    token_ttl_minutes: int = _int_env("INTERVIEW_TOKEN_TTL_MINUTES", 45)

    @property
    def configured(self) -> bool:
        return bool(self.url and self.api_key and self.api_secret)


jwt_settings = JwtSettings()
livekit_settings = LiveKitSettings()
polar_settings = PolarSettings()
usage_limit_settings = UsageLimitSettings()
refund_settings = RefundSettings()

