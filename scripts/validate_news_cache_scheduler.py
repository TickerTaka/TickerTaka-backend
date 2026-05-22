from __future__ import annotations

from contextlib import nullcontext
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import func, select

from app.core.db import SessionLocal
from app.domain.news_cache_scheduler import NewsCacheSchedulerService
from app.domain.news_ingestion import SyncNewsResult
from app.models import NewsCache, TickerMetadata


@dataclass(slots=True)
class FlowResult:
    name: str
    details: str


class FakeIngestionService:
    calls: list[tuple[str, str, bool, int | None]] = []

    def __init__(self, session) -> None:
        self.session = session

    def sync_news_for_ticker(
        self,
        symbol: str,
        mode: str = "initial",
        force: bool = False,
        limit: int | None = None,
    ) -> SyncNewsResult:
        self.__class__.calls.append((symbol, mode, force, limit))
        return SyncNewsResult(
            fetched_count=3,
            inserted_count=1,
            updated_count=1,
            skipped_count=0,
            filtered_count=1,
            body_saved_count=1,
            trimmed_rows_count=0,
        )


class FailingFakeIngestionService(FakeIngestionService):
    failing_symbol: str | None = None

    def sync_news_for_ticker(
        self,
        symbol: str,
        mode: str = "initial",
        force: bool = False,
        limit: int | None = None,
    ) -> SyncNewsResult:
        if symbol == self.failing_symbol:
            raise RuntimeError(f"forced failure for {symbol}")
        return super().sync_news_for_ticker(symbol, mode=mode, force=force, limit=limit)


