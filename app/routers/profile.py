from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.config import Roles
from app.core.security import get_current_user, require_roles
from app.database.connection import get_db
from app.models.resume import Resume
from app.models.user import User
from app.schemas.user_schema import PasswordChange, ProfileUpdate, UserPublic
from app.utils.auth_utils import hash_password, verify_password


router = APIRouter(prefix="/profile", tags=["Profile"])


@router.get("/me", response_model=UserPublic)
def get_me(user: User = Depends(require_roles(Roles.user, Roles.admin))):
    return UserPublic(
        id=str(user.id),
        name=user.name,
        email=user.email,
        role=user.role or Roles.user,
        phone=user.phone,
        location=user.location,
        linkedin_url=user.linkedin_url,
        portfolio_url=user.portfolio_url,
        credits_remaining=int(user.credits_remaining or 0),
    )


@router.patch("/me", response_model=UserPublic)
def update_me(payload: ProfileUpdate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    if payload.name is not None:
        user.name = payload.name
    if payload.phone is not None:
        user.phone = payload.phone or None
    if payload.location is not None:
        user.location = payload.location or None
    if payload.linkedin_url is not None:
        user.linkedin_url = payload.linkedin_url or None
    if payload.portfolio_url is not None:
        user.portfolio_url = payload.portfolio_url or None
    db.add(user)
    db.commit()
    db.refresh(user)
    return UserPublic(
        id=str(user.id),
        name=user.name,
        email=user.email,
        role=user.role or Roles.user,
        phone=user.phone,
        location=user.location,
        linkedin_url=user.linkedin_url,
        portfolio_url=user.portfolio_url,
        credits_remaining=int(user.credits_remaining or 0),
    )


@router.post("/change-password", response_model=UserPublic)
def change_password(
    payload: PasswordChange,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Change password. Requires old password and new password confirmation."""
    if not verify_password(payload.old_password, user.password_hash):
        raise HTTPException(status_code=400, detail="Current password is incorrect")
    if payload.new_password != payload.confirm_password:
        raise HTTPException(status_code=400, detail="New password and confirmation do not match")
    if len(payload.new_password.strip()) < 6:
        raise HTTPException(status_code=400, detail="New password must be at least 6 characters")
    user.password_hash = hash_password(payload.new_password)
    user.token_version = int(user.token_version or 1) + 1
    db.add(user)
    db.commit()
    db.refresh(user)
    return UserPublic(
        id=str(user.id),
        name=user.name,
        email=user.email,
        role=user.role or Roles.user,
        phone=user.phone,
        location=user.location,
        linkedin_url=user.linkedin_url,
        portfolio_url=user.portfolio_url,
        credits_remaining=int(user.credits_remaining or 0),
    )


@router.post("/sync-from-resume/{resume_id}", response_model=UserPublic)
def sync_profile_from_resume(
    resume_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Sync profile fields (phone, location, linkedin, portfolio) from a resume's info section."""
    r = db.query(Resume).filter(
        Resume.user_id == user.id,
        Resume.user_resume_id == resume_id,
        Resume.is_deleted == False,  # noqa: E712
    ).first()
    if not r:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Resume not found")
    info = (r.content or {}).get("info")
    if info and isinstance(info, dict):
        if info.get("full_name"):
            user.name = info["full_name"]
        if info.get("email"):
            user.email = str(info["email"]).lower()
        if "phone" in info:
            user.phone = info.get("phone") or None
        if "location" in info:
            user.location = info.get("location") or None
        if "linkedin_url" in info:
            user.linkedin_url = info.get("linkedin_url") or None
        if "portfolio_url" in info:
            user.portfolio_url = info.get("portfolio_url") or None
        db.add(user)
        db.commit()
    db.refresh(user)
    return UserPublic(
        id=str(user.id),
        name=user.name,
        email=user.email,
        role=user.role or Roles.user,
        phone=user.phone,
        location=user.location,
        linkedin_url=user.linkedin_url,
        portfolio_url=user.portfolio_url,
        credits_remaining=int(user.credits_remaining or 0),
    )

