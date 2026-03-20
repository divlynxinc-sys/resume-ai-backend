from datetime import datetime, timezone, timedelta
import random

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.config import Roles
from app.core.security import require_roles
from app.database.connection import get_db
from app.models.user import User
from app.schemas.user_schema import (
    OtpLoginStart,
    OtpStartResponse,
    OtpVerifyRequest,
    ProfileUpdate,
    SignupOtpSend,
    SignupOtpVerify,
    TokenPair,
    TokenRefresh,
    UserCreate,
    UserLogin,
    UserPublic,
)
from app.utils.auth_utils import create_access_token, create_refresh_token, decode_token, hash_password, verify_password
from app.utils.email_utils import send_otp_email

# In-memory store for signup OTPs: {email: {"otp": str, "expires_at": datetime}}
_signup_otps: dict[str, dict] = {}


router = APIRouter(prefix="/auth", tags=["Auth"])


@router.post("/signup/send-otp")
def signup_send_otp(payload: SignupOtpSend, db: Session = Depends(get_db)):
    """Send a 6-digit OTP to the given email for signup verification."""
    email_normalized = payload.email.lower()
    existing = db.query(User).filter(User.email == email_normalized).first()
    if existing:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Email already registered")

    otp_code = f"{random.randint(0, 999999):06d}"
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=60)

    _signup_otps[email_normalized] = {"otp": otp_code, "expires_at": expires_at}

    send_otp_email(email_normalized, otp_code, subject="Verify your email - Jobsynk AI", template="otp_signup.html")

    return {"message": "OTP sent to your email", "otp_sent": True}


@router.post("/signup/verify-otp")
def signup_verify_otp(payload: SignupOtpVerify):
    """Verify the signup OTP code."""
    email_normalized = payload.email.lower()
    entry = _signup_otps.get(email_normalized)

    if not entry:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No OTP found. Please request a new one.")

    now = datetime.now(timezone.utc)
    if entry["expires_at"] < now:
        _signup_otps.pop(email_normalized, None)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="OTP has expired. Please request a new one.")

    if entry["otp"] != payload.otp_code:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid OTP code")

    # Mark as verified (keep entry so signup can check it)
    _signup_otps[email_normalized]["verified"] = True

    return {"message": "Email verified successfully", "verified": True}


@router.post("/signup", response_model=TokenPair)
def signup(user: UserCreate, db: Session = Depends(get_db)):
    email_normalized = user.email.lower()

    # Verify that email OTP was completed
    otp_entry = _signup_otps.get(email_normalized)
    if not otp_entry or not otp_entry.get("verified"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Email not verified. Please verify your email first.")
    _signup_otps.pop(email_normalized, None)

    existing = db.query(User).filter(User.email == email_normalized).first()
    if existing:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Email already registered")
    hashed_pw = hash_password(user.password)
    instance = User(
        name=user.name,
        email=email_normalized,
        password_hash=hashed_pw,
        role=Roles.user,
        token_version=1,
    )
    db.add(instance)
    db.commit()
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
def login(payload: UserLogin, db: Session = Depends(get_db)):
    """
    Standard email/password login that immediately returns access + refresh tokens.

    This is kept for existing integrations. For OTP-based login, use:
    - POST /auth/login/otp/start
    - POST /auth/login/otp/verify
    """
    email_normalized = payload.email.lower()
    user = db.query(User).filter(User.email == email_normalized, User.is_deleted == False).first()  # noqa: E712
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")
    if not verify_password(payload.password, user.password_hash):
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
    if not verify_password(payload.password, user.password_hash):
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


