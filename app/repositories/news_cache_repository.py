from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import Select, delete, distinct, select
from sqlalchemy.orm import Session

from app.models import NewsCache


class NewsCacheRepository:
    """Persistence helpers for news cache ingestion."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def get_by_source_urls(self, source_urls: Sequence[str]) -> dict[str, NewsCache]:
        if not source_urls:
            return {}
        rows = self.session.scalars(
            select(NewsCache).where(NewsCache.source_url.in_(source_urls))
        ).all()
        return {row.source_url: row for row in rows}

    def get_by_ids(self, ids: Sequence[str | UUID]) -> dict[str, NewsCache]:
        if not ids:
            return {}
        normalized_ids = [UUID(str(value)) for value in ids]
        rows = self.session.scalars(
            select(NewsCache).where(NewsCache.id.in_(normalized_ids))
        ).all()
        return {str(row.id): row for row in rows}

    def get_recent_by_symbol(self, symbol: str, since_hours: int = 24) -> list[NewsCache]:
        since = datetime.now(UTC) - timedelta(hours=since_hours)
        stmt: Select[tuple[NewsCache]] = (
            select(NewsCache)
            .where(NewsCache.symbol == symbol, NewsCache.retrieved_at >= since)
            .order_by(NewsCache.published_at.desc().nullslast(), NewsCache.retrieved_at.desc())
        )
        return list(self.session.scalars(stmt))

    def save(self, row: NewsCache) -> NewsCache:
        self.session.add(row)
        self.session.flush()
        return row

    def list_symbols_with_cache(self) -> list[str]:
        stmt = select(distinct(NewsCache.symbol)).order_by(NewsCache.symbol)
        return list(self.session.scalars(stmt))

    def list_by_symbol(self, symbol: str) -> list[NewsCache]:
        stmt: Select[tuple[NewsCache]] = (
            select(NewsCache)
            .where(NewsCache.symbol == symbol)
            .order_by(NewsCache.published_at.desc().nullslast(), NewsCache.retrieved_at.desc())
        )
        return list(self.session.scalars(stmt))

    def delete_expired_rows(self, now: datetime | None = None) -> int:
        return len(self.delete_expired_rows_returning_ids(now=now))

    def delete_expired_rows_returning_ids(self, now: datetime | None = None) -> list[str]:
        cutoff = now or datetime.now(UTC)
        rows = list(
            self.session.scalars(
                select(NewsCache).where(
                    NewsCache.ttl_until.is_not(None),
                    NewsCache.ttl_until < cutoff,
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
        rows = list(
            self.session.scalars(
                select(NewsCache)
                .where(NewsCache.symbol == symbol)
                .order_by(NewsCache.published_at.desc().nullslast(), NewsCache.retrieved_at.desc())
            )
        )
        overflow = len(rows) - max_rows
        if overflow <= 0:
            return []
        deleted_ids: list[str] = []
        for row in rows[-overflow:]:
            deleted_ids.append(str(row.id))
            self.session.delete(row)
        self.session.flush()
        return deleted_ids
