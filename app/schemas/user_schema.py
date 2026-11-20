from pydantic import BaseModel, EmailStr, Field


class UserCreate(BaseModel):
    name: str
    email: EmailStr
    password: str


class SignupOtpSend(BaseModel):
    email: EmailStr
    turnstile_token: str | None = None


class SignupOtpVerify(BaseModel):
    email: EmailStr
    otp_code: str


class UserLogin(BaseModel):
    email: EmailStr
    password: str
    turnstile_token: str


class OtpLoginStart(BaseModel):
    email: EmailStr
    password: str


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


class ProfileUpdate(BaseModel):
    name: str | None = None
    phone: str | None = None
    location: str | None = None
    linkedin_url: str | None = None
    portfolio_url: str | None = None


class PasswordChange(BaseModel):
    old_password: str
    new_password: str
    confirm_password: str


class GoogleAuthRequest(BaseModel):
    credential: str  # Google ID token from frontend
    turnstile_token: str


class ForgotPasswordRequest(BaseModel):
    email: EmailStr
    turnstile_token: str


class ResetPasswordRequest(BaseModel):
    token: str = Field(max_length=256)
    new_password: str = Field(min_length=8, max_length=128)
