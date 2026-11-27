"""Persist account onboarding completion.

Revision ID: m0a1b2c3d4e5
Revises: l9a0b1c2d3e4
"""
from alembic import op
import sqlalchemy as sa

revision = "m0a1b2c3d4e5"
down_revision = "l9a0b1c2d3e4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("onboarding_completed", sa.Boolean(), nullable=False, server_default=sa.true()))
    op.alter_column("users", "onboarding_completed", server_default=sa.false())


def downgrade() -> None:
    op.drop_column("users", "onboarding_completed")
