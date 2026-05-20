"""Endpoint smoke test for watchlist API via FastAPI TestClient.

전제:
- scripts.seed로 phase2-test-user@example.com 사용자가 시드되어 있어야 함
- ticker_metadata에 최소 1건 이상의 종목 존재

검증 범위:
- /health 부트 확인
- POST /api/watchlists happy path (201, response body shape, ticker_name_kr 채워짐, sync_enqueued=True)
- 등록 시 BackgroundTasks가 sync_watchlist_news(symbol)을 호출하는지(mock으로 가로채 검증)
- GET /api/watchlists/{user_id} happy path (200, 방금 등록한 종목이 보임)
- 중복 등록 시 409
- 없는 사용자 등록 시 404
- 없는 symbol 등록 시 404
- 없는 사용자 list 시 404
- 필수 필드 누락 시 Pydantic 422

종료 시 생성한 watchlist row는 명시적으로 삭제.
"""
from __future__ import annotations

from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.core.db import session_scope
from app.main import app
from app.models import AppUser, TickerMetadata, Watchlist

SEED_EMAIL = "phase2-test-user@example.com"


def expect(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def get_seed_user_id():
    with session_scope() as session:
        user = session.scalar(select(AppUser).where(AppUser.email == SEED_EMAIL))
        if user is None:
            raise RuntimeError(f"seed user not found: {SEED_EMAIL}. run `python -m scripts.seed` first.")
        return user.id


def get_test_symbol() -> str:
    with session_scope() as session:
        symbol = session.scalar(
            select(TickerMetadata.symbol).order_by(TickerMetadata.symbol).limit(1)
        )
        if symbol is None:
            raise RuntimeError("ticker_metadata is empty")
        return symbol


def cleanup_watchlist(user_id, symbol: str) -> None:
    with session_scope() as session:
        session.execute(
            delete(Watchlist).where(Watchlist.user_id == user_id, Watchlist.symbol == symbol)
        )


def main() -> None:
    user_id = get_seed_user_id()
    symbol = get_test_symbol()
    print(f"using user_id={user_id} symbol={symbol}")

    cleanup_watchlist(user_id, symbol)
    client = TestClient(app)

    try:
        r = client.get("/health")
        expect(r.status_code == 200, f"health expected 200, got {r.status_code}")
        expect(r.json() == {"status": "ok"}, f"health body unexpected: {r.json()}")
        print(f"[PASS] health: {r.status_code} {r.json()}")

        with patch("app.api.watchlist.sync_watchlist_news") as mock_sync:
            r = client.post(
                "/api/watchlists",
                json={"user_id": str(user_id), "symbol": symbol, "memo": "smoke test"},
            )
            expect(r.status_code == 201, f"create expected 201, got {r.status_code}: {r.text}")
            body = r.json()
            expect(body["watchlist"]["symbol"] == symbol, "symbol mismatch")
            expect(body["watchlist"]["memo"] == "smoke test", "memo mismatch")
            expect(body["watchlist"]["ticker_name_kr"] is not None, "ticker_name_kr should be filled")
            expect(body["sync_enqueued"] is True, "sync_enqueued should be True")
            mock_sync.assert_called_once_with(symbol)
            print(
                f"[PASS] POST create: 201, "
                f"ticker_name_kr={body['watchlist']['ticker_name_kr']}, "
                f"bg_called_with={symbol}"
            )

        r = client.get(f"/api/watchlists/{user_id}")
        expect(r.status_code == 200, f"list expected 200, got {r.status_code}: {r.text}")
        body = r.json()
        found = next((item for item in body["items"] if item["symbol"] == symbol), None)
        expect(found is not None, f"symbol {symbol} not in list")
        expect(found["ticker_name_kr"] is not None, "list ticker_name_kr should be filled")
        print(f"[PASS] GET list: 200, items={len(body['items'])}, found={symbol}")

        with patch("app.api.watchlist.sync_watchlist_news") as mock_sync:
            r = client.post(
                "/api/watchlists",
                json={"user_id": str(user_id), "symbol": symbol, "memo": "duplicate"},
            )
            expect(r.status_code == 409, f"duplicate expected 409, got {r.status_code}: {r.text}")
            mock_sync.assert_not_called()
            print("[PASS] POST duplicate: 409 (background sync not invoked)")

        unknown_user = uuid4()
        with patch("app.api.watchlist.sync_watchlist_news") as mock_sync:
            r = client.post(
                "/api/watchlists",
                json={"user_id": str(unknown_user), "symbol": symbol, "memo": "ghost"},
            )
            expect(r.status_code == 404, f"unknown user expected 404, got {r.status_code}: {r.text}")
            mock_sync.assert_not_called()
            print("[PASS] POST unknown user: 404")

        with patch("app.api.watchlist.sync_watchlist_news") as mock_sync:
            r = client.post(
                "/api/watchlists",
                json={"user_id": str(user_id), "symbol": "ZZZ999999", "memo": "ghost"},
            )
            expect(r.status_code == 404, f"unknown symbol expected 404, got {r.status_code}: {r.text}")
            mock_sync.assert_not_called()
            print("[PASS] POST unknown symbol: 404")

        r = client.get(f"/api/watchlists/{unknown_user}")
        expect(r.status_code == 404, f"unknown user list expected 404, got {r.status_code}: {r.text}")
        print("[PASS] GET unknown user: 404")

        r = client.post("/api/watchlists", json={"user_id": str(user_id)})
        expect(r.status_code == 422, f"missing symbol expected 422, got {r.status_code}: {r.text}")
        print("[PASS] POST missing field: 422")

        print("\nALL SMOKE TESTS PASSED")
    finally:
        cleanup_watchlist(user_id, symbol)
        print(f"cleanup: removed watchlist rows for user_id={user_id} symbol={symbol}")


if __name__ == "__main__":
    main()
