from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class CategoryScoreItem(BaseModel):
    """Single category in ATS breakdown (e.g. Keyword Match)."""
    label: Optional[str] = None
    description: Optional[str] = None
    score: Optional[int] = None
    max: Optional[int] = None
    percent: Optional[float] = None


class ATSScorePayload(BaseModel):
    """Payload from AI service - data structure for ATS analysis."""
    overall_score: int = Field(..., ge=0, le=100)
    max_score: int = Field(default=100, ge=1, le=100)
    category_scores: Optional[Dict[str, Any]] = None  # e.g. {"keyword_match": {...}, ...}
    recommendations: Optional[List[str]] = None  # ["Add keyword...", ...]


class ATSScoreResponse(BaseModel):
    """Response when retrieving stored ATS score."""
    id: int
    resume_id: int
    overall_score: int
    max_score: int
    category_scores: Optional[Dict[str, Any]] = None
    recommendations: Optional[List[str]] = None
    analyzed_at: datetime
    created_at: datetime

    class Config:
        from_attributes = True
