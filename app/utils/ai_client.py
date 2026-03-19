"""
Minimal HTTP client to call the external `resumeai-AI` service.

We intentionally use the stdlib (`urllib`) to avoid adding extra dependencies.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any, Dict, Optional


def _get_env(name: str, default: str) -> str:
    # Environment is already loaded by `app.core.env.load_env()` during app startup.
    return os.getenv(name, default)


def post_json(
    url: str,
    payload: Dict[str, Any],
    *,
    timeout_seconds: Optional[int] = None,
    headers: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    timeout_seconds = timeout_seconds or int(_get_env("RESUMEAI_AI_TIMEOUT_SECONDS", "180"))
    headers = headers or {}
    headers = {**headers, "Content-Type": "application/json"}

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
            body = resp.read().decode("utf-8") if resp is not None else ""
            if not body.strip():
                return {}
            return json.loads(body)
    except urllib.error.HTTPError as e:
        # Best-effort decode of the response body for easier debugging.
        try:
            err_body = e.read().decode("utf-8", errors="replace")
        except Exception:
            err_body = ""
        raise RuntimeError(f"AI service HTTPError {e.code}: {err_body or e.reason}")
    except Exception as e:
        raise RuntimeError(f"AI service request failed: {e}")


def get_ai_base_url() -> str:
    return _get_env("RESUMEAI_AI_BASE_URL", "http://127.0.0.1:8000").rstrip("/")

