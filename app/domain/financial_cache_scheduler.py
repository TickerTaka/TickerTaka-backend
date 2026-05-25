from __future__ import annotations

from dataclasses import dataclass
import logging

from app.config import get_settings
from app.core.db import session_scope
from app.core.redis import build_redis_client, make_key
from app.domain.financial_ingestion import FinancialIngestionService
from app.repositories.watchlist_repository import WatchlistRepository

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class FinancialSweepResult:
    processed_symbols: int = 0
    fetched_periods: int = 0
    saved_rows: int = 0
    trimmed_rows: int = 0
    failed_symbols: int = 0


class FinancialCacheSchedulerService:
    def __init__(self, redis_client=None, session_factory=session_scope) -> None:
        self.redis_client = redis_client or build_redis_client(get_settings().redis_url)
        self.session_factory = session_factory

    def run_watchlist_refresh(self) -> FinancialSweepResult:
        result = FinancialSweepResult()
        with self.session_factory() as session:
            symbols = WatchlistRepository(session).list_distinct_symbols()
        for symbol in symbols:
            try:
                with self.session_factory() as session:
                    sync_result = FinancialIngestionService(session, redis_client=self.redis_client).sync_financials_for_ticker(
                        symbol,
                        mode="refresh",
                    )
                result.processed_symbols += 1
                result.fetched_periods += sync_result.fetched_periods
                result.saved_rows += sync_result.saved_rows
            except Exception:
                result.failed_symbols += 1
                logger.exception("scheduled financial refresh failed for %s", symbol)
        if self.redis_client is not None:
            self.redis_client.set(make_key("financial-sync", "sweep", "last-run", "refresh"), "1", ex=86400 * 30)
        return result

    def run_cleanup(self) -> FinancialSweepResult:
        result = FinancialSweepResult()
        with self.session_factory() as session:
            symbols = WatchlistRepository(session).list_distinct_symbols()
        for symbol in symbols:
            try:
                with self.session_factory() as session:
                    service = FinancialIngestionService(session, redis_client=self.redis_client)
                    result.trimmed_rows += service.repo.trim_rows_for_symbol(symbol, service.MAX_CACHE_ROWS)
                result.processed_symbols += 1
            except Exception:
                result.failed_symbols += 1
                logger.exception("scheduled financial cleanup failed for %s", symbol)
        if self.redis_client is not None:
            self.redis_client.set(make_key("financial-sync", "sweep", "last-run", "cleanup"), "1", ex=86400 * 30)
        return result
