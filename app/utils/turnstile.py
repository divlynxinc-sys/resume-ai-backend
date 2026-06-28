import os

import httpx
from fastapi import HTTPException, status


SITEVERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"


def verify_turnstile(token: str, remote_ip: str | None = None) -> None:
    secret_key = os.getenv("TURNSTILE_SECRET_KEY", "").strip()
    if not secret_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Security verification is not configured",
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
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Security verification failed. Please try again.",
        )
