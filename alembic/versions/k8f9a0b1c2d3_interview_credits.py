"""interview_credits: prepaid AI Interview credits (balance, ledger, per-session charge state)

Revision ID: k8f9a0b1c2d3
Revises: j7e8f9a0b1c2
Create Date: 2026-09-14

AI Interviews move from "paid plan + hidden weekly cap" to prepaid credits: one
credit per interview (report included), sold as one-time Polar packs to any
signed-in user. Additive only — every existing user starts at 0 credits.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "k8f9a0b1c2d3"
down_revision: Union[str, Sequence[str], None] = "j7e8f9a0b1c2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("interview_credits", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "interview_sessions",
        sa.Column("credit_status", sa.String(length=16), nullable=True),
    )
    op.create_table(
        "interview_credit_transactions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("delta", sa.Integer(), nullable=False),
        sa.Column("balance_after", sa.Integer(), nullable=True),
        sa.Column("pack", sa.String(length=32), nullable=True),
        sa.Column("polar_order_id", sa.String(length=255), nullable=True),
        sa.Column("amount_cents", sa.Integer(), nullable=True),
        sa.Column("interview_session_id", sa.String(length=36), nullable=True),
        sa.Column("note", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("polar_order_id", "kind", name="uq_interview_credit_order_kind"),
        sa.UniqueConstraint("interview_session_id", "kind", name="uq_interview_credit_session_kind"),
    )
    op.create_index(
        "ix_interview_credit_transactions_user_id",
        "interview_credit_transactions",
        ["user_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_interview_credit_transactions_user_id", table_name="interview_credit_transactions")
    op.drop_table("interview_credit_transactions")
    op.drop_column("interview_sessions", "credit_status")
    op.drop_column("users", "interview_credits")
