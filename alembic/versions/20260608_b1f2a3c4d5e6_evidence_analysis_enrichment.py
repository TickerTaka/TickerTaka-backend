"""evidence_analysis enrichment: event_type/evidence columns + analysis_jobs queue

Revision ID: b1f2a3c4d5e6
Revises: c2d3e4f5a6b7
Create Date: 2026-06-08

이전까지 evidence_analysis 는 alembic 에 누락되어 scripts/create_evidence_analysis_table.sql
수동 SQL 로만 프로비저닝됐다. 이 마이그레이션은:
  1) evidence_analysis 를 IF NOT EXISTS 로 보장(이미 존재하는 환경 안전)
  2) event_type / evidence 컬럼 추가
  3) Qwen 비동기 보강용 analysis_jobs 큐 테이블 생성
"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b1f2a3c4d5e6"
down_revision: Union[str, None] = "c2d3e4f5a6b7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1) evidence_analysis 테이블 보장 (수동 SQL 로 이미 만들어진 환경에서는 no-op)
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS evidence_analysis (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            source_type VARCHAR(30) NOT NULL,
            source_id UUID NOT NULL,
            symbol VARCHAR(30) NOT NULL,
            sentiment VARCHAR(20) NOT NULL,
            impact_score INTEGER NOT NULL,
            confidence NUMERIC(4, 3),
            summary TEXT NOT NULL,
            key_points JSONB NOT NULL DEFAULT '[]'::jsonb,
            risks JSONB NOT NULL DEFAULT '[]'::jsonb,
            model_name VARCHAR(150) NOT NULL,
            prompt_version VARCHAR(50) NOT NULL,
            raw_response JSONB,
            analyzed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_evidence_analysis_source_version UNIQUE (source_type, source_id, prompt_version)
        );
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_evidence_analysis_symbol ON evidence_analysis (symbol)")
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_evidence_analysis_source ON evidence_analysis (source_type, source_id)"
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_evidence_analysis_sentiment ON evidence_analysis (sentiment)")

    # 2) event_type / evidence 컬럼 추가
    op.execute("ALTER TABLE evidence_analysis ADD COLUMN IF NOT EXISTS event_type VARCHAR(40)")
    op.execute(
        "ALTER TABLE evidence_analysis ADD COLUMN IF NOT EXISTS evidence JSONB NOT NULL DEFAULT '[]'::jsonb"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_evidence_analysis_event_type ON evidence_analysis (event_type)"
    )

    # 3) analysis_jobs 큐 테이블
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS analysis_jobs (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            source_type VARCHAR(30) NOT NULL,
            source_id UUID NOT NULL,
            symbol VARCHAR(30) NOT NULL,
            prompt_version VARCHAR(50) NOT NULL,
            status VARCHAR(20) NOT NULL DEFAULT 'pending',
            attempts INTEGER NOT NULL DEFAULT 0,
            last_error TEXT,
            locked_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_analysis_jobs_source_version UNIQUE (source_type, source_id, prompt_version)
        );
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_analysis_jobs_status ON analysis_jobs (status, created_at)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS analysis_jobs")
    op.execute("DROP INDEX IF EXISTS idx_evidence_analysis_event_type")
    op.execute("ALTER TABLE evidence_analysis DROP COLUMN IF EXISTS evidence")
    op.execute("ALTER TABLE evidence_analysis DROP COLUMN IF EXISTS event_type")
