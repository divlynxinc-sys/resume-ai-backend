import os
import smtplib
from email.message import EmailMessage
from pathlib import Path
from typing import Optional


def _load_otp_template() -> str:
    """
    Load the HTML template for OTP emails.

    The template file is expected at: app/email_templates/otp_login.html
    """
    base_dir = Path(__file__).resolve().parent.parent
    template_path = base_dir / "email_templates" / "otp_login.html"
    try:
        return template_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        # Fallback minimal HTML if template is missing
        return "<html><body><p>Your login code is: <strong>{{OTP_CODE}}</strong></p></body></html>"


def send_otp_email(to_email: str, otp_code: str, *, subject: Optional[str] = None) -> None:
    """
    Send a 6-digit OTP email using SMTP settings from environment variables.

    Required env vars:
    - SMTP_HOST
    - SMTP_PORT
    - SMTP_USERNAME
    - SMTP_PASSWORD
    - SMTP_FROM_EMAIL
    - SMTP_USE_TLS (optional, default: "true")
    """
    smtp_host = os.getenv("SMTP_HOST")
    smtp_port = int(os.getenv("SMTP_PORT", "587"))
    smtp_user = os.getenv("SMTP_USERNAME")
    smtp_password = os.getenv("SMTP_PASSWORD")
    from_email = os.getenv("SMTP_FROM_EMAIL")
    use_tls = os.getenv("SMTP_USE_TLS", "true").lower() == "true"

    if not smtp_host or not smtp_user or not smtp_password or not from_email:
        # In dev environments without SMTP configured, just no-op.
        # You can log the OTP to the console for debugging if desired.
        print(f"[DEV] OTP for {to_email}: {otp_code}")
        return

    html_template = _load_otp_template()
    html_body = html_template.replace("{{OTP_CODE}}", otp_code)

    msg = EmailMessage()
    msg["Subject"] = subject or "Your ResumeAI login code"
    msg["From"] = from_email
    msg["To"] = to_email
    msg.set_content(f"Your login code is: {otp_code}")
    msg.add_alternative(html_body, subtype="html")

    with smtplib.SMTP(host=smtp_host, port=smtp_port) as server:
        if use_tls:
            server.starttls()
        server.login(smtp_user, smtp_password)
        server.send_message(msg)

