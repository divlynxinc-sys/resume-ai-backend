from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session
from sqlalchemy import or_

from app.core.config import Roles
from app.core.security import require_roles
from app.database.connection import get_db
from app.models.help_article import HelpArticle, HelpTopic
from app.models.user import User
from app.schemas.help_schema import (
    HelpArticleCreate,
    HelpArticleDetail,
    HelpArticleListItem,
    HelpArticleUpdate,
    HelpTopicCreate,
    HelpTopicPublic,
    HelpTopicUpdate,
)

router = APIRouter(prefix="/help", tags=["Help Center"])


# --- Public endpoints (authenticated users) ---


@router.get("/topics", response_model=List[HelpTopicPublic])
def list_topics(db: Session = Depends(get_db), _: User = Depends(require_roles(Roles.user, Roles.admin))):
    """Browse by topic - list all help topics."""
    topics = db.query(HelpTopic).order_by(HelpTopic.display_order, HelpTopic.id).all()
    return [HelpTopicPublic.model_validate(t) for t in topics]


@router.get("/articles", response_model=List[HelpArticleListItem])
def list_articles(
    q: Optional[str] = Query(default=None, description="Search for answers"),
    topic_id: Optional[int] = Query(default=None, description="Filter by topic"),
    featured_only: bool = Query(default=False, description="Featured articles only"),
    faq_only: bool = Query(default=False, description="FAQs only"),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    _: User = Depends(require_roles(Roles.user, Roles.admin)),
):
    """List articles with optional search and filters. No N+1: single query."""
    query = db.query(HelpArticle)
    if topic_id:
        query = query.filter(HelpArticle.topic_id == topic_id)
    if featured_only:
        query = query.filter(HelpArticle.is_featured == True)  # noqa: E712
    if faq_only:
        query = query.filter(HelpArticle.is_faq == True)  # noqa: E712
    if q:
        search = f"%{q}%"
        query = query.filter(
            or_(
                HelpArticle.title.ilike(search),
                HelpArticle.excerpt.ilike(search),
                HelpArticle.content.ilike(search),
            )
        )
    articles = query.order_by(HelpArticle.display_order, HelpArticle.created_at.desc()).limit(limit).offset(offset).all()
    return [HelpArticleListItem.model_validate(a) for a in articles]


@router.get("/articles/featured", response_model=List[HelpArticleListItem])
def list_featured_articles(
    limit: int = Query(default=10, ge=1, le=50),
    db: Session = Depends(get_db),
    _: User = Depends(require_roles(Roles.user, Roles.admin)),
):
    """Featured articles for the help center landing."""
    articles = (
        db.query(HelpArticle)
        .filter(HelpArticle.is_featured == True)  # noqa: E712
        .order_by(HelpArticle.display_order, HelpArticle.created_at.desc())
        .limit(limit)
        .all()
    )
    return [HelpArticleListItem.model_validate(a) for a in articles]


@router.get("/articles/faqs", response_model=List[HelpArticleListItem])
def list_faqs(
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    _: User = Depends(require_roles(Roles.user, Roles.admin)),
):
    """FAQs list."""
    articles = (
        db.query(HelpArticle)
        .filter(HelpArticle.is_faq == True)  # noqa: E712
        .order_by(HelpArticle.display_order, HelpArticle.created_at.desc())
        .limit(limit)
        .all()
    )
    return [HelpArticleListItem.model_validate(a) for a in articles]


@router.get("/articles/{slug}", response_model=HelpArticleDetail)
def get_article(
    slug: str,
    db: Session = Depends(get_db),
    _: User = Depends(require_roles(Roles.user, Roles.admin)),
):
    """Get full article by slug (for Read More)."""
    article = db.query(HelpArticle).filter(HelpArticle.slug == slug).first()
    if not article:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Article not found")
    topic = article.topic
    return HelpArticleDetail(
        id=article.id,
        topic_id=article.topic_id,
        topic_name=topic.name if topic else None,
        title=article.title,
        slug=article.slug,
        excerpt=article.excerpt,
        content=article.content,
        is_featured=article.is_featured,
        is_faq=article.is_faq,
        display_order=article.display_order,
        created_at=article.created_at,
        updated_at=article.updated_at,
    )


# --- Admin CRUD ---


@router.get("/admin/topics", response_model=List[HelpTopicPublic])
def admin_list_topics(db: Session = Depends(get_db), _: User = Depends(require_roles(Roles.admin))):
    topics = db.query(HelpTopic).order_by(HelpTopic.display_order, HelpTopic.id).all()
    return [HelpTopicPublic.model_validate(t) for t in topics]


