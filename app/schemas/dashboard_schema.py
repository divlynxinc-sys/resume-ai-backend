from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel


class DashboardRecentItem(BaseModel):
    id: int
    title: str
    updated_at: datetime


class DashboardSummary(BaseModel):
    welcome_name: str
    resume_count: int
    credits_remaining: int
    recent: List[DashboardRecentItem]
    suggested_templates: List[dict] = []

