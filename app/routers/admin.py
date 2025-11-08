from fastapi import APIRouter, Depends

from app.core.config import Roles
from app.core.security import require_roles


router = APIRouter(prefix="/admin", tags=["Admin"])


@router.get("/health")
def admin_health(_: dict = Depends(require_roles(Roles.admin))):
    return {"status": "ok"}

