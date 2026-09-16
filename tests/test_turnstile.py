from unittest.mock import Mock, patch

import pytest
from fastapi import HTTPException

from app.utils.turnstile import verify_turnstile


def _siteverify_result(**overrides):
    result = {
        "success": True,
        "hostname": "www.jobsynk.co",
        "action": "login",
        "error-codes": [],
    }
    result.update(overrides)
    response = Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = result
    return response


def test_production_rejects_cloudflare_test_secret(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("TURNSTILE_SECRET_KEY", "1x0000000000000000000000000000000AA")

    with pytest.raises(HTTPException) as error:
        verify_turnstile("token", expected_actions={"login"})

    assert error.value.status_code == 503


@pytest.mark.parametrize(
    ("hostname", "action"),
    [("attacker.example", "login"), ("www.jobsynk.co", "signup")],
)
def test_production_rejects_wrong_token_context(monkeypatch, hostname, action):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("TURNSTILE_SECRET_KEY", "real-secret")
    monkeypatch.setenv("TURNSTILE_ALLOWED_HOSTNAMES", "jobsynk.co,www.jobsynk.co")

    with patch("app.utils.turnstile.httpx.post", return_value=_siteverify_result(hostname=hostname, action=action)):
        with pytest.raises(HTTPException) as error:
            verify_turnstile("token", expected_actions={"login"})

    assert error.value.status_code == 400


def test_production_accepts_expected_hostname_and_action(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("TURNSTILE_SECRET_KEY", "real-secret")

    with patch("app.utils.turnstile.httpx.post", return_value=_siteverify_result()):
        verify_turnstile("token", expected_actions={"login"})
