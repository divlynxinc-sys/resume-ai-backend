import hashlib
import hmac
import math
import os
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models.signup_verification import SignupVerification

CODE_MINUTES = 10
GRANT_MINUTES = 10
RESEND_SECONDS = 60
SEND_WINDOW_SECONDS = 3600
SEND_LIMIT = 5
ATTEMPT_LIMIT = 5


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def otp_digest(email: str, challenge: str, code: str) -> str:
    secret = os.getenv("SIGNUP_OTP_HMAC_KEY", "")
    if len(secret.encode()) < 32:
        raise HTTPException(503, detail="Signup verification is temporarily unavailable")
    return hmac.new(secret.encode(), f"{email}\0{challenge}\0{code}".encode(), hashlib.sha256).hexdigest()


def signup_error(status: int, code: str, message: str, now: datetime, retry_at: datetime | None = None):
    detail = {"code": code, "message": message, "server_now": now.isoformat()}
    headers = None
    if retry_at is not None:
        detail["retry_at"] = retry_at.isoformat()
        headers = {"Retry-After": str(max(0, math.ceil((retry_at - now).total_seconds())))}
    return HTTPException(status_code=status, detail=detail, headers=headers)


def load_for_update(db: Session, email: str) -> SignupVerification | None:
    return db.scalar(select(SignupVerification).where(SignupVerification.email == email).with_for_update())


def reserve_send(db: Session, email: str, challenge: str, now: datetime) -> datetime:
    stale = select(SignupVerification.email).where(
        SignupVerification.last_activity < now - timedelta(hours=24)
    ).order_by(SignupVerification.last_activity).limit(50).with_for_update(skip_locked=True)
    db.execute(delete(SignupVerification).where(SignupVerification.email.in_(stale)))
    db.execute(insert(SignupVerification).values(email=email, last_activity=now)
               .on_conflict_do_nothing(index_elements=["email"]))
    row = load_for_update(db, email)
    timestamp = now.timestamp()
    sends = [sent for sent in row.send_times if sent > timestamp - SEND_WINDOW_SECONDS]
    retry_timestamp = sends[-1] + RESEND_SECONDS if sends else timestamp
    if len(sends) >= SEND_LIMIT:
        retry_timestamp = max(retry_timestamp, sends[0] + SEND_WINDOW_SECONDS)
    if retry_timestamp > timestamp:
        retry_at = datetime.fromtimestamp(retry_timestamp, tz=timezone.utc)
        raise signup_error(429, "signup_send_limited", "Please wait before requesting another code.", now, retry_at)

    row.send_times = [*sends, timestamp]
    row.pending_challenge_id = challenge
    row.last_activity = now
    db.commit()
    return now + timedelta(seconds=RESEND_SECONDS)


def activate_code(db: Session, email: str, challenge: str, digest: str, now: datetime) -> datetime:
    row = load_for_update(db, email)
    if row is None or row.pending_challenge_id != challenge:
        raise signup_error(409, "signup_challenge_replaced", "Request a new verification code.", now)
    expires = now + timedelta(minutes=CODE_MINUTES)
    row.pending_challenge_id = None
    row.challenge_id = challenge
    row.code_hash = digest
    row.code_expires_at = expires
    row.failures = 0
    row.last_activity = now
    db.commit()
    return expires


def verify_code(db: Session, email: str, challenge: str, code: str) -> tuple[str, datetime, datetime]:
    row = load_for_update(db, email)
    now = utc_now()
    if (row is None or row.challenge_id != challenge or not row.code_hash
            or row.code_expires_at is None or now >= row.code_expires_at):
        raise signup_error(400, "signup_code_expired", "This code is unavailable. Request a new code.", now)
    if not hmac.compare_digest(row.code_hash, otp_digest(email, challenge, code)):
        row.failures += 1
        row.last_activity = now
        exhausted = row.failures >= ATTEMPT_LIMIT
        if exhausted:
            row.code_hash = None
            row.code_expires_at = None
        db.commit()
        if exhausted:
            raise signup_error(429, "signup_code_attempt_limit", "Too many attempts for this code. Request a new one.", now)
        raise signup_error(400, "signup_code_invalid", "The verification code is incorrect.", now)

    proof = secrets.token_urlsafe(32)
    expires = now + timedelta(minutes=GRANT_MINUTES)
    row.code_hash = None
    row.code_expires_at = None
    row.grant_hash = hashlib.sha256(proof.encode()).hexdigest()
    row.grant_expires_at = expires
    row.last_activity = now
    db.commit()
    return proof, expires, now


def consume_grant(db: Session, email: str, proof: str) -> None:
    row = load_for_update(db, email)
    now = utc_now()
    digest = hashlib.sha256(proof.encode()).hexdigest()
    if (row is None or not row.grant_hash or row.grant_expires_at is None
            or now >= row.grant_expires_at or not hmac.compare_digest(digest, row.grant_hash)):
        raise signup_error(400, "signup_proof_invalid", "Verify your email again to continue.", now)
    row.grant_hash = None
    row.grant_expires_at = None
    row.last_activity = now
