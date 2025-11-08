import os
from dataclasses import dataclass

from app.core.env import load_env

load_env()


@dataclass(frozen=True)
class JwtSettings:
    secret_key: str = os.getenv("JWT_SECRET_KEY", "CHANGE_ME_IN_PRODUCTION")
    algorithm: str = os.getenv("JWT_ALGORITHM", "HS256")
    access_expire_minutes: int = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "60"))
    refresh_expire_minutes: int = int(os.getenv("REFRESH_TOKEN_EXPIRE_MINUTES", "43200"))  # 30 days


class Roles:
    guest = "guest"
    user = "user"
    admin = "admin"


jwt_settings = JwtSettings()

