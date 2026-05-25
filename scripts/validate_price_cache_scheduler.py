from __future__ import annotations

from contextlib import contextmanager

import app.domain.price_cache_scheduler as scheduler_module
from app.domain.price_ingestion import SyncPriceResult
from app.domain.price_cache_scheduler import PriceCacheSchedulerService


class FakeRedis:
    def __init__(self) -> None:
        self.data: dict[str, str] = {}

    def set(self, key, value, ex=None):
        self.data[key] = str(value)
        return True


class FakeWatchlistRepository:
    def __init__(self, session) -> None:
        self.session = session

    def list_distinct_symbols(self) -> list[str]:
        return ["000020", "000040"]


class FakePriceIngestionService:
    def __init__(self, session, redis_client=None) -> None:
        self.session = session
        self.redis_client = redis_client

    def sync_prices_for_ticker(self, symbol: str, mode: str = "refresh", force: bool = False, backfill_days=None) -> SyncPriceResult:
        if symbol == "000040":
            raise RuntimeError("forced failure")
        return SyncPriceResult(
            fetched_count=5,
            inserted_count=3,
            updated_count=1,
            indicators_count=5,
        )


@contextmanager
def fake_session_scope():
    yield object()


def main() -> None:
    original_repo = scheduler_module.WatchlistRepository
    original_service = scheduler_module.PriceIngestionService
    try:
        scheduler_module.WatchlistRepository = FakeWatchlistRepository
        scheduler_module.PriceIngestionService = FakePriceIngestionService
        scheduler = PriceCacheSchedulerService(redis_client=FakeRedis(), session_factory=fake_session_scope)
        result = scheduler.run_watchlist_refresh()
        assert result.processed_symbols == 1
        assert result.failed_symbols == 1
        assert result.fetched_rows == 5
        assert result.inserted_rows == 3
        assert result.updated_rows == 1
        assert result.indicator_rows == 5
        print(
            {
                "processed_symbols": result.processed_symbols,
                "failed_symbols": result.failed_symbols,
                "fetched_rows": result.fetched_rows,
                "inserted_rows": result.inserted_rows,
                "updated_rows": result.updated_rows,
                "indicator_rows": result.indicator_rows,
            }
        )
    finally:
        scheduler_module.WatchlistRepository = original_repo
        scheduler_module.PriceIngestionService = original_service


if __name__ == "__main__":
    main()
