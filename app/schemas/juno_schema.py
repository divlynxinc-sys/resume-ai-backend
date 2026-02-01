from typing import Optional

from pydantic import BaseModel


class JunoPromptBase(BaseModel):
    text: str
    category: Optional[str] = None
    display_order: int = 0
    is_active: bool = True


class JunoPromptCreate(JunoPromptBase):
    pass


class JunoPromptUpdate(BaseModel):
    text: Optional[str] = None
    category: Optional[str] = None
    display_order: Optional[int] = None
    is_active: Optional[bool] = None


class JunoPromptPublic(BaseModel):
    id: int
    text: str
    category: Optional[str] = None
    display_order: int

    class Config:
        from_attributes = True
