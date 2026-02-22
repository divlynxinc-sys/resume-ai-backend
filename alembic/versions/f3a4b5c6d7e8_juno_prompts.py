"""juno example prompts

Revision ID: f3a4b5c6d7e8
Revises: e2f3a4b5c6d7
Create Date: 2025-02-01

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "f3a4b5c6d7e8"
down_revision: Union[str, Sequence[str], None] = "e2f3a4b5c6d7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "juno_prompts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("text", sa.String(500), nullable=False),
        sa.Column("category", sa.String(100), nullable=True),
        sa.Column("display_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_juno_prompts_id"), "juno_prompts", ["id"], unique=False)
    op.create_index(op.f("ix_juno_prompts_category"), "juno_prompts", ["category"], unique=False)

    op.execute(
        """
        INSERT INTO juno_prompts (text, category, display_order, created_at, updated_at)
        VALUES
        ('Make my summary more professional', 'summary', 1, NOW(), NOW()),
        ('Improve my work experience descriptions', 'experience', 2, NOW(), NOW()),
        ('Suggest skills for my industry', 'skills', 3, NOW(), NOW()),
        ('Write a strong career objective', 'summary', 4, NOW(), NOW()),
        ('Optimize my resume for ATS', 'ats', 5, NOW(), NOW()),
        ('Improve my formatting and structure', 'formatting', 6, NOW(), NOW()),
        ('Create a cover letter for my resume', 'cover_letter', 7, NOW(), NOW()),
        ('Enhance my achievements with metrics', 'experience', 8, NOW(), NOW());
        """
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_juno_prompts_category"), table_name="juno_prompts")
    op.drop_index(op.f("ix_juno_prompts_id"), table_name="juno_prompts")
    op.drop_table("juno_prompts")
