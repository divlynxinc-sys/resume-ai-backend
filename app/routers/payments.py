from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.config import Roles, polar_settings
from app.core.security import require_roles
from app.database.connection import get_db
from app.models.pricing_plan import PricingPlan
from app.models.user import User
from app.utils.polar_client import get_polar, get_product_id_for_slug

router = APIRouter(prefix="/payments", tags=["Payments"])


class PolarCheckoutRequest(BaseModel):
    plan_slug: str = Field(..., description="Slug of the pricing plan, e.g. 'weekly'")


class PolarCheckoutResponse(BaseModel):
    checkout_url: str
    checkout_id: str


@router.post("/polar/checkout", response_model=PolarCheckoutResponse)
def create_polar_checkout(
    payload: PolarCheckoutRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(Roles.user, Roles.admin)),
):
    """
    Create a Polar checkout session for the authenticated user.

    Returns a hosted checkout URL the frontend should redirect the browser to.
    The Polar webhook (/webhooks/polar) is the source of truth for activating
    the subscription once payment succeeds.
    """
    plan = (
        db.query(PricingPlan)
        .filter(PricingPlan.slug == payload.plan_slug, PricingPlan.is_active == True)  # noqa: E712
        .first()
    )
    if not plan:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plan not found")

    product_id = get_product_id_for_slug(plan.slug)
    if not product_id:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Polar product not configured for plan '{plan.slug}'",
        )

    try:
        polar = get_polar()
        checkout = polar.checkouts.create(
            request={
                "products": [product_id],
                "success_url": polar_settings.success_url,
                "customer_email": user.email,
                "external_customer_id": str(user.id),
                "metadata": {
                    "user_id": str(user.id),
                    "plan_id": str(plan.id),
                    "plan_slug": plan.slug,
                },
            }
        )
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        )
    except Exception as exc:  # polar SDK errors
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Polar checkout failed: {exc}",
        )

    return PolarCheckoutResponse(checkout_url=checkout.url, checkout_id=checkout.id)
