from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
import logging
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.config import get_settings
from app.core.db import session_scope
from app.domain.news_ingestion import NewsIngestionService, SyncNewsResult
from app.repositories.news_cache_repository import NewsCacheRepository
from app.repositories.watchlist_repository import WatchlistRepository

logger = logging.getLogger(__name__)
KST = ZoneInfo("Asia/Seoul")


@dataclass(slots=True)
class RefreshSweepResult:
    processed_symbols: int = 0
    failed_symbols: int = 0
    skipped_symbols: int = 0
    fetched_count: int = 0
    inserted_count: int = 0
    updated_count: int = 0
    skipped_count: int = 0
    filtered_count: int = 0
    body_failed_count: int = 0
    body_quota_saved_count: int = 0
    body_attempts_count: int = 0
    body_saved_count: int = 0
    trimmed_rows_count: int = 0
    trimmed_content_count: int = 0
    elapsed_ms: int = 0


@dataclass(slots=True)
class CleanupSweepResult:
    deleted_expired_rows: int = 0
    trimmed_rows_count: int = 0
    trimmed_content_count: int = 0
    processed_symbols: int = 0
    elapsed_ms: int = 0


class NewsCacheSchedulerService:
    """Runs periodic refresh and cleanup sweeps for news cache."""

    def __init__(
        self,
        session: Session,
        *,
        ingestion_factory: Callable[[Session], NewsIngestionService] | None = None,
        symbol_session_factory: Callable[[], Any] | None = None,
        max_cache_rows: int | None = None,
        max_content_rows: int | None = None,
    ) -> None:
        self.session = session
        self.watchlist_repo = WatchlistRepository(session)
        self.news_repo = NewsCacheRepository(session)
        self.ingestion_factory = ingestion_factory or NewsIngestionService
        self.symbol_session_factory = symbol_session_factory or session_scope
        self.max_cache_rows = max_cache_rows or NewsIngestionService.MAX_CACHE_ROWS
        self.max_content_rows = max_content_rows or NewsIngestionService.MAX_CONTENT_ROWS
        self.redis_client = NewsIngestionService._build_redis_client(get_settings().redis_url)

    def run_watchlist_refresh(
        self,
        *,
        force: bool = False,
        limit: int | None = None,
    ) -> RefreshSweepResult:
        started = datetime.now(UTC)
        result = RefreshSweepResult()
        symbols = self.watchlist_repo.list_distinct_symbols()

        for symbol in symbols:
            try:
                with self.symbol_session_factory() as symbol_session:
                    sync_result = self.ingestion_factory(symbol_session).sync_news_for_ticker(
                        symbol,
                        mode="refresh",
                        force=force,
                        limit=limit,
                    )
            except Exception:
                result.failed_symbols += 1
                logger.exception("scheduled refresh failed for %s", symbol)
                continue

            result.processed_symbols += 1
            if sync_result.skipped_count > 0 and sync_result.fetched_count == 0:
                result.skipped_symbols += 1
            self._merge_sync_result(result, sync_result)

        result.elapsed_ms = int((datetime.now(UTC) - started).total_seconds() * 1000)
        self._set_sweep_last_run("refresh", started)
        logger.info(
            "scheduled refresh finished",
            extra={
                "processed_symbols": result.processed_symbols,
                "failed_symbols": result.failed_symbols,
                "skipped_symbols": result.skipped_symbols,
                "fetched": result.fetched_count,
                "inserted": result.inserted_count,
                "updated": result.updated_count,
                "filtered": result.filtered_count,
                "body_quota_saved": result.body_quota_saved_count,
                "body_attempts": result.body_attempts_count,
                "body_saved": result.body_saved_count,
                "trimmed_rows": result.trimmed_rows_count,
                "trimmed_content": result.trimmed_content_count,
                "elapsed_ms": result.elapsed_ms,
            },
        )
        return result

    def run_news_cleanup(self, *, now: datetime | None = None) -> CleanupSweepResult:
        started = datetime.now(UTC)
        result = CleanupSweepResult()

        result.deleted_expired_rows = self.news_repo.delete_expired_rows(now=now)

        for symbol in self.news_repo.list_symbols_with_cache():
            result.processed_symbols += 1
            result.trimmed_rows_count += self.news_repo.trim_rows_for_symbol(symbol, self.max_cache_rows)
            result.trimmed_content_count += self.news_repo.trim_content_for_symbol(
                symbol,
                self.max_content_rows,
            )

        result.elapsed_ms = int((datetime.now(UTC) - started).total_seconds() * 1000)
        self._set_sweep_last_run("cleanup", started)
        logger.info(
            "scheduled cleanup finished",
            extra={
                "deleted_expired_rows": result.deleted_expired_rows,
                "processed_symbols": result.processed_symbols,
                "trimmed_rows": result.trimmed_rows_count,
                "trimmed_content": result.trimmed_content_count,
                "elapsed_ms": result.elapsed_ms,
            },
        )
        return result

    @staticmethod
    def _merge_sync_result(sweep_result: RefreshSweepResult, sync_result: SyncNewsResult) -> None:
        sweep_result.fetched_count += sync_result.fetched_count
        sweep_result.inserted_count += sync_result.inserted_count
        sweep_result.updated_count += sync_result.updated_count
        sweep_result.skipped_count += sync_result.skipped_count
        sweep_result.filtered_count += sync_result.filtered_count
        sweep_result.body_failed_count += sync_result.body_failed_count
        sweep_result.body_quota_saved_count += sync_result.body_quota_saved_count
        sweep_result.body_attempts_count += sync_result.body_attempts_count
        sweep_result.body_saved_count += sync_result.body_saved_count
        sweep_result.trimmed_rows_count += sync_result.trimmed_rows_count
        sweep_result.trimmed_content_count += sync_result.trimmed_content_count

    def _set_sweep_last_run(self, mode: str, started_at: datetime) -> None:
        if self.redis_client is None:
            return
        try:
            self.redis_client.set(
                self._sweep_last_run_key(mode),
                started_at.astimezone(KST).isoformat(),
                ex=60 * 60 * 24 * 7,
            )
        except Exception:
            logger.exception("failed to persist scheduler last-run for %s", mode)

    @staticmethod
    def _sweep_last_run_key(mode: str) -> str:
        return f"news-sync:sweep:last-run:{mode}"


def run_scheduled_watchlist_refresh(*, force: bool = False, limit: int | None = None) -> RefreshSweepResult:
    with session_scope() as session:
        service = NewsCacheSchedulerService(session)
        return service.run_watchlist_refresh(force=force, limit=limit)


def run_scheduled_news_cleanup(*, now: datetime | None = None) -> CleanupSweepResult:
    with session_scope() as session:
        service = NewsCacheSchedulerService(session)
        return service.run_news_cleanup(now=now)
