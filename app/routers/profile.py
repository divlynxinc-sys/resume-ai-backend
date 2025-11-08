from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.config import Roles
from app.core.security import get_current_user, require_roles
from app.schemas.user_schema import ProfileUpdate, UserPublic
from app.database.connection import get_db
from app.utils.auth_utils import hash_password
from app.models.user import User


router = APIRouter(prefix="/profile", tags=["Profile"])


@router.get("/me", response_model=UserPublic)
def get_me(user: User = Depends(require_roles(Roles.user, Roles.admin))):
    return UserPublic(
        id=str(user.id),
        name=user.name,
        email=user.email,
        role=user.role or Roles.user,
    )


@router.patch("/me")
def update_me(payload: ProfileUpdate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    if payload.name is not None:
        user.name = payload.name
    if payload.password is not None and payload.password.strip():
        user.password_hash = hash_password(payload.password)
        user.token_version = int(user.token_version or 1) + 1
    db.add(user)
    db.commit()
    return {"message": "Profile updated"}

