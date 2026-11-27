from datetime import datetime

from pydantic import BaseModel, EmailStr, Field


class UserCreate(BaseModel):
    name: str = Field(max_length=255)
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    signup_token: str = Field(pattern=r"^[A-Za-z0-9_-]{43}$")


class SignupOtpSend(BaseModel):
    email: EmailStr
    turnstile_token: str = Field(max_length=2048)


class SignupOtpVerify(BaseModel):
    email: EmailStr
    challenge_id: str = Field(pattern=r"^[A-Za-z0-9_-]{24}$")
    otp_code: str = Field(pattern=r"^[0-9]{6}$")


class SignupOtpSent(BaseModel):
    message: str
    otp_sent: bool = True
    challenge_id: str
    expires_at: datetime
    resend_available_at: datetime
    server_now: datetime


class SignupOtpVerified(BaseModel):
    message: str
    verified: bool = True
    signup_token: str
    signup_expires_at: datetime
    server_now: datetime


CURRENT_PASSWORD_MAX_LENGTH = 1024


class UserLogin(BaseModel):
    email: EmailStr
    # Allow legacy passwords longer than the limit for new passwords.
    password: str = Field(max_length=CURRENT_PASSWORD_MAX_LENGTH)
    turnstile_token: str


class OtpLoginStart(BaseModel):
    email: EmailStr
    password: str = Field(max_length=CURRENT_PASSWORD_MAX_LENGTH)


class OtpVerifyRequest(BaseModel):
    email: EmailStr
    otp_code: str


class OtpStartResponse(BaseModel):
    message: str
    otp_sent: bool = True


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    is_new_user: bool = False


class TokenRefresh(BaseModel):
    refresh_token: str


class UserPublic(BaseModel):
    id: str
    name: str
    email: str
    role: str = "user"
    phone: str | None = None
    location: str | None = None
    linkedin_url: str | None = None
    portfolio_url: str | None = None
    credits_remaining: int = 0
    onboarding_completed: bool = True


class ProfileUpdate(BaseModel):
    name: str | None = None
    phone: str | None = None
    location: str | None = None
    linkedin_url: str | None = None
    portfolio_url: str | None = None


class PasswordChange(BaseModel):
    old_password: str = Field(max_length=CURRENT_PASSWORD_MAX_LENGTH)
    new_password: str = Field(min_length=8, max_length=128)
    confirm_password: str = Field(max_length=128)


class GoogleAuthRequest(BaseModel):
    credential: str  # Google ID token from frontend
    turnstile_token: str


class ForgotPasswordRequest(BaseModel):
    email: EmailStr
    turnstile_token: str


class ResetPasswordRequest(BaseModel):
    token: str = Field(max_length=256)
    new_password: str = Field(min_length=8, max_length=128)
