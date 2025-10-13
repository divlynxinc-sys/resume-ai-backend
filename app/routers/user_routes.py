from fastapi import APIRouter, Depends
from app.schemas.user_schema import UserCreate

router = APIRouter(prefix="/users", tags=["Users"])

@router.post("/")
def create_user(user: UserCreate):
    return {"message": f"User {user.name} registered successfully!"}
