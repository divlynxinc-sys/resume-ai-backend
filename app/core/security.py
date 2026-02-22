from typing import Callable

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from app.core.config import Roles
from app.database.connection import get_db
from app.models.user import User
from app.utils.auth_utils import decode_token


oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")


def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)) -> User:
    payload = decode_token(token)
    if not payload or payload.get("type") != "access":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid authentication credentials")
    user_id = payload.get("sub")
    token_version = payload.get("tv")
    if not user_id or token_version is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid authentication credentials")
    try:
        user_id_int = int(user_id)
    except Exception:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid authentication credentials")
    user = db.query(User).filter(User.id == user_id_int, User.is_deleted == False).first()  # noqa: E712
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    if int(user.token_version or 1) != token_version:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token expired or revoked")
    return user


def require_roles(*allowed_roles: str) -> Callable[[User], User]:
    def _checker(user: User = Depends(get_current_user)) -> User:
        role = user.role or Roles.user
        if role not in allowed_roles:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions")
        return user
    return _checker

