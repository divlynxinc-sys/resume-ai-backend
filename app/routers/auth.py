import hashlib
import os
import secrets
from datetime import datetime, timezone, timedelta
import random

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import Roles
from app.core.security import require_roles
from app.database.connection import get_db
from app.models.user import User
from app.schemas.user_schema import (
    ForgotPasswordRequest,
    GoogleAuthRequest,
    OtpLoginStart,
    OtpStartResponse,
    OtpVerifyRequest,
    ProfileUpdate,
    ResetPasswordRequest,
    SignupOtpSend,
    SignupOtpSent,
    SignupOtpVerify,
    SignupOtpVerified,
    TokenPair,
    TokenRefresh,
    UserCreate,
    UserLogin,
    UserPublic,
)
from app.utils.auth_utils import create_access_token, create_refresh_token, decode_token, hash_password, verify_password
from app.utils.email_utils import send_otp_email, send_password_changed_email, send_password_reset_email, send_signup_otp_email
from app.utils import signup_verification
from app.utils.turnstile import verify_turnstile

PASSWORD_RESET_COOLDOWN_SECONDS = 60
PASSWORD_RESET_TOKEN_TTL_MINUTES = 30

GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")

router = APIRouter(prefix="/auth", tags=["Auth"])


@router.post("/signup/send-otp", response_model=SignupOtpSent)
def signup_send_otp(payload: SignupOtpSend, request: Request, db: Session = Depends(get_db)):
    verify_turnstile(
        payload.turnstile_token,
        request.client.host if request.client else None,
        expected_actions={"signup"},
    )
    email = payload.email.lower()
    existing = db.query(User.id).filter(User.email == email).first()
    if existing:
        raise HTTPException(status_code=400, detail="Email already registered")

    code = f"{secrets.randbelow(1_000_000):06d}"
    challenge = secrets.token_urlsafe(18)
    digest = signup_verification.otp_digest(email, challenge, code)
    retry_at = signup_verification.reserve_send(db, email, challenge, signup_verification.utc_now())
    if not send_signup_otp_email(email, code, signup_verification.CODE_MINUTES, challenge):
        raise signup_verification.signup_error(
            503, "signup_email_unavailable", "Could not send a verification code. Please try again.",
            signup_verification.utc_now(), retry_at,
        )
    now = signup_verification.utc_now()
    expires = signup_verification.activate_code(db, email, challenge, digest, now)
    return SignupOtpSent(
        message="OTP sent to your email", challenge_id=challenge, expires_at=expires,
        resend_available_at=retry_at, server_now=now,
    )


@router.post("/signup/verify-otp", response_model=SignupOtpVerified)
def signup_verify_otp(payload: SignupOtpVerify, db: Session = Depends(get_db)):
    proof, expires, now = signup_verification.verify_code(db, payload.email.lower(), payload.challenge_id, payload.otp_code)
    return SignupOtpVerified(
        message="Email verified successfully", signup_token=proof, signup_expires_at=expires, server_now=now,
    )

@router.post("/signup", response_model=TokenPair)
def signup(user: UserCreate, db: Session = Depends(get_db)):
    email_normalized = user.email.lower()

    signup_verification.consume_grant(db, email_normalized, user.signup_token)
    hashed_pw = hash_password(user.password)
    instance = User(
        name=user.name,
        email=email_normalized,
        password_hash=hashed_pw,
        role=Roles.user,
        token_version=1,
    )
    db.add(instance)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        if getattr(getattr(exc.orig, "diag", None), "constraint_name", None) == "ix_users_email":
            raise HTTPException(status_code=400, detail="Email already registered") from exc
        raise
    db.refresh(instance)

    # Return tokens so the user is logged in immediately
    token_version = int(instance.token_version or 1)
    user_id = str(instance.id)
    access_token = create_access_token(
        subject=user_id,
        token_version=token_version,
        additional_claims={"email": instance.email, "role": instance.role or Roles.user},
    )
    refresh_token = create_refresh_token(
        subject=user_id,
        token_version=token_version,
    )
    return TokenPair(access_token=access_token, refresh_token=refresh_token, token_type="bearer")


@router.post("/login", response_model=TokenPair)
def login(payload: UserLogin, request: Request, db: Session = Depends(get_db)):
    """
    Standard email/password login that immediately returns access + refresh tokens.

    This is kept for existing integrations. For OTP-based login, use:
    - POST /auth/login/otp/start
    - POST /auth/login/otp/verify
    """
    verify_turnstile(
        payload.turnstile_token,
        request.client.host if request.client else None,
        expected_actions={"login"},
    )
    email_normalized = payload.email.lower()
    user = db.query(User).filter(User.email == email_normalized, User.is_deleted == False).first()  # noqa: E712
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")
    if not user.password_hash or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")
    token_version = int(user.token_version or 1)
    user_id = str(user.id)
    access_token = create_access_token(
        subject=user_id,
        token_version=token_version,
        additional_claims={"email": user.email, "role": user.role or Roles.user},
    )
    refresh_token = create_refresh_token(
        subject=user_id,
        token_version=token_version,
    )
    return TokenPair(access_token=access_token, refresh_token=refresh_token, token_type="bearer")


