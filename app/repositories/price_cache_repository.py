from __future__ import annotations

from collections.abc import Sequence
from datetime import date

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models import PriceCache


class PriceCacheRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get_latest_price_date(self, symbol: str) -> date | None:
        stmt = select(func.max(PriceCache.price_date)).where(PriceCache.symbol == symbol)
        return self.session.scalar(stmt)

    def list_recent(self, symbol: str, limit: int = 260) -> list[PriceCache]:
        stmt = (
            select(PriceCache)
            .where(PriceCache.symbol == symbol)
            .order_by(PriceCache.price_date.desc())
            .limit(limit)
        )
        return list(self.session.scalars(stmt))

    def upsert_many(self, rows: Sequence[dict]) -> tuple[int, int]:
        if not rows:
            return (0, 0)
        inserted = 0
        updated = 0
        for row in rows:
            stmt = insert(PriceCache).values(**row)
            stmt = stmt.on_conflict_do_update(
                constraint="uq_price_cache",
                set_={
                    "open_price": stmt.excluded.open_price,
                    "high_price": stmt.excluded.high_price,
                    "low_price": stmt.excluded.low_price,
                    "close_price": stmt.excluded.close_price,
                    "adjusted_close": stmt.excluded.adjusted_close,
                    "volume": stmt.excluded.volume,
                    "change_rate": stmt.excluded.change_rate,
                    "retrieved_at": stmt.excluded.retrieved_at,
                },
            )
            result = self.session.execute(stmt)
            if result.rowcount:
                inserted += 1
        self.session.flush()
        return inserted, updated

    def trim_rows_for_symbol(self, symbol: str, max_rows: int) -> int:
        rows = list(
            self.session.scalars(
                select(PriceCache).where(PriceCache.symbol == symbol).order_by(PriceCache.price_date.desc())
            )
        )
        overflow = len(rows) - max_rows
        if overflow <= 0:
            return 0
        ids = [row.id for row in rows[-overflow:]]
        self.session.execute(delete(PriceCache).where(PriceCache.id.in_(ids)))
        self.session.flush()
        return overflow
