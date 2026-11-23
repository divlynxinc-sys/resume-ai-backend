from datetime import datetime, timezone

import pytest

from app.utils import email_utils


@pytest.mark.parametrize(
    ("send", "value"),
    [
        (email_utils.send_password_reset_email, "https://example.com/reset-password?token=sample"),
        (email_utils.send_otp_email, "123456"),
    ],
)
def test_email_renders_footer_and_action(monkeypatch, send, value):
    def capture(_recipient, _subject, body):
        assert "{{YEAR}}" not in body
        assert f"&copy; {datetime.now(timezone.utc).year} " in body
        assert value in body
        return True

    monkeypatch.setattr(email_utils, "_send_via_resend", capture)
    send("test@example.com", value)
