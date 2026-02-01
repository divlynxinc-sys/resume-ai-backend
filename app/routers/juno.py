from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.config import Roles
from app.core.security import require_roles
from app.database.connection import get_db
from app.models.juno_prompt import JunoPrompt
from app.models.user import User
from app.schemas.juno_schema import JunoPromptCreate, JunoPromptPublic, JunoPromptUpdate

router = APIRouter(prefix="/juno", tags=["Juno AI Assistant"])


@router.get("/prompts", response_model=List[JunoPromptPublic])
def list_example_prompts(
    category: Optional[str] = Query(default=None, description="Filter by category"),
    db: Session = Depends(get_db),
    _: User = Depends(require_roles(Roles.user, Roles.admin)),
):
    """Fetch example prompts for Juno AI Assistant - view in the UI."""
    query = db.query(JunoPrompt).filter(JunoPrompt.is_active == True)  # noqa: E712
    if category:
        query = query.filter(JunoPrompt.category == category)
    prompts = query.order_by(JunoPrompt.display_order, JunoPrompt.id).all()
    return [JunoPromptPublic.model_validate(p) for p in prompts]


# --- Admin CRUD ---


@router.get("/admin/prompts", response_model=List[JunoPromptPublic])
def admin_list_prompts(
    category: Optional[str] = Query(default=None),
    include_inactive: bool = Query(default=False),
    db: Session = Depends(get_db),
    _: User = Depends(require_roles(Roles.admin)),
):
    """Admin: list all prompts including inactive."""
    query = db.query(JunoPrompt)
    if not include_inactive:
        query = query.filter(JunoPrompt.is_active == True)  # noqa: E712
    if category:
        query = query.filter(JunoPrompt.category == category)
    prompts = query.order_by(JunoPrompt.display_order, JunoPrompt.id).all()
    return [JunoPromptPublic.model_validate(p) for p in prompts]


@router.post("/admin/prompts", response_model=JunoPromptPublic, status_code=status.HTTP_201_CREATED)
def admin_create_prompt(
    payload: JunoPromptCreate,
    db: Session = Depends(get_db),
    _: User = Depends(require_roles(Roles.admin)),
):
    """Admin: create a new example prompt."""
    prompt = JunoPrompt(**payload.model_dump())
    db.add(prompt)
    db.commit()
    db.refresh(prompt)
    return JunoPromptPublic.model_validate(prompt)


@router.get("/admin/prompts/{prompt_id}", response_model=JunoPromptPublic)
def admin_get_prompt(
    prompt_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(require_roles(Roles.admin)),
):
    """Admin: get a single prompt."""
    prompt = db.query(JunoPrompt).filter(JunoPrompt.id == prompt_id).first()
    if not prompt:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Prompt not found")
    return JunoPromptPublic.model_validate(prompt)


@router.patch("/admin/prompts/{prompt_id}", response_model=JunoPromptPublic)
def admin_update_prompt(
    prompt_id: int,
    payload: JunoPromptUpdate,
    db: Session = Depends(get_db),
    _: User = Depends(require_roles(Roles.admin)),
):
    """Admin: update a prompt."""
    prompt = db.query(JunoPrompt).filter(JunoPrompt.id == prompt_id).first()
    if not prompt:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Prompt not found")
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(prompt, k, v)
    db.add(prompt)
    db.commit()
    db.refresh(prompt)
    return JunoPromptPublic.model_validate(prompt)


@router.delete("/admin/prompts/{prompt_id}", status_code=status.HTTP_204_NO_CONTENT)
def admin_delete_prompt(
    prompt_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(require_roles(Roles.admin)),
):
    """Admin: delete a prompt."""
    prompt = db.query(JunoPrompt).filter(JunoPrompt.id == prompt_id).first()
    if not prompt:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Prompt not found")
    db.delete(prompt)
    db.commit()
    return None
