from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status, Body
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.core.config import Roles
from app.core.security import require_roles, get_current_user
from app.database.connection import get_db
from app.models.resume import Resume
from app.models.user import User
from app.schemas.resume_schema import ResumeCreate, ResumeDetail, ResumeItem, ResumeUpdate, ResumeSection, ResumeContent

router = APIRouter(prefix="/resumes", tags=["Resumes"])


def _default_content() -> dict:
    return ResumeContent().model_dump()


@router.post("", response_model=ResumeDetail, status_code=status.HTTP_201_CREATED)
def create_resume(
    payload: ResumeCreate,
    mode: str = Query(default="scratch", description="scratch|empty"),
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(Roles.user, Roles.admin)),
):
    now = datetime.now(timezone.utc)
    next_local_id = (db.query(func.coalesce(func.max(Resume.user_resume_id), 0)).filter(Resume.user_id == user.id).scalar() or 0) + 1
    instance = Resume(
        user_id=user.id,
        user_resume_id=next_local_id,
        title=payload.title,
        template_id=payload.template_id,
        status="draft",
        content=(payload.content.model_dump() if isinstance(payload.content, ResumeContent) else payload.content) if payload.content else (_default_content() if mode == "scratch" else {}),
        created_at=now,
        updated_at=now,
    )
    db.add(instance)
    db.commit()
    db.refresh(instance)
    return ResumeDetail(
        id=instance.user_resume_id,
        title=instance.title,
        template_id=instance.template_id,
        status=instance.status,
        content=instance.content,
        created_at=instance.created_at,
        updated_at=instance.updated_at,
    )


@router.get("", response_model=List[ResumeItem])
def list_resumes(
    q: Optional[str] = Query(default=None, description="Search in title"),
    limit: int = Query(default=10, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(Roles.user, Roles.admin)),
):
    query = db.query(Resume).filter(Resume.user_id == user.id, Resume.is_deleted == False)  # noqa: E712
    if q:
        query = query.filter(Resume.title.ilike(f"%{q}%"))
    rows = query.order_by(Resume.updated_at.desc()).limit(limit).offset(offset).all()
    return [ResumeItem(id=r.user_resume_id, title=r.title, updated_at=r.updated_at, status=r.status) for r in rows]


def _get_owned_resume(db: Session, user_id: int, resume_id: int) -> Resume:
    r = db.query(Resume).filter(Resume.user_id == user_id, Resume.user_resume_id == resume_id, Resume.is_deleted == False).first()  # noqa: E712
    if not r:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Resume not found")
    return r