def expect(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def get_test_symbols(session, limit: int = 2, *, empty_cache_only: bool = False) -> list[str]:
    stmt = select(TickerMetadata.symbol).order_by(TickerMetadata.symbol)
    if empty_cache_only:
        stmt = (
            select(TickerMetadata.symbol)
            .outerjoin(NewsCache, NewsCache.symbol == TickerMetadata.symbol)
            .group_by(TickerMetadata.symbol)
            .having(func.count(NewsCache.id) == 0)
            .order_by(TickerMetadata.symbol)
        )
    symbols = list(session.scalars(stmt.limit(limit)))
    if len(symbols) < limit:
        raise RuntimeError("not enough ticker_metadata rows to validate scheduler")
    return symbols


def run_refresh_flow() -> FlowResult:
    FakeIngestionService.calls = []
    with SessionLocal() as session:
        try:
            symbols = get_test_symbols(session, limit=2)
            service = NewsCacheSchedulerService(
                session,
                ingestion_factory=FakeIngestionService,
                symbol_session_factory=lambda: nullcontext(session),
            )
            service.watchlist_repo.list_distinct_symbols = lambda: symbols
            result = service.run_watchlist_refresh(force=True, limit=4)

            expect(result.processed_symbols == 2, "refresh should process 2 symbols")
            expect(result.failed_symbols == 0, "refresh should not fail")
            expect(result.fetched_count == 6, "refresh fetched total mismatch")
            expect(result.inserted_count == 2, "refresh inserted total mismatch")
            expect(result.updated_count == 2, "refresh updated total mismatch")
            expect(result.filtered_count == 2, "refresh filtered total mismatch")
            expect(FakeIngestionService.calls == [(symbols[0], "refresh", True, 4), (symbols[1], "refresh", True, 4)], "refresh calls mismatch")

            return FlowResult(
                name="watchlist_refresh",
                details=f"symbols={symbols} fetched={result.fetched_count} inserted={result.inserted_count}",
            )
        finally:
            session.rollback()


def run_empty_watchlist_flow() -> FlowResult:
    FakeIngestionService.calls = []
    with SessionLocal() as session:
        try:
            service = NewsCacheSchedulerService(
                session,
                ingestion_factory=FakeIngestionService,
                symbol_session_factory=lambda: nullcontext(session),
            )
            service.watchlist_repo.list_distinct_symbols = lambda: []
            result = service.run_watchlist_refresh(force=True, limit=4)

            expect(result.processed_symbols == 0, "empty watchlist should process 0 symbols")
            expect(result.failed_symbols == 0, "empty watchlist should fail 0 symbols")
            expect(FakeIngestionService.calls == [], "empty watchlist should not call ingestion")
            return FlowResult(name="empty_watchlist", details="processed=0 failed=0")
        finally:
            session.rollback()


def run_refresh_failure_isolation_flow() -> FlowResult:
    FakeIngestionService.calls = []
    FailingFakeIngestionService.calls = []
    with SessionLocal() as session:
        try:
            symbols = get_test_symbols(session, limit=2)
            FailingFakeIngestionService.failing_symbol = symbols[0]
            service = NewsCacheSchedulerService(
                session,
                ingestion_factory=FailingFakeIngestionService,
                symbol_session_factory=lambda: nullcontext(session),
            )
            service.watchlist_repo.list_distinct_symbols = lambda: symbols
            result = service.run_watchlist_refresh(force=True, limit=4)

            expect(result.processed_symbols == 1, "failure isolation should still process the healthy symbol")
            expect(result.failed_symbols == 1, "failure isolation should count one failed symbol")
            expect(result.fetched_count == 3, "failure isolation fetched total mismatch")
            expect(FailingFakeIngestionService.calls == [(symbols[1], "refresh", True, 4)], "healthy symbol should still run")
            return FlowResult(
                name="refresh_failure_isolation",
                details=f"failed={symbols[0]} processed={symbols[1]}",
            )
        finally:
            FailingFakeIngestionService.failing_symbol = None
            session.rollback()


def run_cleanup_flow() -> FlowResult:
    with SessionLocal() as session:
        try:
            symbol = get_test_symbols(session, limit=1, empty_cache_only=True)[0]
            base = datetime.now(UTC).replace(microsecond=0)

            rows: list[NewsCache] = []
            for index in range(5):
                rows.append(
                    NewsCache(
                        symbol=symbol,
                        title=f"phase3 cleanup row {index}",
                        source_url=f"https://example.com/phase3/cleanup/{uuid4().hex}/{index}",
                        summary="summary",
                        content=f"content {index}" if index < 4 else None,
                        source_name="example.com",
                        published_at=base - timedelta(hours=index),
                        retrieved_at=base - timedelta(hours=index),
                        ttl_until=base + timedelta(days=30),
                    )
                )

            expired = NewsCache(
                symbol=symbol,
                title="phase3 expired row",
                source_url=f"https://example.com/phase3/expired/{uuid4().hex}",
                summary="expired",
                content="expired content",
                source_name="example.com",
                published_at=base - timedelta(days=40),
                retrieved_at=base - timedelta(days=40),
                ttl_until=base - timedelta(minutes=1),
            )
            session.add_all(rows + [expired])
            session.flush()

            service = NewsCacheSchedulerService(
                session,
                max_cache_rows=3,
            )
            service.news_repo.list_symbols_with_cache = lambda: [symbol]
            result = service.run_news_cleanup(now=base)

            total_rows = session.scalar(
                select(func.count()).select_from(NewsCache).where(NewsCache.symbol == symbol)
            ) or 0
            expect(result.deleted_expired_rows >= 1, "cleanup should delete at least 1 expired row")
            expect(result.trimmed_rows_count == 2, "cleanup should trim 2 rows for the target symbol")
            expect(total_rows == 3, "cleanup final row count mismatch")

            return FlowResult(
                name="cleanup_sweep",
                details=(
                    f"symbol={symbol} deleted={result.deleted_expired_rows} "
                    f"trimmed_rows={result.trimmed_rows_count}"
                ),
            )
        finally:
            session.rollback()


def run_cleanup_no_expired_flow() -> FlowResult:
    with SessionLocal() as session:
        try:
            symbol = get_test_symbols(session, limit=1, empty_cache_only=True)[0]
            base = datetime.now(UTC).replace(microsecond=0)
            session.add(
                NewsCache(
                    symbol=symbol,
                    title="phase3 no expired row",
                    source_url=f"https://example.com/phase3/no-expired/{uuid4().hex}",
                    summary="summary",
                    content="content",
                    source_name="example.com",
                    published_at=base,
                    retrieved_at=base,
                    ttl_until=base + timedelta(days=10),
                )
            )
            session.flush()

            service = NewsCacheSchedulerService(session, max_cache_rows=3)
            service.news_repo.list_symbols_with_cache = lambda: [symbol]
            result = service.run_news_cleanup(now=base)

            expect(result.deleted_expired_rows == 0, "cleanup_no_expired should delete 0 rows")
            return FlowResult(name="cleanup_no_expired", details="deleted=0")
        finally:
            session.rollback()


def run_cleanup_under_limits_flow() -> FlowResult:
    with SessionLocal() as session:
        try:
            symbol = get_test_symbols(session, limit=1, empty_cache_only=True)[0]
            base = datetime.now(UTC).replace(microsecond=0)
            for index in range(2):
                session.add(
                    NewsCache(
                        symbol=symbol,
                        title=f"phase3 under limit {index}",
                        source_url=f"https://example.com/phase3/under-limit/{uuid4().hex}/{index}",
                        summary="summary",
                        content=f"content {index}",
                        source_name="example.com",
                        published_at=base - timedelta(minutes=index),
                        retrieved_at=base - timedelta(minutes=index),
                        ttl_until=base + timedelta(days=5),
                    )
                )
            session.flush()

            service = NewsCacheSchedulerService(session, max_cache_rows=3)
            service.news_repo.list_symbols_with_cache = lambda: [symbol]
            result = service.run_news_cleanup(now=base)

            expect(result.trimmed_rows_count == 0, "cleanup_under_limits should trim 0 rows")
            return FlowResult(name="cleanup_under_limits", details="trimmed_rows=0")
        finally:
            session.rollback()


def main() -> None:
    flows = [
        run_empty_watchlist_flow(),
        run_refresh_flow(),
        run_refresh_failure_isolation_flow(),
        run_cleanup_flow(),
        run_cleanup_no_expired_flow(),
        run_cleanup_under_limits_flow(),
    ]
    for flow in flows:
        print(f"[PASS] {flow.name}")
        print(f"       {flow.details}")
    print("\nALL PASSED")


if __name__ == "__main__":
    main()
