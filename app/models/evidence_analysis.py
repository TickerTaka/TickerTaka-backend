from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import DateTime, Index, Integer, Numeric, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class EvidenceAnalysis(Base):
    """Structured investment-oriented analysis for news and filing evidence."""

    __tablename__ = "evidence_analysis"
    __table_args__ = (
        UniqueConstraint("source_type", "source_id", "prompt_version", name="uq_evidence_analysis_source_version"),
        Index("idx_evidence_analysis_symbol", "symbol"),
        Index("idx_evidence_analysis_source", "source_type", "source_id"),
        Index("idx_evidence_analysis_sentiment", "sentiment"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    source_type: Mapped[str] = mapped_column(String(30), nullable=False)
    source_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    symbol: Mapped[str] = mapped_column(String(30), nullable=False)
    sentiment: Mapped[str] = mapped_column(String(20), nullable=False)
    impact_score: Mapped[int] = mapped_column(Integer, nullable=False)
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(4, 3))
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    key_points: Mapped[list[str]] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    risks: Mapped[list[str]] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    model_name: Mapped[str] = mapped_column(String(150), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(50), nullable=False)
    raw_response: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    analyzed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
        server_onupdate=text("now()"),
    )