@router.get("/{resume_id}", response_model=ResumeDetail)
def get_resume(resume_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    r = _get_owned_resume(db, user.id, resume_id)
    return ResumeDetail(
        id=r.user_resume_id,
        title=r.title,
        template_id=r.template_id,
        status=r.status,
        content=r.content,
        created_at=r.created_at,
        updated_at=r.updated_at,
    )


@router.patch("/{resume_id}", response_model=ResumeDetail)
def update_resume(resume_id: int, payload: ResumeUpdate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    r = _get_owned_resume(db, user.id, resume_id)
    if payload.title is not None:
        r.title = payload.title
    if payload.template_id is not None:
        r.template_id = payload.template_id
    if payload.status is not None:
        r.status = payload.status
    if payload.content is not None:
        r.content = payload.content
    r.updated_at = datetime.now(timezone.utc)
    db.add(r)
    db.commit()
    db.refresh(r)
    return ResumeDetail(
        id=r.user_resume_id,
        title=r.title,
        template_id=r.template_id,
        status=r.status,
        content=r.content,
        created_at=r.created_at,
        updated_at=r.updated_at,
    )


@router.post("/{resume_id}/duplicate", response_model=ResumeDetail, status_code=status.HTTP_201_CREATED)
def duplicate_resume(resume_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    r = _get_owned_resume(db, user.id, resume_id)
    now = datetime.now(timezone.utc)
    next_local_id = (db.query(func.coalesce(func.max(Resume.user_resume_id), 0)).filter(Resume.user_id == user.id).scalar() or 0) + 1
    clone = Resume(
        user_id=user.id,
        user_resume_id=next_local_id,
        title=f"{r.title} (Copy)",
        template_id=r.template_id,
        status="draft",
        content=r.content,
        created_at=now,
        updated_at=now,
    )
    db.add(clone)
    db.commit()
    db.refresh(clone)
    return ResumeDetail(
        id=clone.user_resume_id,
        title=clone.title,
        template_id=clone.template_id,
        status=clone.status,
        content=clone.content,
        created_at=clone.created_at,
        updated_at=clone.updated_at,
    )


@router.delete("/{resume_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_resume(resume_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    r = _get_owned_resume(db, user.id, resume_id)
    r.is_deleted = True
    r.updated_at = datetime.now(timezone.utc)
    db.add(r)
    db.commit()
    return


@router.get("/{resume_id}/content")
def get_content(resume_id: int, section: Optional[ResumeSection] = Query(default=None), db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    r = _get_owned_resume(db, user.id, resume_id)
    if not section:
        return r.content or {}
    return (r.content or {}).get(section.value, {} if section != ResumeSection.skills else [])


@router.patch("/{resume_id}/content")
def patch_content(
    resume_id: int,
    section: ResumeSection = Query(...),
    payload: dict | list | str | None = Body(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    r = _get_owned_resume(db, user.id, resume_id)
    content = dict(r.content or {})
    if payload is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No content provided")

    # Validate minimal required fields per section
    if section == ResumeSection.info:
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="info must be an object")
        required = ["full_name", "email", "phone"]
        missing = [k for k in required if not payload.get(k)]
        if missing:
            raise HTTPException(status_code=400, detail=f"Missing fields: {', '.join(missing)}")
    elif section == ResumeSection.experience:
        if not isinstance(payload, list):
            raise HTTPException(status_code=400, detail="experience must be a list")
    elif section == ResumeSection.education:
        if not isinstance(payload, list):
            raise HTTPException(status_code=400, detail="education must be a list")
        for idx, item in enumerate(payload):
            for k in ["school", "degree", "start_date", "end_date"]:
                if not isinstance(item, dict) or not item.get(k):
                    raise HTTPException(status_code=400, detail=f"education[{idx}].{k} is required")
    elif section == ResumeSection.skills:
        if not isinstance(payload, list):
            raise HTTPException(status_code=400, detail="skills must be a list of strings")
    elif section == ResumeSection.summary:
        if not isinstance(payload, str):
            raise HTTPException(status_code=400, detail="summary must be a string")
    elif section == ResumeSection.job_description:
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="job_description must be an object")
        for k in ["job_title", "company", "description"]:
            if not payload.get(k):
                raise HTTPException(status_code=400, detail=f"job_description.{k} is required")

    content[section.value] = payload
    r.content = content
    r.updated_at = datetime.now(timezone.utc)
    db.add(r)
    db.commit()
    return {"message": "Updated", "section": section.value}


@router.get("/sections/definition")
def section_definition(section: ResumeSection):
    # Minimal field definitions to drive a generic form in the UI
    definitions = {
        "info": [
            {"key": "full_name", "label": "Full Name", "type": "text", "required": True, "placeholder": "Alex Doe"},
            {"key": "email", "label": "Email", "type": "email", "required": True},
            {"key": "phone", "label": "Phone", "type": "text", "required": True},
            {"key": "location", "label": "Location", "type": "text"},
            {"key": "linkedin_url", "label": "LinkedIn Profile URL", "type": "url"},
            {"key": "portfolio_url", "label": "Portfolio URL", "type": "url"},
        ],
        "experience": [
            {"key": "role", "label": "Role", "type": "text"},
            {"key": "company", "label": "Company", "type": "text"},
            {"key": "start_date", "label": "Start Date", "type": "text"},
            {"key": "end_date", "label": "End Date", "type": "text"},
            {"key": "location", "label": "Location", "type": "text"},
            {"key": "description", "label": "Description", "type": "textarea"},
        ],
        "education": [
            {"key": "school", "label": "School", "type": "text", "required": True},
            {"key": "degree", "label": "Degree", "type": "text", "required": True},
            {"key": "start_date", "label": "Start Date", "type": "text", "required": True},
            {"key": "end_date", "label": "End Date", "type": "text", "required": True},
            {"key": "location", "label": "Location", "type": "text"},
            {"key": "field_of_study", "label": "Field of Study", "type": "text"},
        ],
        "skills": [
            {"key": "skills", "label": "Skills", "type": "tags", "required": False},
        ],
        "summary": [
            {"key": "summary", "label": "Professional Summary", "type": "textarea"},
        ],
        "job_description": [
            {"key": "job_title", "label": "Job Title", "type": "text", "required": True},
            {"key": "company", "label": "Company", "type": "text", "required": True},
            {"key": "location", "label": "Location", "type": "text"},
            {"key": "description", "label": "Job Description / Key Requirements", "type": "textarea", "required": True},
        ],
        "custom": [
            {"key": "custom", "label": "Custom", "type": "json"},
        ],
    }
    return {"section": section.value, "fields": definitions[section.value]}

