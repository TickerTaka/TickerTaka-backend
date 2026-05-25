from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models import FinancialCache


class FinancialCacheRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def list_recent(self, symbol: str, limit: int = 20) -> list[FinancialCache]:
        stmt = (
            select(FinancialCache)
            .where(FinancialCache.symbol == symbol)
            .order_by(FinancialCache.fiscal_year.desc(), FinancialCache.fiscal_quarter.desc().nullslast())
            .limit(limit)
        )
        return list(self.session.scalars(stmt))

    def upsert_many(self, rows: Sequence[dict]) -> int:
        if not rows:
            return 0
        count = 0
        for row in rows:
            stmt = insert(FinancialCache).values(**row)
            stmt = stmt.on_conflict_do_update(
                constraint="uq_financial_cache",
                set_={
                    "revenue": stmt.excluded.revenue,
                    "operating_profit": stmt.excluded.operating_profit,
                    "net_income": stmt.excluded.net_income,
                    "total_assets": stmt.excluded.total_assets,
                    "total_liabilities": stmt.excluded.total_liabilities,
                    "total_equity": stmt.excluded.total_equity,
                    "per": stmt.excluded.per,
                    "pbr": stmt.excluded.pbr,
                    "roe": stmt.excluded.roe,
                    "debt_ratio": stmt.excluded.debt_ratio,
                    "source_url": stmt.excluded.source_url,
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
                select(FinancialCache)
                .where(FinancialCache.symbol == symbol)
                .order_by(FinancialCache.fiscal_year.desc(), FinancialCache.fiscal_quarter.desc().nullslast())
            )
        )
        overflow = len(rows) - max_rows
        if overflow <= 0:
            return 0
        ids = [row.id for row in rows[-overflow:]]
        self.session.execute(delete(FinancialCache).where(FinancialCache.id.in_(ids)))
        self.session.flush()
        return overflow
