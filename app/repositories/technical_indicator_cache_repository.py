from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models import TechnicalIndicatorCache


class TechnicalIndicatorCacheRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def upsert_many(self, rows: Sequence[dict]) -> int:
        if not rows:
            return 0
        count = 0
        for row in rows:
            stmt = insert(TechnicalIndicatorCache).values(**row)
            stmt = stmt.on_conflict_do_update(
                constraint="uq_technical_indicator",
                set_={
                    "ma20": stmt.excluded.ma20,
                    "ma60": stmt.excluded.ma60,
                    "ma120": stmt.excluded.ma120,
                    "rsi14": stmt.excluded.rsi14,
                    "macd": stmt.excluded.macd,
                    "macd_signal": stmt.excluded.macd_signal,
                    "macd_hist": stmt.excluded.macd_hist,
                    "volume_ma20": stmt.excluded.volume_ma20,
                    "retrieved_at": stmt.excluded.retrieved_at,
                },
            )
            result = self.session.execute(stmt)
            if result.rowcount:
                count += 1
        self.session.flush()
        return count

    def trim_rows_for_symbol(self, symbol: str, max_rows: int) -> int:
        rows = list(
            self.session.scalars(
                select(TechnicalIndicatorCache)
                .where(TechnicalIndicatorCache.symbol == symbol)
                .order_by(TechnicalIndicatorCache.indicator_date.desc())
            )
        )
        overflow = len(rows) - max_rows
        if overflow <= 0:
            return 0
        ids = [row.id for row in rows[-overflow:]]
        self.session.execute(delete(TechnicalIndicatorCache).where(TechnicalIndicatorCache.id.in_(ids)))
        self.session.flush()
        return overflow
