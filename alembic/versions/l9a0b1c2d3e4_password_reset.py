"""password reset tokens

Revision ID: l9a0b1c2d3e4
Revises: k8f9a0b1c2d3
Create Date: 2026-11-20

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "l9a0b1c2d3e4"
down_revision: Union[str, Sequence[str], None] = "k8f9a0b1c2d3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add forgot-password token fields to users."""
    op.add_column("users", sa.Column("password_reset_token_hash", sa.String(length=64), nullable=True))
    op.add_column("users", sa.Column("password_reset_expires_at", sa.DateTime(timezone=True), nullable=True))
    # Per-account cooldown so /auth/forgot-password can't be used to spam an inbox.
    op.add_column("users", sa.Column("password_reset_requested_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    """Remove forgot-password token fields from users."""
    op.drop_column("users", "password_reset_requested_at")
    op.drop_column("users", "password_reset_expires_at")
    op.drop_column("users", "password_reset_token_hash")
