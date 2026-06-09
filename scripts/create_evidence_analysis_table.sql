CREATE TABLE IF NOT EXISTS evidence_analysis (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_type VARCHAR(30) NOT NULL,
    source_id UUID NOT NULL,
    symbol VARCHAR(30) NOT NULL,
    sentiment VARCHAR(20) NOT NULL,
    impact_score INTEGER NOT NULL,
    confidence NUMERIC(4, 3),
    event_type VARCHAR(40),
    summary TEXT NOT NULL,
    key_points JSONB NOT NULL DEFAULT '[]'::jsonb,
    risks JSONB NOT NULL DEFAULT '[]'::jsonb,
    evidence JSONB NOT NULL DEFAULT '[]'::jsonb,
    model_name VARCHAR(150) NOT NULL,
    prompt_version VARCHAR(50) NOT NULL,
    raw_response JSONB,
    analyzed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_evidence_analysis_source_version UNIQUE (source_type, source_id, prompt_version)
);

CREATE INDEX IF NOT EXISTS idx_evidence_analysis_symbol ON evidence_analysis (symbol);
CREATE INDEX IF NOT EXISTS idx_evidence_analysis_source ON evidence_analysis (source_type, source_id);
CREATE INDEX IF NOT EXISTS idx_evidence_analysis_sentiment ON evidence_analysis (sentiment);
CREATE INDEX IF NOT EXISTS idx_evidence_analysis_event_type ON evidence_analysis (event_type);

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

CREATE INDEX IF NOT EXISTS idx_analysis_jobs_status ON analysis_jobs (status, created_at);
