from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.config import Roles
from app.core.security import require_roles
from app.database.connection import get_db
from app.models.template import Template


router = APIRouter(prefix="/templates", tags=["Templates"])


@router.get("")
def list_templates(
    q: Optional[str] = Query(default=None),
    limit: int = Query(default=12, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    _: dict = Depends(require_roles(Roles.user, Roles.admin)),
):
    query = db.query(Template)
    if q:
        query = query.filter(Template.name.ilike(f"%{q}%"))
    rows = query.order_by(Template.created_at.desc()).limit(limit).offset(offset).all()
    return [{"id": t.id, "name": t.name, "slug": t.slug, "preview_url": t.preview_url, "is_premium": t.is_premium} for t in rows]

