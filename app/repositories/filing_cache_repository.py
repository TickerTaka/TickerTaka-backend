from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import Select, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models import FilingCache


class FilingCacheRepository:
    """Persistence helpers for filing cache ingestion."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def get_by_receipt_nos(self, receipt_nos: Sequence[str]) -> dict[str, FilingCache]:
        if not receipt_nos:
            return {}
        rows = self.session.scalars(
            select(FilingCache).where(FilingCache.dart_receipt_no.in_(receipt_nos))
        ).all()
        return {row.dart_receipt_no: row for row in rows if row.dart_receipt_no}

    def get_by_ids(self, ids: Sequence[str | UUID]) -> dict[str, FilingCache]:
        if not ids:
            return {}
        normalized_ids = [UUID(str(value)) for value in ids]
        rows = self.session.scalars(
            select(FilingCache).where(FilingCache.id.in_(normalized_ids))
        ).all()
        return {str(row.id): row for row in rows}

    def list_by_symbol(self, symbol: str) -> list[FilingCache]:
        stmt: Select[tuple[FilingCache]] = (
            select(FilingCache)
            .where(FilingCache.symbol == symbol)
            .order_by(FilingCache.disclosed_at.desc().nullslast(), FilingCache.retrieved_at.desc())
        )
        return list(self.session.scalars(stmt))

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

    def delete_expired_rows(self, now: datetime | None = None) -> int:
        return len(self.delete_expired_rows_returning_ids(now=now))

    def delete_expired_rows_returning_ids(self, now: datetime | None = None) -> list[str]:
        cutoff = now or datetime.now(UTC)
        rows = list(
            self.session.scalars(
                select(FilingCache).where(
                    FilingCache.ttl_until.is_not(None),
                    FilingCache.ttl_until < cutoff,
                )
            )
        )
        deleted_ids: list[str] = []
        for row in rows:
            deleted_ids.append(str(row.id))
            self.session.delete(row)
        self.session.flush()
        return deleted_ids

    def trim_rows_for_symbol(self, symbol: str, max_rows: int) -> int:
        return len(self.trim_rows_for_symbol_returning_ids(symbol, max_rows))

    def trim_rows_for_symbol_returning_ids(self, symbol: str, max_rows: int) -> list[str]:
        rows = self.list_by_symbol(symbol)
        overflow = len(rows) - max_rows
        if overflow <= 0:
            return []
        deleted_ids: list[str] = []
        for row in rows[-overflow:]:
            deleted_ids.append(str(row.id))
            self.session.delete(row)
        self.session.flush()
        return deleted_ids
