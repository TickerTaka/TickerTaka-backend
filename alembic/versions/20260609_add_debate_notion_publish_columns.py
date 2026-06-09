"""add notion publish columns to debate_session

Revision ID: c2d3e4f5a6b7
Revises: b1c2d3e4f5a6
Create Date: 2026-06-09
"""

from alembic import op
import sqlalchemy as sa

revision = "c2d3e4f5a6b7"
down_revision = "b1c2d3e4f5a6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("debate_session", sa.Column("notion_page_id", sa.String(length=255), nullable=True))
    op.add_column("debate_session", sa.Column("notion_page_url", sa.String(length=2048), nullable=True))
    op.add_column("debate_session", sa.Column("notion_published_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("debate_session", "notion_published_at")
    op.drop_column("debate_session", "notion_page_url")
    op.drop_column("debate_session", "notion_page_id")