@router.post("/login/otp/start", response_model=OtpStartResponse)
def start_otp_login(payload: OtpLoginStart, db: Session = Depends(get_db)):
    """
    Step 1: Verify credentials, generate a 6-digit OTP, send it via email, and
    store it on the user with a short expiry.
    """
    email_normalized = payload.email.lower()
    user = db.query(User).filter(User.email == email_normalized, User.is_deleted == False).first()  # noqa: E712
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")
    if not user.password_hash or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")

    # Generate a 6-digit numeric OTP
    otp_code = f"{random.randint(0, 999999):06d}"
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=10)

    user.otp_code = otp_code
    user.otp_expires_at = expires_at
    db.add(user)
    db.commit()

    send_otp_email(user.email, otp_code)

    return OtpStartResponse(message="OTP sent to your email", otp_sent=True)


@router.post("/login/otp/verify", response_model=TokenPair)
def verify_otp_login(payload: OtpVerifyRequest, db: Session = Depends(get_db)):
    """
    Step 2: Verify the 6-digit OTP and issue access + refresh tokens.
    """
    email_normalized = payload.email.lower()
    user = db.query(User).filter(User.email == email_normalized, User.is_deleted == False).first()  # noqa: E712
    if not user or not user.otp_code:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired OTP")

    now = datetime.now(timezone.utc)
    if not user.otp_expires_at or user.otp_expires_at < now:
        user.otp_code = None
        user.otp_expires_at = None
        db.add(user)
        db.commit()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="OTP has expired")

    if payload.otp_code != user.otp_code:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid OTP code")

    # Clear OTP fields after successful verification
    user.otp_code = None
    user.otp_expires_at = None
    db.add(user)
    db.commit()
    db.refresh(user)

    token_version = int(user.token_version or 1)
    user_id = str(user.id)
    access_token = create_access_token(
        subject=user_id,
        token_version=token_version,
        additional_claims={"email": user.email, "role": user.role or Roles.user},
    )
    refresh_token = create_refresh_token(
        subject=user_id,
        token_version=token_version,
    )
    return TokenPair(access_token=access_token, refresh_token=refresh_token, token_type="bearer")


@router.post("/google", response_model=TokenPair)
def google_auth(payload: GoogleAuthRequest, request: Request, db: Session = Depends(get_db)):
    """
    Authenticate with Google. Accepts a Google access token from the frontend.
    Verifies it by calling Google's userinfo API.
    - If the Google account is already linked to a user, logs them in.
    - If the email exists but isn't linked to Google yet, links the account and logs in.
    - If the user doesn't exist, creates a new account.
    """
    # Verify the access token by fetching user info from Google
    verify_turnstile(
        payload.turnstile_token,
        request.client.host if request.client else None,
        expected_actions={"login", "signup"},
    )
    try:
        resp = httpx.get(
            "https://www.googleapis.com/oauth2/v3/userinfo",
            headers={"Authorization": f"Bearer {payload.credential}"},
            timeout=10.0,
        )
        if resp.status_code != 200:
            raise ValueError("Invalid token")
        idinfo = resp.json()
    except Exception:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid Google token")

    google_sub = idinfo["sub"]
    email = idinfo.get("email", "").lower()
    name = idinfo.get("name", email.split("@")[0])

    if not email:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Google account has no email")
    if idinfo.get("email_verified") is not True:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Google email is not verified")

    # 1. Check if user exists by google_sub
    user = db.query(User).filter(User.google_sub == google_sub, User.is_deleted == False).first()  # noqa: E712
    is_new_user = False

    if not user:
        # 2. Check if user exists by email (link Google to existing account)
        user = db.query(User).filter(User.email == email, User.is_deleted == False).first()  # noqa: E712
        if user:
            user.google_sub = google_sub
            db.add(user)
            db.commit()
            db.refresh(user)
        else:
            # 3. Create new user
            is_new_user = True
            user = User(
                name=name,
                email=email,
                password_hash=None,
                google_sub=google_sub,
                role=Roles.user,
                token_version=1,
            )
            db.add(user)
            db.commit()
            db.refresh(user)

    token_version = int(user.token_version or 1)
    user_id = str(user.id)
    access_token = create_access_token(
        subject=user_id,
        token_version=token_version,
        additional_claims={"email": user.email, "role": user.role or Roles.user},
    )
    refresh_token = create_refresh_token(
        subject=user_id,
        token_version=token_version,
    )
    return TokenPair(access_token=access_token, refresh_token=refresh_token, token_type="bearer", is_new_user=is_new_user)


