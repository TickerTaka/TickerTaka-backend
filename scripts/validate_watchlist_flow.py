from __future__ import annotations

from dataclasses import dataclass
import logging
from unittest.mock import patch
from uuid import uuid4

from sqlalchemy import func, select

from app.core.db import SessionLocal
from app.domain.watchlist_service import (
    TickerNotFoundError,
    UserNotFoundError,
    WatchlistAlreadyExistsError,
    WatchlistService,
    sync_watchlist_news,
)
from app.models import AppUser, TickerMetadata, Watchlist


@dataclass(slots=True)
class FlowResult:
    name: str
    details: str


def expect(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def get_test_symbol() -> str:
    with SessionLocal() as session:
        row = session.scalar(select(TickerMetadata.symbol).order_by(TickerMetadata.symbol).limit(1))
        if row is None:
            raise RuntimeError("ticker_metadata is empty")
        return row


def run_service_flow(symbol: str) -> FlowResult:
    with SessionLocal() as session:
        try:
            user = AppUser(
                email=f"watchlist-test-{uuid4().hex[:8]}@example.com",
                password_hash="test-hash",
                name="Watchlist Test User",
            )
            session.add(user)
            session.flush()

            service = WatchlistService(session)
            created = service.create_watchlist(user.id, symbol, "phase2 test memo")
            listed = service.list_watchlists(user.id)

            expect(created.symbol == symbol, "created watchlist symbol mismatch")
            expect(created.memo == "phase2 test memo", "created watchlist memo mismatch")
            expect(created.ticker is not None, "created watchlist should include ticker relationship")
            expect(len(listed) == 1, "list_watchlists should return 1 item")
            expect(listed[0].symbol == symbol, "listed watchlist symbol mismatch")

            duplicate_raised = False
            try:
                service.create_watchlist(user.id, symbol, "duplicate")
            except WatchlistAlreadyExistsError:
                duplicate_raised = True
            expect(duplicate_raised, "duplicate watchlist should raise WatchlistAlreadyExistsError")

            persisted_count = session.scalar(
                select(func.count())
                .select_from(Watchlist)
                .where(Watchlist.user_id == user.id, Watchlist.symbol == symbol)
            )
            expect(persisted_count == 1, "watchlist row should exist once inside transaction")

            return FlowResult(
                name="service_flow",
                details=f"user_id={user.id} symbol={symbol} listed={len(listed)} duplicate_guard=ok",
            )
        finally:
            session.rollback()


def run_empty_watchlist_flow() -> FlowResult:
    with SessionLocal() as session:
        try:
            user = AppUser(
                email=f"watchlist-empty-{uuid4().hex[:8]}@example.com",
                password_hash="test-hash",
                name="Watchlist Empty User",
            )
            session.add(user)
            session.flush()

            service = WatchlistService(session)
            listed = service.list_watchlists(user.id)
            expect(listed == [], "empty watchlist should return empty list")
            return FlowResult(name="empty_watchlist", details=f"user_id={user.id} listed=0")
        finally:
            session.rollback()


def run_missing_user_flow(symbol: str) -> FlowResult:
    with SessionLocal() as session:
        try:
            service = WatchlistService(session)
            missing_id = uuid4()
            missing_create = False
            missing_list = False
            try:
                service.create_watchlist(missing_id, symbol, "memo")
            except UserNotFoundError:
                missing_create = True
            try:
                service.list_watchlists(missing_id)
            except UserNotFoundError:
                missing_list = True
            expect(missing_create, "missing user create should raise UserNotFoundError")
            expect(missing_list, "missing user list should raise UserNotFoundError")
            return FlowResult(name="missing_user", details=f"user_id={missing_id} create=list=guarded")
        finally:
            session.rollback()


def run_missing_ticker_flow() -> FlowResult:
    with SessionLocal() as session:
        try:
            user = AppUser(
                email=f"watchlist-noticker-{uuid4().hex[:8]}@example.com",
                password_hash="test-hash",
                name="Watchlist NoTicker User",
            )
            session.add(user)
            session.flush()

            service = WatchlistService(session)
            raised = False
            try:
                service.create_watchlist(user.id, "ZZZ999999", "memo")
            except TickerNotFoundError:
                raised = True
            expect(raised, "missing ticker create should raise TickerNotFoundError")
            return FlowResult(name="missing_ticker", details=f"user_id={user.id} symbol=ZZZ999999 guarded")
        finally:
            session.rollback()


def run_background_trigger_flow(symbol: str) -> FlowResult:
    captured: dict[str, object] = {}

    class FakeNewsIngestionService:
        def __init__(self, session) -> None:
            captured["session_bound"] = session is not None

        def sync_news_for_ticker(self, symbol: str, mode: str = "initial", force: bool = False):
            captured["symbol"] = symbol
            captured["mode"] = mode
            captured["force"] = force

            class Result:
                fetched_count = 1
                inserted_count = 1
                updated_count = 0
                skipped_count = 0
                filtered_count = 0
                body_saved_count = 1

            return Result()

    with patch("app.domain.watchlist_service.NewsIngestionService", FakeNewsIngestionService):
        sync_watchlist_news(symbol)

    expect(captured.get("session_bound") is True, "background trigger should open DB session")
    expect(captured.get("symbol") == symbol, "background trigger should pass symbol")
    expect(captured.get("mode") == "initial", "background trigger should use initial mode")
    expect(captured.get("force") is True, "background trigger should force sync")

    return FlowResult(
        name="background_trigger",
        details=f"symbol={captured['symbol']} mode={captured['mode']} force={captured['force']}",
    )


def run_background_failure_flow(symbol: str) -> FlowResult:
    class FailingNewsIngestionService:
        def __init__(self, session) -> None:
            pass

        def sync_news_for_ticker(self, symbol: str, mode: str = "initial", force: bool = False):
            raise RuntimeError("background sync boom")

    logger = logging.getLogger("app.domain.watchlist_service")

    with patch("app.domain.watchlist_service.NewsIngestionService", FailingNewsIngestionService):
        with patch.object(logger, "exception") as logger_exception:
            sync_watchlist_news(symbol)
            expect(logger_exception.called, "background failure should be logged via logger.exception")

    return FlowResult(name="background_failure", details=f"symbol={symbol} exception_logged=True")


def main() -> None:
    symbol = get_test_symbol()
    results = [
        run_service_flow(symbol),
        run_empty_watchlist_flow(),
        run_missing_user_flow(symbol),
        run_missing_ticker_flow(),
        run_background_trigger_flow(symbol),
        run_background_failure_flow(symbol),
    ]
    for result in results:
        print(f"[PASS] {result.name}")
        print(f"       {result.details}")


if __name__ == "__main__":
    main()
