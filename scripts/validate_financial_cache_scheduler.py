from __future__ import annotations

from contextlib import contextmanager

import app.domain.financial_cache_scheduler as scheduler_module
from app.domain.financial_ingestion import SyncFinancialResult
from app.domain.financial_cache_scheduler import FinancialCacheSchedulerService


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


class FakeFinancialIngestionService:
    MAX_CACHE_ROWS = 60

    def __init__(self, session, redis_client=None) -> None:
        self.session = session
        self.redis_client = redis_client
        self.repo = type("Repo", (), {"trim_rows_for_symbol": lambda self, symbol, max_rows: 1 if symbol == "000020" else 0})()

    def sync_financials_for_ticker(self, symbol: str, mode: str = "refresh", force: bool = False, backfill_years=None) -> SyncFinancialResult:
        if symbol == "000040":
            raise RuntimeError("forced failure")
        return SyncFinancialResult(
            fetched_periods=4,
            saved_rows=4,
            trimmed_rows=0,
        )


@contextmanager
def fake_session_scope():
    yield object()


def main() -> None:
    original_repo = scheduler_module.WatchlistRepository
    original_service = scheduler_module.FinancialIngestionService
    try:
        scheduler_module.WatchlistRepository = FakeWatchlistRepository
        scheduler_module.FinancialIngestionService = FakeFinancialIngestionService

        scheduler = FinancialCacheSchedulerService(redis_client=FakeRedis(), session_factory=fake_session_scope)
        refresh = scheduler.run_watchlist_refresh()
        cleanup = scheduler.run_cleanup()
        assert refresh.processed_symbols == 1
        assert refresh.failed_symbols == 1
        assert refresh.fetched_periods == 4
        assert refresh.saved_rows == 4
        assert cleanup.processed_symbols == 2
        assert cleanup.trimmed_rows == 1
        print(
            {
                "refresh_processed": refresh.processed_symbols,
                "refresh_failed": refresh.failed_symbols,
                "cleanup_processed": cleanup.processed_symbols,
                "cleanup_trimmed_rows": cleanup.trimmed_rows,
            }
        )
    finally:
        scheduler_module.WatchlistRepository = original_repo
        scheduler_module.FinancialIngestionService = original_service


if __name__ == "__main__":
    main()
