import os
from typing import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase

from app.core.env import load_env
from urllib.parse import quote_plus

load_env()


def _build_db_url_from_parts() -> str:
    name = os.getenv("DB_NAME", "resume_ai")
    user = os.getenv("DB_USER", "postgres")
    password = os.getenv("DB_PASSWORD", "postgres")
    host = os.getenv("DB_HOST", "localhost")
    port = os.getenv("DB_PORT", "5432")
    return f"postgresql+psycopg2://{user}:{quote_plus(password)}@{host}:{port}/{name}"


DATABASE_URL = os.getenv("DATABASE_URL") or _build_db_url_from_parts()

# Neon (and most serverless/pooled Postgres) closes idle connections after a short
# window. Without pre-ping, SQLAlchemy hands out a dead connection on the first
# request after an idle period, which surfaces as intermittent 500s / "socket hang up"
# / "connection reset" errors. pool_pre_ping validates the connection before use and
# pool_recycle proactively drops connections older than the recycle window.
engine = create_engine(
    DATABASE_URL,
    future=True,
    pool_pre_ping=True,
    pool_recycle=int(os.getenv("DB_POOL_RECYCLE_SECONDS", "280")),
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine, future=True)


class Base(DeclarativeBase):
    pass


def get_db() -> Generator:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
