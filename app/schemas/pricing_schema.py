from typing import List, Optional

from pydantic import BaseModel, Field


class PricingPlanBase(BaseModel):
    name: str
    slug: str
    label: Optional[str] = None
    price: float = 0.0
    credits: int = 0
    description: Optional[str] = None
    features: Optional[List[str]] = None
    is_popular: bool = False
    display_order: int = 0
    is_active: bool = True


class PricingPlanCreate(PricingPlanBase):
    pass


class PricingPlanUpdate(BaseModel):
    name: Optional[str] = None
    slug: Optional[str] = None
    label: Optional[str] = None
    price: Optional[float] = None
    credits: Optional[int] = None
    description: Optional[str] = None
    features: Optional[List[str]] = None
    is_popular: Optional[bool] = None
    display_order: Optional[int] = None
    is_active: Optional[bool] = None


class PricingPlanPublic(BaseModel):
    id: int
    name: str
    slug: str
    label: Optional[str] = None
    price: float
    credits: int
    description: Optional[str] = None
    features: Optional[List[str]] = None
    is_popular: bool
    display_order: int

    class Config:
        from_attributes = True
