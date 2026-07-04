"""subscription lifecycle: state, polar ids, period dates (refund/reserve/reactivate)

Revision ID: i6d7e8f9a0b1
Revises: h5c6d7e8f9a0
Create Date: 2026-07-04

Adds local subscription-lifecycle tracking to `users` so we can enforce the
money-back window (100% refund on early cancel), block-immediately-on-cancel,
and free re-subscribe until the original period end. See
app.core.config.SubscriptionState and app.utils.subscription.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "i6d7e8f9a0b1"
down_revision: Union[str, Sequence[str], None] = "h5c6d7e8f9a0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("subscription_state", sa.String(length=32), nullable=True))
    op.add_column("users", sa.Column("polar_subscription_id", sa.String(length=255), nullable=True))
    op.add_column("users", sa.Column("polar_order_id", sa.String(length=255), nullable=True))
    op.add_column("users", sa.Column("polar_order_amount", sa.Integer(), nullable=True))
    op.add_column("users", sa.Column("subscription_started_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("users", sa.Column("subscription_period_end", sa.DateTime(timezone=True), nullable=True))

    # Backfill: anyone already on a plan is treated as active so they don't lose
    # access. started_at/period_end stay NULL (unknown) — that means no refund
    # window and no reservation boundary until Polar refreshes the dates.
    op.execute(
        "UPDATE users SET subscription_state = 'active' WHERE plan_id IS NOT NULL"
    )


def downgrade() -> None:
    op.drop_column("users", "subscription_period_end")
    op.drop_column("users", "subscription_started_at")
    op.drop_column("users", "polar_order_amount")
    op.drop_column("users", "polar_order_id")
    op.drop_column("users", "polar_subscription_id")
    op.drop_column("users", "subscription_state")
