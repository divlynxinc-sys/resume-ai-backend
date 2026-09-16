import os
import logging
from collections.abc import Collection

import httpx
from fastapi import HTTPException, status


SITEVERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"
TEST_SECRET_KEYS = {
    "1x0000000000000000000000000000000AA",
    "2x0000000000000000000000000000000AA",
    "3x0000000000000000000000000000000AA",
}
logger = logging.getLogger(__name__)


def _is_production() -> bool:
    environment = (
        os.getenv("APP_ENV")
        or os.getenv("ENVIRONMENT")
        or os.getenv("RAILWAY_ENVIRONMENT_NAME")
        or ""
    ).strip().lower()
    return environment in {"prod", "production"}


def verify_turnstile(
    token: str,
    remote_ip: str | None = None,
    *,
    expected_actions: Collection[str] | None = None,
) -> None:
    secret_key = os.getenv("TURNSTILE_SECRET_KEY", "").strip()
    if not secret_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Security verification is not configured",
        )
    if _is_production() and secret_key in TEST_SECRET_KEYS:
        logger.error("Cloudflare Turnstile test secret is configured in production")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Security verification is not configured correctly",
        )
    if not token or len(token) > 2048:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Please complete the security verification",
        )

    payload = {"secret": secret_key, "response": token}
    if remote_ip:
        payload["remoteip"] = remote_ip

    try:
        response = httpx.post(SITEVERIFY_URL, data=payload, timeout=8.0)
        response.raise_for_status()
        result = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Security verification is temporarily unavailable",
        ) from exc

    if not result.get("success"):
        logger.warning(
            "Turnstile validation failed: error_codes=%s",
            result.get("error-codes", []),
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Security verification failed. Please try again.",
        )

    if _is_production():
        allowed_hostnames = {
            hostname.strip().lower()
            for hostname in os.getenv(
                "TURNSTILE_ALLOWED_HOSTNAMES", "jobsynk.co,www.jobsynk.co"
            ).split(",")
            if hostname.strip()
        }
        hostname = str(result.get("hostname", "")).lower()
        action = str(result.get("action", ""))
        if hostname not in allowed_hostnames or (
            expected_actions is not None and action not in expected_actions
        ):
            logger.warning(
                "Turnstile token context mismatch: hostname=%r action=%r",
                hostname,
                action,
            )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Security verification failed. Please try again.",
            )
