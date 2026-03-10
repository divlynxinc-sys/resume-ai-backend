"""otp login support

Revision ID: e5b6c7d8e9f0
Revises: d1e2f3a4b5c6
Create Date: 2026-03-10

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e5b6c7d8e9f0"
down_revision: Union[str, Sequence[str], None] = "d1e2f3a4b5c6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add OTP login fields to users."""
    op.add_column("users", sa.Column("otp_code", sa.String(length=6), nullable=True))
    op.add_column("users", sa.Column("otp_expires_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    """Remove OTP login fields from users."""
    op.drop_column("users", "otp_expires_at")
    op.drop_column("users", "otp_code")

