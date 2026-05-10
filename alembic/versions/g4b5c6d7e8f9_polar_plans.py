"""seed weekly/monthly/three_months pricing plans for Polar

Revision ID: g4b5c6d7e8f9
Revises: f3a4b5c6d7e8
Create Date: 2026-05-10

"""
from typing import Sequence, Union

from alembic import op


revision: str = "g4b5c6d7e8f9"
down_revision: Union[str, Sequence[str], None] = "f3a4b5c6d7e8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        INSERT INTO pricing_plans
            (name, slug, label, price, credits, description, features, is_popular, display_order, is_active, created_at, updated_at)
        VALUES
        ('Weekly', 'weekly', NULL, 14.99, 0,
         'Try the full toolkit on a short-term sprint.',
         '["AI-tailored resumes", "AI-tailored cover letters", "HR email drafts", "Q&A answers", "PDF & DOCX downloads"]'::jsonb,
         false, 5, true, NOW(), NOW()),
        ('Monthly', 'monthly', 'Most Popular', 35.99, 0,
         'Best for an active job search across many roles.',
         '["AI-tailored resumes", "AI-tailored cover letters", "HR email drafts", "Q&A answers", "PDF & DOCX downloads"]'::jsonb,
         true, 6, true, NOW(), NOW()),
        ('3 Months', 'three_months', NULL, 79.99, 0,
         'Best value for a longer search — save vs monthly.',
         '["AI-tailored resumes", "AI-tailored cover letters", "HR email drafts", "Q&A answers", "PDF & DOCX downloads"]'::jsonb,
         false, 7, true, NOW(), NOW())
        ON CONFLICT (slug) DO NOTHING;
        """
    )


def downgrade() -> None:
    op.execute(
        "DELETE FROM pricing_plans WHERE slug IN ('weekly', 'monthly', 'three_months');"
    )
