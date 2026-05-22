from __future__ import annotations

import logging
from uuid import UUID

from sqlalchemy.orm import Session

from app.core.db import session_scope
from app.domain.filing_ingestion import FilingIngestionService
from app.domain.news_ingestion import NewsIngestionService
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


def sync_watchlist_news(symbol: str) -> None:
    try:
        with session_scope() as session:
            service = NewsIngestionService(session)
            result = service.sync_news_for_ticker(symbol, mode="initial", force=True)
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
                },
            )
    except Exception:
        logger.exception("watchlist background sync failed for %s", symbol)


def sync_watchlist_filings(symbol: str) -> None:
    try:
        with session_scope() as session:
            service = FilingIngestionService(session)
            result = service.sync_filings_for_ticker(symbol)
            logger.info(
                "watchlist filing sync finished",
                extra={
                    "symbol": symbol,
                    "fetched": result.fetched_count,
                    "inserted": result.inserted_count,
                    "updated": result.updated_count,
                    "skipped": result.skipped_count,
                },
            )
    except Exception:
        logger.exception("watchlist filing sync failed for %s", symbol)
