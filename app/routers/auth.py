from datetime import datetime, timezone
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.config import Roles
from app.core.security import require_roles
from app.database.connection import get_db
from app.models.user import User
from app.schemas.user_schema import ProfileUpdate, TokenPair, TokenRefresh, UserCreate, UserLogin, UserPublic
from app.utils.auth_utils import create_access_token, create_refresh_token, decode_token, hash_password, verify_password


router = APIRouter(prefix="/auth", tags=["Auth"])


@router.post("/signup")
def signup(user: UserCreate, db: Session = Depends(get_db)):
    email_normalized = user.email.lower()
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
    return {"message": "User registered successfully", "user_id": instance.id}


@router.post("/login", response_model=TokenPair)
def login(payload: UserLogin, db: Session = Depends(get_db)):
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


