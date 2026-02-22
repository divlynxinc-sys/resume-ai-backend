import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from jose import JWTError, jwt
from passlib.context import CryptContext

from app.core.config import jwt_settings


_pwd_context = CryptContext(
    schemes=["django_pbkdf2_sha256"],
    deprecated="auto",
    django_pbkdf2_sha256__default_rounds=int(os.getenv("PBKDF2_ROUNDS", "390000")),
)


def hash_password(password: str) -> str:
    return _pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return _pwd_context.verify(plain_password, hashed_password)


def create_access_token(subject: str, token_version: int, additional_claims: Optional[Dict[str, Any]] = None, expires_delta: Optional[timedelta] = None) -> str:
    to_encode: Dict[str, Any] = {"sub": subject, "tv": token_version, "type": "access"}
    if additional_claims:
        to_encode.update(additional_claims)
    expire = datetime.now(timezone.utc) + (expires_delta or timedelta(minutes=jwt_settings.access_expire_minutes))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, jwt_settings.secret_key, algorithm=jwt_settings.algorithm)


def create_refresh_token(subject: str, token_version: int, additional_claims: Optional[Dict[str, Any]] = None, expires_delta: Optional[timedelta] = None) -> str:
    to_encode: Dict[str, Any] = {"sub": subject, "tv": token_version, "type": "refresh"}
    if additional_claims:
        to_encode.update(additional_claims)
    expire = datetime.now(timezone.utc) + (expires_delta or timedelta(minutes=jwt_settings.refresh_expire_minutes))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, jwt_settings.secret_key, algorithm=jwt_settings.algorithm)


def decode_token(token: str) -> Optional[Dict[str, Any]]:
    try:
        payload = jwt.decode(token, jwt_settings.secret_key, algorithms=[jwt_settings.algorithm])
        return payload
    except JWTError:
        return None


