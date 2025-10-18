from fastapi import APIRouter, HTTPException, Depends
from app.schemas.user_schema import UserCreate
from app.database.connection import get_db
from app.models.user import User
from pydantic import EmailStr

router = APIRouter(prefix="/users", tags=["Users"]) 

@router.post("/register")
def register_user(user: UserCreate, db = Depends(get_db)):
    # Check if user already exists
    existing_user = db.users.find_one({"email": user.email})
    if existing_user:
        raise HTTPException(status_code=400, detail="Email already registered")
    
    # Create user model from schema
    user_data = User(
        name=user.name,
        email=EmailStr(user.email),
        password=user.password
    )
    
    # Insert into database
    db.users.insert_one(user_data.dict())
    return {"message": "User registered successfully!"}
