"""user profile, pricing plans, settings

Revision ID: d1e2f3a4b5c6
Revises: 0fda5fd18c59
Create Date: 2025-02-01

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "d1e2f3a4b5c6"
down_revision: Union[str, Sequence[str], None] = "0fda5fd18c59"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1) Create pricing_plans
    op.create_table(
        "pricing_plans",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("slug", sa.String(100), nullable=False),
        sa.Column("label", sa.String(50), nullable=True),
        sa.Column("price", sa.Float(), nullable=False, server_default="0"),
        sa.Column("credits", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("description", sa.String(255), nullable=True),
        sa.Column("features", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("is_popular", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("display_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_pricing_plans_id"), "pricing_plans", ["id"], unique=False)
    op.create_index(op.f("ix_pricing_plans_slug"), "pricing_plans", ["slug"], unique=True)

    # 2) Add user profile columns and plan_id
    op.add_column("users", sa.Column("phone", sa.String(50), nullable=True))
    op.add_column("users", sa.Column("location", sa.String(255), nullable=True))
    op.add_column("users", sa.Column("linkedin_url", sa.String(512), nullable=True))
    op.add_column("users", sa.Column("portfolio_url", sa.String(512), nullable=True))
    op.add_column("users", sa.Column("plan_id", sa.Integer(), nullable=True))
    op.add_column("users", sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default="false"))
    op.create_foreign_key("fk_users_plan_id", "users", "pricing_plans", ["plan_id"], ["id"], ondelete="SET NULL")

    # 3) Create user_settings
    op.create_table(
        "user_settings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("dark_mode", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("accent_color", sa.String(50), nullable=False, server_default="blue"),
        sa.Column("email_notifications", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("in_app_notifications", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("two_factor_enabled", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_user_settings_id"), "user_settings", ["id"], unique=False)
    op.create_index(op.f("ix_user_settings_user_id"), "user_settings", ["user_id"], unique=True)

    # 4) Add template filters
    op.add_column("templates", sa.Column("style", sa.String(100), nullable=True))
    op.add_column("templates", sa.Column("industry", sa.String(100), nullable=True))

    # 5) Seed default pricing plans
    op.execute(
        """
        INSERT INTO pricing_plans (id, name, slug, label, price, credits, description, features, is_popular, display_order, created_at, updated_at)
        VALUES
        (1, 'Free', 'free', 'Head Start', 0, 5, 'Perfect for getting started.', '["5 AI Credits", "Basic Templates", "Standard Support"]'::jsonb, false, 1, NOW(), NOW()),
        (2, 'Starter', 'starter', NULL, 9.99, 50, 'Ideal for first-time job seekers.', '["50 AI Credits", "All Templates", "Priority Support"]'::jsonb, false, 2, NOW(), NOW()),
        (3, 'Premium', 'premium', 'Most Popular', 29.99, 200, 'For professionals aiming high.', '["200 AI Credits", "AI Cover Letters", "Premium Support"]'::jsonb, true, 3, NOW(), NOW()),
        (4, 'Pro', 'pro', NULL, 49.99, 500, 'For serious career builders.', '["500 AI Credits", "Interview Prep Module", "24/7 VIP Support"]'::jsonb, false, 4, NOW(), NOW());
        """
    )
    op.execute("SELECT setval(pg_get_serial_sequence('pricing_plans', 'id'), 4)")


def downgrade() -> None:
    op.drop_column("templates", "industry")
    op.drop_column("templates", "style")
    op.drop_index(op.f("ix_user_settings_user_id"), table_name="user_settings")
    op.drop_index(op.f("ix_user_settings_id"), table_name="user_settings")
    op.drop_table("user_settings")
    op.drop_constraint("fk_users_plan_id", "users", type_="foreignkey")
    op.drop_column("users", "is_deleted")
    op.drop_column("users", "plan_id")
    op.drop_column("users", "portfolio_url")
    op.drop_column("users", "linkedin_url")
    op.drop_column("users", "location")
    op.drop_column("users", "phone")
    op.drop_index(op.f("ix_pricing_plans_slug"), table_name="pricing_plans")
    op.drop_index(op.f("ix_pricing_plans_id"), table_name="pricing_plans")
    op.drop_table("pricing_plans")
