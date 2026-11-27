"""Persist signup challenges, delivery limits, and account creation grants.

Revision ID: n1b2c3d4e5f6
Revises: m0a1b2c3d4e5
"""
from alembic import op
import sqlalchemy as sa

revision = "n1b2c3d4e5f6"
down_revision = "m0a1b2c3d4e5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "signup_verifications",
        sa.Column("email", sa.String(255), primary_key=True),
        sa.Column("pending_challenge_id", sa.String(24)),
        sa.Column("challenge_id", sa.String(24)),
        sa.Column("code_hash", sa.String(64)),
        sa.Column("code_expires_at", sa.DateTime(timezone=True)),
        sa.Column("failures", sa.Integer(), nullable=False),
        sa.Column("send_times", sa.JSON(), nullable=False),
        sa.Column("grant_hash", sa.String(64)),
        sa.Column("grant_expires_at", sa.DateTime(timezone=True)),
        sa.Column("last_activity", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_signup_verifications_last_activity", "signup_verifications", ["last_activity"])


def downgrade() -> None:
    op.drop_index("ix_signup_verifications_last_activity", "signup_verifications")
    op.drop_table("signup_verifications")
