from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel


class HelpTopicBase(BaseModel):
    name: str
    slug: str
    description: Optional[str] = None
    icon: Optional[str] = None
    display_order: int = 0


class HelpTopicCreate(HelpTopicBase):
    pass


class HelpTopicUpdate(BaseModel):
    name: Optional[str] = None
    slug: Optional[str] = None
    description: Optional[str] = None
    icon: Optional[str] = None
    display_order: Optional[int] = None


class HelpTopicPublic(BaseModel):
    id: int
    name: str
    slug: str
    description: Optional[str] = None
    icon: Optional[str] = None
    display_order: int

    class Config:
        from_attributes = True


class HelpArticleBase(BaseModel):
    topic_id: int
    title: str
    slug: str
    excerpt: Optional[str] = None
    content: str = ""
    is_featured: bool = False
    is_faq: bool = False
    display_order: int = 0


class HelpArticleCreate(HelpArticleBase):
    pass


class HelpArticleUpdate(BaseModel):
    topic_id: Optional[int] = None
    title: Optional[str] = None
    slug: Optional[str] = None
    excerpt: Optional[str] = None
    content: Optional[str] = None
    is_featured: Optional[bool] = None
    is_faq: Optional[bool] = None
    display_order: Optional[int] = None


class HelpArticleListItem(BaseModel):
    id: int
    topic_id: int
    title: str
    slug: str
    excerpt: Optional[str] = None
    is_featured: bool
    is_faq: bool
    display_order: int
    created_at: datetime

    class Config:
        from_attributes = True


class HelpArticleDetail(BaseModel):
    id: int
    topic_id: int
    topic_name: Optional[str] = None
    title: str
    slug: str
    excerpt: Optional[str] = None
    content: str
    is_featured: bool
    is_faq: bool
    display_order: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
