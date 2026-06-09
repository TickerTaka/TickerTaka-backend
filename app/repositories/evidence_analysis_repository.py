from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import Select, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models import EvidenceAnalysis


class EvidenceAnalysisRepository:
    """Persistence helpers for structured news/filing analysis."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def upsert_analysis(
        self,
        *,
        source_type: str,
        source_id: str | UUID,
        symbol: str,
        sentiment: str,
        impact_score: int,
        confidence: float | Decimal | None,
        summary: str,
        key_points: list[str],
        risks: list[str],
        model_name: str,
        prompt_version: str,
        event_type: str | None = None,
        evidence: list[str] | None = None,
        raw_response: dict[str, Any] | None = None,
    ) -> EvidenceAnalysis:
        values = {
            "source_type": source_type,
            "source_id": UUID(str(source_id)),
            "symbol": symbol,
            "sentiment": sentiment,
            "impact_score": impact_score,
            "confidence": confidence,
            "event_type": event_type,
            "summary": summary,
            "key_points": key_points,
            "risks": risks,
            "evidence": evidence or [],
            "model_name": model_name,
            "prompt_version": prompt_version,
            "raw_response": raw_response,
        }
        stmt = insert(EvidenceAnalysis).values(**values)
        stmt = stmt.on_conflict_do_update(
            constraint="uq_evidence_analysis_source_version",
            set_={
                "symbol": stmt.excluded.symbol,
                "sentiment": stmt.excluded.sentiment,
                "impact_score": stmt.excluded.impact_score,
                "confidence": stmt.excluded.confidence,
                "event_type": stmt.excluded.event_type,
                "summary": stmt.excluded.summary,
                "key_points": stmt.excluded.key_points,
                "risks": stmt.excluded.risks,
                "evidence": stmt.excluded.evidence,
                "model_name": stmt.excluded.model_name,
                "raw_response": stmt.excluded.raw_response,
                "analyzed_at": text("now()"),
                "updated_at": text("now()"),
            },
        ).returning(EvidenceAnalysis)
        row = self.session.execute(stmt).scalar_one()
        self.session.flush()
        return row

    def get_by_sources(
        self,
        source_type: str,
        source_ids: Sequence[str | UUID],
        *,
        prompt_version: str | None = None,
    ) -> dict[str, EvidenceAnalysis]:
        if not source_ids:
            return {}
        normalized_ids = [UUID(str(value)) for value in source_ids]
        stmt: Select[tuple[EvidenceAnalysis]] = select(EvidenceAnalysis).where(
            EvidenceAnalysis.source_type == source_type,
            EvidenceAnalysis.source_id.in_(normalized_ids),
        )
        if prompt_version is not None:
            stmt = stmt.where(EvidenceAnalysis.prompt_version == prompt_version)
        stmt = stmt.order_by(EvidenceAnalysis.analyzed_at.desc())
        rows = self.session.scalars(stmt).all()
        result: dict[str, EvidenceAnalysis] = {}
        for row in rows:
            result.setdefault(str(row.source_id), row)
        return result

    def list_recent_by_symbol(self, symbol: str, limit: int = 20) -> list[EvidenceAnalysis]:
        stmt: Select[tuple[EvidenceAnalysis]] = (
            select(EvidenceAnalysis)
            .where(EvidenceAnalysis.symbol == symbol)
            .order_by(EvidenceAnalysis.analyzed_at.desc())
            .limit(limit)
        )
        return list(self.session.scalars(stmt))
