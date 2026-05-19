from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

from sqlalchemy import Select, case, select
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

    def trim_rows_for_symbol(self, symbol: str, max_rows: int) -> int:
        rows = list(
            self.session.scalars(
                select(NewsCache)
                .where(NewsCache.symbol == symbol)
                .order_by(NewsCache.published_at.desc().nullslast(), NewsCache.retrieved_at.desc())
            )
        )
        overflow = len(rows) - max_rows
        if overflow <= 0:
            return 0
        for row in rows[-overflow:]:
            self.session.delete(row)
        self.session.flush()
        return overflow

    def trim_content_for_symbol(self, symbol: str, max_content_rows: int) -> int:
        rows = list(
            self.session.scalars(
                select(NewsCache)
                .where(NewsCache.symbol == symbol, NewsCache.content.is_not(None))
                .order_by(NewsCache.published_at.asc().nullslast(), NewsCache.retrieved_at.asc())
            )
        )
        overflow = len(rows) - max_content_rows
        if overflow <= 0:
            return 0
        for row in rows[:overflow]:
            row.content = None
        self.session.flush()
        return overflow
