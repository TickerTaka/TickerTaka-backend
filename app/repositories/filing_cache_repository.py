from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models import FilingCache


class FilingCacheRepository:
    """Persistence helpers for DART filing cache ingestion."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def get_by_receipt_nos(self, receipt_nos: Sequence[str]) -> dict[str, FilingCache]:
        if not receipt_nos:
            return {}
        rows = self.session.scalars(
            select(FilingCache).where(FilingCache.dart_receipt_no.in_(receipt_nos))
        ).all()
        return {
            row.dart_receipt_no: row
            for row in rows
            if row.dart_receipt_no
        }

    def upsert_filing(
        self,
        *,
        symbol: str,
        filing_title: str,
        filing_type: str | None,
        dart_receipt_no: str,
        source_url: str,
        disclosed_at: datetime | None,
        ttl_until: datetime,
    ) -> UUID | None:
        stmt = insert(FilingCache).values(
            symbol=symbol,
            filing_title=filing_title,
            filing_type=filing_type,
            content=None,
            summary=None,
            dart_receipt_no=dart_receipt_no,
            source_url=source_url,
            disclosed_at=disclosed_at,
            ttl_until=ttl_until,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[FilingCache.dart_receipt_no],
            set_={
                "symbol": stmt.excluded.symbol,
                "filing_title": stmt.excluded.filing_title,
                "filing_type": stmt.excluded.filing_type,
                "source_url": stmt.excluded.source_url,
                "disclosed_at": stmt.excluded.disclosed_at,
                "retrieved_at": text("now()"),
                "ttl_until": stmt.excluded.ttl_until,
            },
        ).returning(FilingCache.id)
        row = self.session.execute(stmt).first()
        self.session.flush()
        return row[0] if row else None
