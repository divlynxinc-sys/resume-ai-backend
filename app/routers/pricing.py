from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.config import Roles
from app.core.security import require_roles
from app.database.connection import get_db
from app.models.pricing_plan import PricingPlan
from app.models.user import User
from app.schemas.pricing_schema import (
    AddonOptionsResponse,
    AddonOption,
    AddonPurchaseRequest,
    AddonPurchaseResponse,
    PricingPlanCreate,
    PricingPlanPublic,
    PricingPlanUpdate,
)

router = APIRouter(prefix="/pricing", tags=["Pricing"])


PREMIUM_PLAN_SLUG = "premium"
FREE_PLAN_SLUG = "free"


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


@router.get("/plans/{plan_id}/addons", response_model=AddonOptionsResponse)
def get_addon_options(
    plan_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(Roles.user, Roles.admin)),
):
    """
    Return available add-on credit bundles for the subscribed plan.

    Frontend can use this to show the popup when a user runs out of credits.
    We currently base prices on the plan's price-per-credit ratio.
    """
    if user.plan_id != plan_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You can only purchase add-ons for your active plan",
        )

    plan = (
        db.query(PricingPlan)
        .filter(PricingPlan.id == plan_id, PricingPlan.is_active == True)  # noqa: E712
        .first()
    )
    if not plan:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plan not found")

    if plan.slug != PREMIUM_PLAN_SLUG:
        # For now we only support add-ons on the Premium plan
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Add-ons are only available on the Premium plan",
        )

    if plan.credits <= 0 or plan.price <= 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Plan is misconfigured for add-ons",
        )

    price_per_credit = plan.price / float(plan.credits)

    # Default UI bundles: +10, +15, +20 credits
    credits_bundles = [10, 15, 20]
    options: List[AddonOption] = []
    for bundle in credits_bundles:
        price = round(price_per_credit * bundle, 2)
        options.append(AddonOption(credits=bundle, price=price))

    return AddonOptionsResponse(
        plan_id=plan.id,
        plan_slug=plan.slug,
        base_price_per_credit=round(price_per_credit, 4),
        options=options,
    )


@router.post("/plans/addons/purchase", response_model=AddonPurchaseResponse)
def purchase_addon_credits(
    payload: AddonPurchaseRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(Roles.user, Roles.admin)),
):
    """
    "Purchase" add-on credits for the current plan.

    This endpoint assumes the actual payment is handled by the frontend / payment
    provider. Once payment is confirmed, call this endpoint to credit the account.
    """
    if not user.plan_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You must have an active plan before purchasing add-ons",
        )
    if user.plan_id != payload.plan_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Plan mismatch for add-on purchase",
        )

    plan = (
        db.query(PricingPlan)
        .filter(PricingPlan.id == payload.plan_id, PricingPlan.is_active == True)  # noqa: E712
        .first()
    )
    if not plan:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plan not found")

    if plan.slug != PREMIUM_PLAN_SLUG:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Add-ons are only available on the Premium plan",
        )

    if plan.credits <= 0 or plan.price <= 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Plan is misconfigured for add-ons",
        )

    price_per_credit = plan.price / float(plan.credits)
    price_charged = round(price_per_credit * payload.credits, 2)

    # Enforce that the base 15 plan credits stay intact (cannot be "minused").
    # We only ever increase credits_remaining here, never decrease.
    current_credits = int(user.credits_remaining or 0)
    new_total = current_credits + payload.credits

    user.credits_remaining = new_total
    db.add(user)
    db.commit()
    db.refresh(user)

    return AddonPurchaseResponse(
        credits_added=payload.credits,
        total_credits_after_purchase=new_total,
        price_charged=price_charged,
    )


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
