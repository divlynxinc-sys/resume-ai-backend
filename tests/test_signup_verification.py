import hashlib
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.utils import signup_verification as verification


def test_final_failed_attempt_persists_and_invalidates_the_challenge(monkeypatch):
    monkeypatch.setenv("SIGNUP_OTP_HMAC_KEY", "signup-code-test-key-local-only-unique")
    now = datetime(2026, 11, 27, tzinfo=timezone.utc)
    monkeypatch.setattr(verification, "utc_now", lambda: now)
    email = "fixture@example.com"
    challenge = "A" * 24
    row = SimpleNamespace(
        challenge_id=challenge, code_hash=verification.otp_digest(email, challenge, "654321"),
        code_expires_at=now + timedelta(minutes=1), failures=verification.ATTEMPT_LIMIT - 1,
    )
    monkeypatch.setattr(verification, "load_for_update", lambda *_: row)
    commits = []
    db = SimpleNamespace(commit=lambda: commits.append(True))
    with pytest.raises(HTTPException) as failure:
        verification.verify_code(db, email, challenge, "111111")
    assert failure.value.status_code == 429
    assert row.code_hash is None
    assert row.failures == verification.ATTEMPT_LIMIT
    assert commits == [True]


def test_grant_expiry_boundary_and_consumption(monkeypatch):
    now = datetime(2026, 11, 27, tzinfo=timezone.utc)
    monkeypatch.setattr(verification, "utc_now", lambda: now)
    proof = "synthetic-local-test-grant"
    row = SimpleNamespace(grant_hash=hashlib.sha256(proof.encode()).hexdigest(), grant_expires_at=now)
    monkeypatch.setattr(verification, "load_for_update", lambda *_: row)

    with pytest.raises(HTTPException) as expired:
        verification.consume_grant(None, "fixture@example.com", proof)
    assert expired.value.status_code == 400
    row.grant_expires_at += timedelta(seconds=1)
    verification.consume_grant(None, "fixture@example.com", proof)
    assert row.grant_hash is None
    with pytest.raises(HTTPException):
        verification.consume_grant(None, "fixture@example.com", proof)
