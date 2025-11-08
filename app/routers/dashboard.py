from typing import List

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.config import Roles
from app.core.security import require_roles
from app.database.connection import get_db
from app.models.resume import Resume
from app.models.template import Template
from app.models.user import User
from app.schemas.dashboard_schema import DashboardRecentItem, DashboardSummary


router = APIRouter(prefix="/dashboard", tags=["Dashboard"])


@router.get("/summary", response_model=DashboardSummary)
def get_summary(db: Session = Depends(get_db), user: User = Depends(require_roles(Roles.user, Roles.admin))):
    resume_count = db.query(Resume).filter(Resume.user_id == user.id, Resume.is_deleted == False).count()  # noqa: E712
    recents = (
        db.query(Resume)
        .filter(Resume.user_id == user.id, Resume.is_deleted == False)  # noqa: E712
        .order_by(Resume.updated_at.desc())
        .limit(5)
        .all()
    )
    recent_items: List[DashboardRecentItem] = [
        DashboardRecentItem(id=r.id, title=r.title, updated_at=r.updated_at) for r in recents
    ]
    templates = db.query(Template).order_by(Template.created_at.desc()).limit(4).all()
    suggested = [{"id": t.id, "name": t.name, "slug": t.slug, "preview_url": t.preview_url, "is_premium": t.is_premium} for t in templates]

    return DashboardSummary(
        welcome_name=user.name,
        resume_count=resume_count,
        credits_remaining=int(user.credits_remaining or 0),
        recent=recent_items,
        suggested_templates=suggested,
    )

