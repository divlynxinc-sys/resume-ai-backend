from logging.config import fileConfig
import os
from urllib.parse import quote_plus

from sqlalchemy import engine_from_config
from sqlalchemy import pool

from alembic import context

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Import models and metadata
from app.database.connection import Base  # noqa: E402
from app.models import user as user_model  # ensure model is imported for autogenerate
from app.models import session_tracking as session_tracking_model  # ensure model is imported
from app.models import resume as resume_model  # ensure model is imported
from app.models import template as template_model  # ensure model is imported
from app.models import pricing_plan as pricing_plan_model  # ensure model is imported
from app.models import user_settings as user_settings_model  # ensure model is imported
from app.models import help_article as help_article_model  # ensure model is imported
from app.models import ats_score as ats_score_model  # ensure model is imported

target_metadata = Base.metadata


def _build_db_url_from_parts() -> str:
    name = os.getenv("DB_NAME", "resume_ai")
    user = os.getenv("DB_USER", "postgres")
    password = os.getenv("DB_PASSWORD", "postgres")
    host = os.getenv("DB_HOST", "localhost")
    port = os.getenv("DB_PORT", "5432")
    return f"postgresql+psycopg2://{user}:{quote_plus(password)}@{host}:{port}/{name}"


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    url = config.get_main_option("sqlalchemy.url")
    # Override placeholder or empty URL with env-based URL
    if not url or url.startswith("driver://"):
        url = os.getenv("DATABASE_URL") or _build_db_url_from_parts()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    section = config.get_section(config.config_ini_section, {})
    url_in_ini = section.get("sqlalchemy.url", "")
    if (not url_in_ini) or url_in_ini.startswith("driver://"):
        env_url = os.getenv("DATABASE_URL") or _build_db_url_from_parts()
        section["sqlalchemy.url"] = env_url
    connectable = engine_from_config(
        section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection, target_metadata=target_metadata
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
