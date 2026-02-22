"""help center, articles, ATS score

Revision ID: e2f3a4b5c6d7
Revises: d1e2f3a4b5c6
Create Date: 2025-02-01

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "e2f3a4b5c6d7"
down_revision: Union[str, Sequence[str], None] = "d1e2f3a4b5c6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "help_topics",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("slug", sa.String(100), nullable=False),
        sa.Column("description", sa.String(255), nullable=True),
        sa.Column("icon", sa.String(50), nullable=True),
        sa.Column("display_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_help_topics_id"), "help_topics", ["id"], unique=False)
    op.create_index(op.f("ix_help_topics_slug"), "help_topics", ["slug"], unique=True)

    op.create_table(
        "help_articles",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("topic_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("slug", sa.String(255), nullable=False),
        sa.Column("excerpt", sa.String(500), nullable=True),
        sa.Column("content", sa.Text(), nullable=False, server_default=""),
        sa.Column("is_featured", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("is_faq", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("display_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["topic_id"], ["help_topics.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_help_articles_id"), "help_articles", ["id"], unique=False)
    op.create_index(op.f("ix_help_articles_slug"), "help_articles", ["slug"], unique=True)
    op.create_index(op.f("ix_help_articles_topic_id"), "help_articles", ["topic_id"], unique=False)

    op.create_table(
        "resume_ats_scores",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("resume_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("overall_score", sa.Integer(), nullable=False),
        sa.Column("max_score", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("category_scores", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("recommendations", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("analyzed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["resume_id"], ["resumes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_resume_ats_scores_id"), "resume_ats_scores", ["id"], unique=False)
    op.create_index(op.f("ix_resume_ats_scores_resume_id"), "resume_ats_scores", ["resume_id"], unique=False)
    op.create_index(op.f("ix_resume_ats_scores_user_id"), "resume_ats_scores", ["user_id"], unique=False)

    # Seed help topics and sample articles
    op.execute(
        """
        INSERT INTO help_topics (id, name, slug, description, icon, display_order, created_at, updated_at)
        VALUES
        (1, 'Getting Started', 'getting-started', 'Learn the basics of using ResumeAI.', 'lightning', 1, NOW(), NOW()),
        (2, 'Account Management', 'account-management', 'Manage your account settings and preferences.', 'person', 2, NOW(), NOW()),
        (3, 'Resume Editing', 'resume-editing', 'Edit and customize your resume content.', 'pencil', 3, NOW(), NOW()),
        (4, 'Templates & Design', 'templates-design', 'Explore and apply different resume templates.', 'grid', 4, NOW(), NOW()),
        (5, 'Troubleshooting', 'troubleshooting', 'Solve common issues and errors.', 'wrench', 5, NOW(), NOW()),
        (6, 'Billing & Subscriptions', 'billing-subscriptions', 'Manage your subscription and payment details.', 'document', 6, NOW(), NOW());
        """
    )
    op.execute("SELECT setval(pg_get_serial_sequence('help_topics', 'id'), 6)")

    op.execute(
        """
        INSERT INTO help_articles (topic_id, title, slug, excerpt, content, is_featured, is_faq, display_order, created_at, updated_at)
        VALUES
        (1, 'How to create a standout resume', 'how-to-create-standout-resume',
         'Learn the key elements of a compelling resume that will impress recruiters.',
         'Full content: Key elements include clear structure, action verbs, quantified achievements, and ATS-friendly formatting.', true, true, 1, NOW(), NOW()),
        (3, 'Tips for writing a strong cover letter', 'tips-strong-cover-letter',
         'Discover how to craft a cover letter that complements your resume and highlights your skills.',
         'Full content: Personalize for each role, match keywords, and tell your story concisely.', true, true, 2, NOW(), NOW()),
        (5, 'Common resume mistakes to avoid', 'common-resume-mistakes',
         'Identify and correct common errors that can negatively impact your resume''s effectiveness.',
         'Full content: Avoid typos, generic objectives, inconsistent formatting, and missing keywords.', true, true, 3, NOW(), NOW());
        """
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_resume_ats_scores_user_id"), table_name="resume_ats_scores")
    op.drop_index(op.f("ix_resume_ats_scores_resume_id"), table_name="resume_ats_scores")
    op.drop_index(op.f("ix_resume_ats_scores_id"), table_name="resume_ats_scores")
    op.drop_table("resume_ats_scores")
    op.drop_index(op.f("ix_help_articles_topic_id"), table_name="help_articles")
    op.drop_index(op.f("ix_help_articles_slug"), table_name="help_articles")
    op.drop_index(op.f("ix_help_articles_id"), table_name="help_articles")
    op.drop_table("help_articles")
    op.drop_index(op.f("ix_help_topics_slug"), table_name="help_topics")
    op.drop_index(op.f("ix_help_topics_id"), table_name="help_topics")
    op.drop_table("help_topics")
