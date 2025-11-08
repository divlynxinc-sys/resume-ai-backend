from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.orm import Session
from app.schemas.user_schema import UserCreate
from app.database.connection import get_db
from app.utils.auth_utils import hash_password
from app.core.config import Roles
from app.models.user import User

router = APIRouter(prefix="/users", tags=["Users"]) 

@router.post("/register")
def register_user(user: UserCreate, db: Session = Depends(get_db)):
    email_normalized = user.email.lower()
    existing_user = db.query(User).filter(User.email == email_normalized).first()
    if existing_user:
        raise HTTPException(status_code=400, detail="Email already registered")

    instance = User(
        name=user.name,
        email=email_normalized,
        password_hash=hash_password(user.password),
        role=Roles.user,
        token_version=1,
    )
    db.add(instance)
    db.commit()
    return {"message": "User registered successfully!"}
