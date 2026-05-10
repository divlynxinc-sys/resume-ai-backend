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
    # Frontend URL Polar redirects to after a successful checkout
    success_url: str = os.getenv(
        "POLAR_SUCCESS_URL",
        "http://localhost:5173/success?checkout_id={CHECKOUT_ID}",
    )
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


jwt_settings = JwtSettings()
polar_settings = PolarSettings()

