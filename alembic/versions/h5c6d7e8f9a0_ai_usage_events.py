"""ai_usage_events: hidden per-user weekly AI usage caps

Revision ID: h5c6d7e8f9a0
Revises: g4b5c6d7e8f9, a1b2c3d4e5f6
Create Date: 2026-07-01

Merges the two existing heads (polar_plans + google_auth) into a single head and
adds the anti-abuse usage-tracking table.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "h5c6d7e8f9a0"
down_revision: Union[str, Sequence[str], None] = ("g4b5c6d7e8f9", "a1b2c3d4e5f6")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ai_usage_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("feature", sa.String(50), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_ai_usage_events_id"), "ai_usage_events", ["id"], unique=False)
    op.create_index(op.f("ix_ai_usage_events_user_id"), "ai_usage_events", ["user_id"], unique=False)
    op.create_index(
        "ix_ai_usage_user_feature_time",
        "ai_usage_events",
        ["user_id", "feature", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_ai_usage_user_feature_time", table_name="ai_usage_events")
    op.drop_index(op.f("ix_ai_usage_events_user_id"), table_name="ai_usage_events")
    op.drop_index(op.f("ix_ai_usage_events_id"), table_name="ai_usage_events")
    op.drop_table("ai_usage_events")
