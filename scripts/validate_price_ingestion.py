from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

import app.domain.price_ingestion as price_module
from app.external.krx_client import DailyPriceRecord
from app.models import MarketType


class FakeRedis:
    def __init__(self) -> None:
        self.data: dict[str, str] = {}

    def set(self, key, value, nx=False, ex=None):
        if nx and key in self.data:
            return False
        self.data[key] = str(value)
        return True

    def get(self, key):
        return self.data.get(key)

    def delete(self, key):
        self.data.pop(key, None)


class FakeSession:
    def __init__(self, symbol: str) -> None:
        self.symbol = symbol
        self.saved_price_rows = []

    def get(self, model, key):
        if key != self.symbol:
            return None
        return type("Ticker", (), {"symbol": key, "market": MarketType.KOSPI})()

    def commit(self):
        return None

    def flush(self):
        return None

    def scalars(self, stmt):
        return list(self.saved_price_rows)


class FakePriceRepo:
    def __init__(self, session) -> None:
        self.session = session
        self.rows: list[dict] = []

    def get_latest_price_date(self, symbol: str):
        if not self.rows:
            return None
        return max(row["price_date"] for row in self.rows)

    def upsert_many(self, rows):
        self.rows = list(rows)
        self.session.saved_price_rows = [
            type(
                "PriceRow",
                (),
                {
                    "symbol": row["symbol"],
                    "price_date": row["price_date"],
                    "close_price": row["close_price"],
                    "volume": row["volume"],
                },
            )()
            for row in rows
        ]
        return len(rows), 0

    def trim_rows_for_symbol(self, symbol: str, max_rows: int) -> int:
        overflow = max(0, len(self.rows) - max_rows)
        if overflow:
            self.rows = self.rows[-max_rows:]
        return overflow


class FakeIndicatorRepo:
    def __init__(self, session) -> None:
        self.rows: list[dict] = []

    def upsert_many(self, rows):
        self.rows = list(rows)
        return len(rows)

    def trim_rows_for_symbol(self, symbol: str, max_rows: int) -> int:
        overflow = max(0, len(self.rows) - max_rows)
        if overflow:
            self.rows = self.rows[-max_rows:]
        return overflow


@dataclass(slots=True)
class FakePriceClient:
    records: list[DailyPriceRecord]

    def fetch_daily_prices(self, symbol: str, *, start_date: date, end_date: date, market) -> list[DailyPriceRecord]:
        return [row for row in self.records if start_date <= row.price_date <= end_date]


def main() -> None:
    symbol = "000020"
    base = date(2026, 1, 1)
    records = [
        DailyPriceRecord(
            symbol=symbol,
            price_date=base + timedelta(days=index),
            open_price=100 + index,
            high_price=101 + index,
            low_price=99 + index,
            close_price=100 + index,
            volume=1000 + index,
        )
        for index in range(140)
    ]

    original_price_repo = price_module.PriceCacheRepository
    original_indicator_repo = price_module.TechnicalIndicatorCacheRepository
    try:
        price_module.PriceCacheRepository = FakePriceRepo
        price_module.TechnicalIndicatorCacheRepository = FakeIndicatorRepo
        service = price_module.PriceIngestionService(
            FakeSession(symbol),
            price_client=FakePriceClient(records),
            redis_client=FakeRedis(),
        )
        result = service.sync_prices_for_ticker(symbol, force=True, backfill_days=200)
        print(
            {
                "fetched": result.fetched_count,
                "inserted": result.inserted_count,
                "updated": result.updated_count,
                "indicators": result.indicators_count,
                "trimmed_price_rows": result.trimmed_price_rows,
                "trimmed_indicator_rows": result.trimmed_indicator_rows,
            }
        )
    finally:
        price_module.PriceCacheRepository = original_price_repo
        price_module.TechnicalIndicatorCacheRepository = original_indicator_repo


if __name__ == "__main__":
    main()
