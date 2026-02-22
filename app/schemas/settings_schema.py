from typing import Optional

from pydantic import BaseModel


class UserSettingsResponse(BaseModel):
    dark_mode: bool = True
    accent_color: str = "blue"
    email_notifications: bool = True
    in_app_notifications: bool = True
    two_factor_enabled: bool = False

    class Config:
        from_attributes = True


class UserSettingsUpdate(BaseModel):
    dark_mode: Optional[bool] = None
    accent_color: Optional[str] = None
    email_notifications: Optional[bool] = None
    in_app_notifications: Optional[bool] = None
    two_factor_enabled: Optional[bool] = None


class AccountSummary(BaseModel):
    """For settings page - plan, credits, etc."""
    current_plan: Optional[str] = None
    credits_remaining: int = 0