@router.post("/refresh", response_model=TokenPair)
def refresh_tokens(data: TokenRefresh, db: Session = Depends(get_db)):
    payload = decode_token(data.refresh_token)
    if not payload or payload.get("type") != "refresh":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token")
    user_id = payload.get("sub")
    token_version = payload.get("tv")
    try:
        user_id_int = int(user_id)
    except Exception:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token")
    user = db.query(User).filter(User.id == user_id_int, User.is_deleted == False).first()  # noqa: E712
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token")
    if int(user.token_version or 1) != token_version:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Refresh token revoked")

    # Rotate token version on refresh to prevent reuse of stolen refresh tokens
    user.token_version = int(user.token_version or 1) + 1
    db.add(user)
    db.commit()
    db.refresh(user)

    access_token = create_access_token(
        subject=str(user.id),
        token_version=user.token_version,
        additional_claims={"email": user.email, "role": user.role or Roles.user},
    )
    refresh_token = create_refresh_token(
        subject=str(user.id),
        token_version=user.token_version,
    )
    return TokenPair(access_token=access_token, refresh_token=refresh_token, token_type="bearer")


@router.post("/logout-all")
def logout_all(db: Session = Depends(get_db), user = Depends(require_roles(Roles.user, Roles.admin))):
    user.token_version = int(user.token_version or 1) + 1
    db.add(user)
    db.commit()
    return {"message": "Logged out from all sessions"}


_FORGOT_PASSWORD_RESPONSE = {"message": "If an account exists for this email, a reset link has been sent."}


@router.post("/forgot-password")
def forgot_password(
    payload: ForgotPasswordRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """
    Request a password reset link.

    Always returns the same response whether or not the email is registered,
    so this endpoint can't be used to find registered accounts. The email
    itself is sent via BackgroundTasks (after the response is sent) so the
    "exists" and "doesn't exist" paths also take the same amount of time --
    the slow part (talking to the email provider) never runs on the request
    path either way.
    """
    verify_turnstile(
        payload.turnstile_token,
        request.client.host if request.client else None,
        expected_actions={"forgot_password"},
    )
    email_normalized = payload.email.lower()
    user = db.query(User).filter(User.email == email_normalized, User.is_deleted == False).first()  # noqa: E712
    if user:
        now = datetime.now(timezone.utc)
        requested_at = user.password_reset_requested_at
        if requested_at is not None and requested_at.tzinfo is None:
            requested_at = requested_at.replace(tzinfo=timezone.utc)
        on_cooldown = (
            requested_at is not None
            and (now - requested_at).total_seconds() < PASSWORD_RESET_COOLDOWN_SECONDS
        )
        if not on_cooldown:
            # A new request invalidates any previous token (single active token per user).
            raw_token = secrets.token_urlsafe(32)
            user.password_reset_token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
            user.password_reset_expires_at = now + timedelta(minutes=PASSWORD_RESET_TOKEN_TTL_MINUTES)
            user.password_reset_requested_at = now
            db.add(user)
            db.commit()

            frontend_url = os.getenv("FRONTEND_URL", "http://localhost:5173").rstrip("/")
            reset_link = f"{frontend_url}/reset-password?token={raw_token}"
            background_tasks.add_task(send_password_reset_email, user.email, reset_link)
        # On cooldown: skip silently -- same response, no new email, no DB write.
    return _FORGOT_PASSWORD_RESPONSE


@router.post("/reset-password")
def reset_password(
    payload: ResetPasswordRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """
    Set a new password from a forgot-password link.

    Works for Google-only accounts too (password_hash is NULL) -- a reset
    simply sets one, so those users aren't locked out of ever having a
    password. The token is single-use: cleared on success, and a mismatch or
    expiry both return the same generic error so neither leaks which check
    failed.
    """
    token_hash = hashlib.sha256(payload.token.encode()).hexdigest()
    now = datetime.now(timezone.utc)
    user = (
        db.query(User)
        .filter(
            User.password_reset_token_hash == token_hash,
            User.password_reset_expires_at > now,
            User.is_deleted == False,  # noqa: E712
        )
        .first()
    )
    if not user:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="This link is invalid or has expired")

    user.password_hash = hash_password(payload.new_password)
    user.password_reset_token_hash = None
    user.password_reset_expires_at = None
    user.token_version = int(user.token_version or 1) + 1
    db.add(user)
    db.commit()

    background_tasks.add_task(send_password_changed_email, user.email)
    return {"message": "Password has been reset"}


