import os
import smtplib
from email.message import EmailMessage
from pathlib import Path
from typing import Optional

import resend


def _load_template(template_name: str) -> str:
    base_dir = Path(__file__).resolve().parent.parent
    template_path = base_dir / "email_templates" / template_name
    try:
        return template_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return "<html><body><p>Your code is: <strong>{{OTP_CODE}}</strong></p></body></html>"


def _send_via_resend(to_email: str, subject: str, html_body: str) -> bool:
    api_key = os.getenv("RESEND_API_KEY")
    if not api_key:
        return False
    resend.api_key = api_key
    from_email = os.getenv("RESEND_FROM_EMAIL", "noreply@jobsynk.com")
    resend.Emails.send({
        "from": from_email,
        "to": [to_email],
        "subject": subject,
        "html": html_body,
    })
    return True


def _send_via_smtp(to_email: str, subject: str, html_body: str, plain_text: str) -> bool:
    smtp_host = os.getenv("SMTP_HOST")
    smtp_port = int(os.getenv("SMTP_PORT", "587"))
    smtp_user = os.getenv("SMTP_USERNAME")
    smtp_password = os.getenv("SMTP_PASSWORD")
    from_email = os.getenv("SMTP_FROM_EMAIL")
    use_tls = os.getenv("SMTP_USE_TLS", "true").lower() == "true"

    if not smtp_host or not smtp_user or not smtp_password or not from_email:
        return False

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = from_email
    msg["To"] = to_email
    msg.set_content(plain_text)
    msg.add_alternative(html_body, subtype="html")

    with smtplib.SMTP(host=smtp_host, port=smtp_port) as server:
        if use_tls:
            server.starttls()
        server.login(smtp_user, smtp_password)
        server.send_message(msg)
    return True


def send_otp_email(to_email: str, otp_code: str, *, subject: Optional[str] = None, template: str = "otp_login.html") -> None:
    html_template = _load_template(template)
    html_body = html_template.replace("{{OTP_CODE}}", otp_code)
    final_subject = subject or "Your Jobsynk AI verification code"
    plain_text = f"Your verification code is: {otp_code}"

    # Try Resend first, fall back to SMTP, then dev console
    if _send_via_resend(to_email, final_subject, html_body):
        return
    if _send_via_smtp(to_email, final_subject, html_body, plain_text):
        return

    print(f"[DEV] OTP for {to_email}: {otp_code}")
