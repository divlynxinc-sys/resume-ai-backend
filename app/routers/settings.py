import json
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.core.config import Roles
from app.core.security import require_roles
from app.database.connection import get_db
from app.models.pricing_plan import PricingPlan
from app.models.resume import Resume
from app.models.user import User
from app.models.user_settings import UserSettings
from app.schemas.settings_schema import AccountSummary, UserSettingsResponse, UserSettingsUpdate

router = APIRouter(prefix="/settings", tags=["Settings"])


def _get_or_create_settings(db: Session, user_id: int) -> UserSettings:
    settings = db.query(UserSettings).filter(UserSettings.user_id == user_id).first()
    if not settings:
        settings = UserSettings(user_id=user_id)
        db.add(settings)
        db.commit()
        db.refresh(settings)
    return settings


@router.get("/preferences", response_model=UserSettingsResponse)
def get_preferences(
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(Roles.user, Roles.admin)),
):
    """Get user preferences (theme, notifications, 2FA)."""
    settings = _get_or_create_settings(db, user.id)
    return UserSettingsResponse(
        dark_mode=settings.dark_mode,
        accent_color=settings.accent_color,
        email_notifications=settings.email_notifications,
        in_app_notifications=settings.in_app_notifications,
        two_factor_enabled=settings.two_factor_enabled,
    )


@router.patch("/preferences", response_model=UserSettingsResponse)
def update_preferences(
    payload: UserSettingsUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(Roles.user, Roles.admin)),
):
    """Update user preferences."""
    settings = _get_or_create_settings(db, user.id)
    data = payload.model_dump(exclude_unset=True)
    for k, v in data.items():
        setattr(settings, k, v)
    settings.updated_at = datetime.now(timezone.utc)
    db.add(settings)
    db.commit()
    db.refresh(settings)
    return UserSettingsResponse(
        dark_mode=settings.dark_mode,
        accent_color=settings.accent_color,
        email_notifications=settings.email_notifications,
        in_app_notifications=settings.in_app_notifications,
        two_factor_enabled=settings.two_factor_enabled,
    )


@router.get("/account/summary", response_model=AccountSummary)
def get_account_summary(
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(Roles.user, Roles.admin)),
):
    """Get account summary (plan name requires DB)."""
    plan_name = None
    if user.plan_id:
        plan = db.query(PricingPlan).filter(PricingPlan.id == user.plan_id).first()
        if plan:
            plan_name = plan.name
    return AccountSummary(
        current_plan=plan_name,
        credits_remaining=int(user.credits_remaining or 0),
    )


@router.get("/account/export")
def export_my_data(
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(Roles.user, Roles.admin)),
):
    """Export all user data (profile, resumes) as JSON. No N+1: single query for resumes."""
    settings = db.query(UserSettings).filter(UserSettings.user_id == user.id).first()
    resumes = db.query(Resume).filter(Resume.user_id == user.id, Resume.is_deleted == False).order_by(Resume.updated_at.desc()).all()

    export_data: dict[str, Any] = {
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "profile": {
            "id": str(user.id),
            "name": user.name,
            "email": user.email,
            "phone": user.phone,
            "location": user.location,
            "linkedin_url": user.linkedin_url,
            "portfolio_url": user.portfolio_url,
            "credits_remaining": int(user.credits_remaining or 0),
        },
        "settings": {
            "dark_mode": settings.dark_mode if settings else True,
            "accent_color": settings.accent_color if settings else "blue",
            "email_notifications": settings.email_notifications if settings else True,
            "in_app_notifications": settings.in_app_notifications if settings else True,
        } if settings else {},
        "resumes": [
            {
                "id": r.user_resume_id,
                "title": r.title,
                "status": r.status,
                "content": r.content,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "updated_at": r.updated_at.isoformat() if r.updated_at else None,
            }
            for r in resumes
        ],
    }

    def iter_json():
        yield json.dumps(export_data, indent=2, default=str).encode("utf-8")

    return StreamingResponse(
        iter_json(),
        media_type="application/json",
        headers={"Content-Disposition": "attachment; filename=resumeai-export.json"},
    )


@router.delete("/account", status_code=status.HTTP_204_NO_CONTENT)
def delete_my_account(
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(Roles.user, Roles.admin)),
):
    """Soft delete account - revokes tokens and marks user as deleted."""
    user.is_deleted = True
    user.token_version = int(user.token_version or 1) + 1
    db.add(user)
    db.commit()
    return None
