from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import logging

from app.config import get_settings
from app.core.db import session_scope
from app.core.redis import build_redis_client, make_key
from app.domain.price_ingestion import PriceIngestionService
from app.repositories.watchlist_repository import WatchlistRepository

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class PriceSweepResult:
    processed_symbols: int = 0
    fetched_rows: int = 0
    inserted_rows: int = 0
    updated_rows: int = 0
    indicator_rows: int = 0
    trimmed_price_rows: int = 0
    trimmed_indicator_rows: int = 0
    failed_symbols: int = 0


class PriceCacheSchedulerService:
    def __init__(self, redis_client=None, session_factory=session_scope) -> None:
        self.redis_client = redis_client or build_redis_client(get_settings().redis_url)
        self.session_factory = session_factory

    def run_watchlist_refresh(self) -> PriceSweepResult:
        result = PriceSweepResult()
        with self.session_factory() as session:
            symbols = WatchlistRepository(session).list_distinct_symbols()
        for symbol in symbols:
            try:
                with self.session_factory() as session:
                    sync_result = PriceIngestionService(session, redis_client=self.redis_client).sync_prices_for_ticker(
                        symbol,
                        mode="refresh",
                    )
                result.processed_symbols += 1
                result.fetched_rows += sync_result.fetched_count
                result.inserted_rows += sync_result.inserted_count
                result.updated_rows += sync_result.updated_count
                result.indicator_rows += sync_result.indicators_count
            except Exception:
                result.failed_symbols += 1
                logger.exception("scheduled price refresh failed for %s", symbol)
        self._set_last_run("refresh")
        return result

    def run_cleanup(self) -> PriceSweepResult:
        result = PriceSweepResult()
        with self.session_factory() as session:
            symbols = WatchlistRepository(session).list_distinct_symbols()
        for symbol in symbols:
            try:
                with self.session_factory() as session:
                    service = PriceIngestionService(session, redis_client=self.redis_client)
                    result.trimmed_price_rows += service.price_repo.trim_rows_for_symbol(symbol, service.MAX_CACHE_ROWS)
                    result.trimmed_indicator_rows += service.indicator_repo.trim_rows_for_symbol(symbol, service.MAX_CACHE_ROWS)
                result.processed_symbols += 1
            except Exception:
                result.failed_symbols += 1
                logger.exception("scheduled price cleanup failed for %s", symbol)
        self._set_last_run("cleanup")
        return result

    def _set_last_run(self, mode: str) -> None:
        if self.redis_client is None:
            return
        self.redis_client.set(make_key("price-sync", "sweep", "last-run", mode), datetime.now(UTC).isoformat(), ex=86400 * 30)
