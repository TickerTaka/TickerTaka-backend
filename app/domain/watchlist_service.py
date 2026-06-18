from __future__ import annotations

import logging
from uuid import UUID

from sqlalchemy.orm import Session

from app.core.db import session_scope
from app.domain.filing_ingestion import FilingIngestionService
from app.domain.financial_ingestion import FinancialIngestionService
from app.domain.news_ingestion import NewsIngestionService
from app.domain.price_ingestion import PriceIngestionService
from app.models import Watchlist
from app.repositories.watchlist_repository import WatchlistRepository

logger = logging.getLogger(__name__)


class WatchlistAlreadyExistsError(ValueError):
    pass


class WatchlistNotFoundError(ValueError):
    pass


class UserNotFoundError(ValueError):
    pass


class TickerNotFoundError(ValueError):
    pass


class WatchlistService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.repo = WatchlistRepository(session)

    def create_watchlist(self, user_id: UUID, symbol: str, memo: str | None = None) -> Watchlist:
        if self.repo.get_user(user_id) is None:
            raise UserNotFoundError(f"user not found: {user_id}")
        if self.repo.get_ticker(symbol) is None:
            raise TickerNotFoundError(f"ticker not found: {symbol}")
        if self.repo.get_by_user_and_symbol(user_id, symbol) is not None:
            raise WatchlistAlreadyExistsError(f"watchlist already exists: {user_id}/{symbol}")
        return self.repo.create(user_id=user_id, symbol=symbol, memo=memo)

    def list_watchlists(self, user_id: UUID) -> list[Watchlist]:
        if self.repo.get_user(user_id) is None:
            raise UserNotFoundError(f"user not found: {user_id}")
        return self.repo.list_by_user(user_id)

    def delete_watchlist(self, user_id: UUID, symbol: str) -> None:
        if self.repo.get_user(user_id) is None:
            raise UserNotFoundError(f"user not found: {user_id}")
        item = self.repo.get_by_user_and_symbol(user_id, symbol)
        if item is None:
            raise WatchlistNotFoundError(f"watchlist not found: {user_id}/{symbol}")
        self.repo.delete(item)


def sync_watchlist_news(symbol: str) -> None:
    try:
        with session_scope() as session:
            service = NewsIngestionService(session)
            result = service.sync_news_for_ticker(symbol, mode="initial", force=True)
            session.flush()

            # News FinBERT/룰 baseline 분석 트리거. 이게 없으면 production
            # 어디서도 reindex_news_for_symbol()이 호출되지 않아 뉴스의
            # sentiment/impact_score/event_type이 영영 null로 남는다.
            # 실패해도 뉴스 DB 적재는 유지되어야 하므로 best-effort.
            from app.domain.evidence_indexing import EvidenceIndexingService

            indexed_rows = 0
            try:
                index_result = EvidenceIndexingService(session).reindex_news_for_symbol(symbol)
                indexed_rows = index_result.indexed_rows
            except Exception:
                logger.exception(
                    "news evidence indexing failed for %s (news persisted, analysis skipped)",
                    symbol,
                )

            logger.info(
                "watchlist background sync finished",
                extra={
                    "symbol": symbol,
                    "fetched": result.fetched_count,
                    "inserted": result.inserted_count,
                    "updated": result.updated_count,
                    "skipped": result.skipped_count,
                    "filtered": result.filtered_count,
                    "body_saved": result.body_saved_count,
                    "indexed_rows": indexed_rows,
                },
            )
    except Exception:
        logger.exception("watchlist background sync failed for %s", symbol)


def sync_watchlist_prices(symbol: str) -> None:
    try:
        with session_scope() as session:
            result = PriceIngestionService(session).sync_prices_for_ticker(symbol, mode="initial", force=True)
            logger.info(
                "watchlist background price sync finished",
                extra={
                    "symbol": symbol,
                    "fetched": result.fetched_count,
                    "inserted": result.inserted_count,
                    "updated": result.updated_count,
                    "indicators": result.indicators_count,
                },
            )
    except Exception:
        logger.exception("watchlist background price sync failed for %s", symbol)


def sync_watchlist_financials(symbol: str) -> None:
    try:
        with session_scope() as session:
            result = FinancialIngestionService(session).sync_financials_for_ticker(symbol, mode="initial", force=True)
            logger.info(
                "watchlist background financial sync finished",
                extra={
                    "symbol": symbol,
                    "fetched_periods": result.fetched_periods,
                    "saved_rows": result.saved_rows,
                },
            )
    except Exception:
        logger.exception("watchlist background financial sync failed for %s", symbol)


def sync_watchlist_valuation(symbol: str) -> None:
    try:
        with session_scope() as session:
            PriceIngestionService(session).sync_latest_valuation_for_ticker(symbol)
            logger.info(
                "watchlist background valuation sync finished",
                extra={"symbol": symbol},
            )
    except Exception:
        logger.exception("watchlist background valuation sync failed for %s", symbol)


def sync_watchlist_filings(symbol: str) -> None:
    try:
        from app.domain.evidence_indexing import EvidenceIndexingService

        with session_scope() as session:
            filing_result = FilingIngestionService(session).sync_filings_for_ticker(symbol, mode="initial")
            index_result = EvidenceIndexingService(session).reindex_filing_for_symbol(symbol)
            logger.info(
                "watchlist background filing sync finished",
                extra={
                    "symbol": symbol,
                    "fetched": filing_result.fetched_count,
                    "inserted": filing_result.inserted_count,
                    "updated": filing_result.updated_count,
                    "skipped": filing_result.skipped_count,
                    "indexed_rows": index_result.indexed_rows,
                    "index_skipped": index_result.skipped_rows,
                    "index_failed": index_result.failed_rows,
                },
            )
    except Exception:
        logger.exception("watchlist background filing sync failed for %s", symbol)