@router.post("/admin/topics", response_model=HelpTopicPublic, status_code=status.HTTP_201_CREATED)
def admin_create_topic(payload: HelpTopicCreate, db: Session = Depends(get_db), _: User = Depends(require_roles(Roles.admin))):
    existing = db.query(HelpTopic).filter(HelpTopic.slug == payload.slug).first()
    if existing:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Slug already exists")
    topic = HelpTopic(**payload.model_dump())
    db.add(topic)
    db.commit()
    db.refresh(topic)
    return HelpTopicPublic.model_validate(topic)


@router.patch("/admin/topics/{topic_id}", response_model=HelpTopicPublic)
def admin_update_topic(
    topic_id: int,
    payload: HelpTopicUpdate,
    db: Session = Depends(get_db),
    _: User = Depends(require_roles(Roles.admin)),
):
    topic = db.query(HelpTopic).filter(HelpTopic.id == topic_id).first()
    if not topic:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Topic not found")
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(topic, k, v)
    db.add(topic)
    db.commit()
    db.refresh(topic)
    return HelpTopicPublic.model_validate(topic)


@router.delete("/admin/topics/{topic_id}", status_code=status.HTTP_204_NO_CONTENT)
def admin_delete_topic(topic_id: int, db: Session = Depends(get_db), _: User = Depends(require_roles(Roles.admin))):
    topic = db.query(HelpTopic).filter(HelpTopic.id == topic_id).first()
    if not topic:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Topic not found")
    db.delete(topic)
    db.commit()
    return None


@router.get("/admin/articles", response_model=List[HelpArticleListItem])
def admin_list_articles(
    topic_id: Optional[int] = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    _: User = Depends(require_roles(Roles.admin)),
):
    query = db.query(HelpArticle)
    if topic_id:
        query = query.filter(HelpArticle.topic_id == topic_id)
    articles = query.order_by(HelpArticle.display_order, HelpArticle.created_at.desc()).limit(limit).offset(offset).all()
    return [HelpArticleListItem.model_validate(a) for a in articles]


@router.post("/admin/articles", response_model=HelpArticleDetail, status_code=status.HTTP_201_CREATED)
def admin_create_article(payload: HelpArticleCreate, db: Session = Depends(get_db), _: User = Depends(require_roles(Roles.admin))):
    existing = db.query(HelpArticle).filter(HelpArticle.slug == payload.slug).first()
    if existing:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Slug already exists")
    topic = db.query(HelpTopic).filter(HelpTopic.id == payload.topic_id).first()
    if not topic:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Topic not found")
    article = HelpArticle(**payload.model_dump())
    db.add(article)
    db.commit()
    db.refresh(article)
    return HelpArticleDetail(
        id=article.id,
        topic_id=article.topic_id,
        topic_name=topic.name,
        title=article.title,
        slug=article.slug,
        excerpt=article.excerpt,
        content=article.content,
        is_featured=article.is_featured,
        is_faq=article.is_faq,
        display_order=article.display_order,
        created_at=article.created_at,
        updated_at=article.updated_at,
    )


@router.patch("/admin/articles/{article_id}", response_model=HelpArticleDetail)
def admin_update_article(
    article_id: int,
    payload: HelpArticleUpdate,
    db: Session = Depends(get_db),
    _: User = Depends(require_roles(Roles.admin)),
):
    article = db.query(HelpArticle).filter(HelpArticle.id == article_id).first()
    if not article:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Article not found")
    data = payload.model_dump(exclude_unset=True)
    if "topic_id" in data:
        topic = db.query(HelpTopic).filter(HelpTopic.id == data["topic_id"]).first()
        if not topic:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Topic not found")
    if "slug" in data:
        other = db.query(HelpArticle).filter(HelpArticle.slug == data["slug"], HelpArticle.id != article_id).first()
        if other:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Slug already exists")
    for k, v in data.items():
        setattr(article, k, v)
    db.add(article)
    db.commit()
    db.refresh(article)
    topic = article.topic
    return HelpArticleDetail(
        id=article.id,
        topic_id=article.topic_id,
        topic_name=topic.name if topic else None,
        title=article.title,
        slug=article.slug,
        excerpt=article.excerpt,
        content=article.content,
        is_featured=article.is_featured,
        is_faq=article.is_faq,
        display_order=article.display_order,
        created_at=article.created_at,
        updated_at=article.updated_at,
    )


@router.delete("/admin/articles/{article_id}", status_code=status.HTTP_204_NO_CONTENT)
def admin_delete_article(article_id: int, db: Session = Depends(get_db), _: User = Depends(require_roles(Roles.admin))):
    article = db.query(HelpArticle).filter(HelpArticle.id == article_id).first()
    if not article:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Article not found")
    db.delete(article)
    db.commit()
    return None
