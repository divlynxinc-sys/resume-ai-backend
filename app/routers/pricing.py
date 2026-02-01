from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.config import Roles
from app.core.security import require_roles
from app.database.connection import get_db
from app.models.pricing_plan import PricingPlan
from app.models.user import User
from app.schemas.pricing_schema import PricingPlanCreate, PricingPlanPublic, PricingPlanUpdate

router = APIRouter(prefix="/pricing", tags=["Pricing"])


@router.post("/plans/{plan_id}/choose")
def choose_plan(
    plan_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(Roles.user, Roles.admin)),
):
    """User selects a plan (updates their plan_id). No payment - for demo/simple flow."""
    plan = db.query(PricingPlan).filter(PricingPlan.id == plan_id, PricingPlan.is_active == True).first()  # noqa: E712
    if not plan:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plan not found")
    user.plan_id = plan.id
    user.credits_remaining = plan.credits  # Reset to plan credits on choose
    db.add(user)
    db.commit()
    return {"message": f"Plan '{plan.name}' selected", "credits": plan.credits}


@router.get("/plans", response_model=List[PricingPlanPublic])
def list_plans(
    active_only: bool = Query(default=True, description="Return only active plans"),
    db: Session = Depends(get_db),
):
    """Public endpoint - list pricing plans for display on pricing page."""
    query = db.query(PricingPlan)
    if active_only:
        query = query.filter(PricingPlan.is_active == True)  # noqa: E712
    plans = query.order_by(PricingPlan.display_order, PricingPlan.id).all()
    return [PricingPlanPublic.model_validate(p) for p in plans]


# --- Admin CRUD (customizable pricing) ---


@router.get("/admin/plans", response_model=List[PricingPlanPublic])
def admin_list_plans(
    db: Session = Depends(get_db),
    _: User = Depends(require_roles(Roles.admin)),
):
    """Admin: list all plans including inactive."""
    plans = db.query(PricingPlan).order_by(PricingPlan.display_order, PricingPlan.id).all()
    return [PricingPlanPublic.model_validate(p) for p in plans]


@router.post("/admin/plans", response_model=PricingPlanPublic, status_code=status.HTTP_201_CREATED)
def admin_create_plan(
    payload: PricingPlanCreate,
    db: Session = Depends(get_db),
    _: User = Depends(require_roles(Roles.admin)),
):
    existing = db.query(PricingPlan).filter(PricingPlan.slug == payload.slug).first()
    if existing:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Slug already exists")
    plan = PricingPlan(**payload.model_dump())
    db.add(plan)
    db.commit()
    db.refresh(plan)
    return PricingPlanPublic.model_validate(plan)


@router.get("/admin/plans/{plan_id}", response_model=PricingPlanPublic)
def admin_get_plan(
    plan_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(require_roles(Roles.admin)),
):
    plan = db.query(PricingPlan).filter(PricingPlan.id == plan_id).first()
    if not plan:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plan not found")
    return PricingPlanPublic.model_validate(plan)


@router.patch("/admin/plans/{plan_id}", response_model=PricingPlanPublic)
def admin_update_plan(
    plan_id: int,
    payload: PricingPlanUpdate,
    db: Session = Depends(get_db),
    _: User = Depends(require_roles(Roles.admin)),
):
    plan = db.query(PricingPlan).filter(PricingPlan.id == plan_id).first()
    if not plan:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plan not found")
    data = payload.model_dump(exclude_unset=True)
    if "slug" in data:
        other = db.query(PricingPlan).filter(PricingPlan.slug == data["slug"], PricingPlan.id != plan_id).first()
        if other:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Slug already exists")
    for k, v in data.items():
        setattr(plan, k, v)
    db.add(plan)
    db.commit()
    db.refresh(plan)
    return PricingPlanPublic.model_validate(plan)


@router.delete("/admin/plans/{plan_id}", status_code=status.HTTP_204_NO_CONTENT)
def admin_delete_plan(
    plan_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(require_roles(Roles.admin)),
):
    plan = db.query(PricingPlan).filter(PricingPlan.id == plan_id).first()
    if not plan:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plan not found")
    db.delete(plan)
    db.commit()
    return None
