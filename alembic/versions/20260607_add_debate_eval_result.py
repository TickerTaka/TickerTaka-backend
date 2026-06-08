"""add debate_eval_result table

Revision ID: b1c2d3e4f5a6
Revises: a8a60fcd0ed2
Create Date: 2026-06-07
"""
from alembic import op
import sqlalchemy as sa

revision = 'b1c2d3e4f5a6'
down_revision = 'a8a60fcd0ed2'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'debate_eval_result',
        sa.Column('id', sa.dialects.postgresql.UUID(as_uuid=True), server_default=sa.text('gen_random_uuid()'), primary_key=True),
        sa.Column('session_id', sa.dialects.postgresql.UUID(as_uuid=True), sa.ForeignKey('debate_session.id', ondelete='CASCADE'), nullable=False),
        sa.Column('eval_type', sa.String(50), nullable=False),
        sa.Column('score', sa.Float(), nullable=True),
        sa.Column('model_used', sa.String(100), nullable=True),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('eval_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    )
    op.create_index('idx_eval_session', 'debate_eval_result', ['session_id'])
    op.create_index('idx_eval_type',    'debate_eval_result', ['eval_type'])


def downgrade() -> None:
    op.drop_index('idx_eval_type',    table_name='debate_eval_result')
    op.drop_index('idx_eval_session', table_name='debate_eval_result')
    op.drop_table('debate_eval_result')
