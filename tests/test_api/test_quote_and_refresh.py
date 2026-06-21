"""신설 엔드포인트 스모크 테스트 — 지연 현재가 quote + 관심종목 refresh.

DB/redis/yfinance 없이 의존성 주입·패치로 라우팅·스키마·throttle 로직을 검증한다.
"""
from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.core.db import get_db
from app.external.quote_client import QuoteSnapshot
from app.main import app


class _FakeDB:
    """db.get(TickerMetadata, symbol) 만 쓰는 quote 엔드포인트용 가짜 세션."""

    def __init__(self, known: set[str]):
        self._known = known

    def get(self, _model, symbol):
        if symbol in self._known:
            return SimpleNamespace(symbol=symbol)
        return None


@pytest.fixture
def client():
    yield TestClient(app)
    app.dependency_overrides.clear()


# ── 현재가 quote ──────────────────────────────────────────────

def test_quote_returns_snapshot(client, monkeypatch):
    app.dependency_overrides[get_db] = lambda: _FakeDB({"005930"})

    snap = QuoteSnapshot(
        symbol="005930", price=81200.0, prev_close=80500.0, change=700.0,
        change_rate=0.87, volume=12345678, source="yfinance", is_delayed=True,
        ts=datetime(2026, 6, 20, 5, 30, tzinfo=UTC),
    )

    class _FakeSvc:
        def get_latest_quote(self, symbol, **_):
            return snap

    monkeypatch.setattr("app.domain.intraday_quote.IntradayQuoteService", lambda *a, **k: _FakeSvc())

    r = client.get("/api/stocks/005930/quote")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["symbol"] == "005930"
    assert body["price"] == 81200.0
    assert body["change_rate"] == 0.87
    assert body["is_delayed"] is True
    assert body["source"] == "yfinance"


def test_quote_unknown_symbol_404(client):
    app.dependency_overrides[get_db] = lambda: _FakeDB(set())
    r = client.get("/api/stocks/000000/quote")
    assert r.status_code == 404


def test_quote_upstream_failure_502(client, monkeypatch):
    app.dependency_overrides[get_db] = lambda: _FakeDB({"005930"})

    class _BoomSvc:
        def get_latest_quote(self, symbol, **_):
            raise RuntimeError("yfinance down")

    monkeypatch.setattr("app.domain.intraday_quote.IntradayQuoteService", lambda *a, **k: _BoomSvc())
    r = client.get("/api/stocks/005930/quote")
    assert r.status_code == 502


# ── throttle 로직 (Redis SET NX EX) ───────────────────────────

class _FakeRedis:
    """SET NX EX 의미만 흉내내는 인메모리 redis."""

    def __init__(self):
        self.store: dict[str, str] = {}

    def set(self, key, value, nx=False, ex=None):
        if nx and key in self.store:
            return None  # 이미 존재 → 점유 실패
        self.store[key] = value
        return True


def test_claim_refresh_slot_throttles_second_call(monkeypatch):
    from app.api import watchlist as wl

    fake = _FakeRedis()
    monkeypatch.setattr("app.core.redis.get_redis", lambda: fake)
    # throttle 활성 (>0)
    from app.config import get_settings
    get_settings.cache_clear()
    monkeypatch.setenv("WATCHLIST_REFRESH_THROTTLE_SECONDS", "600")
    get_settings.cache_clear()

    assert wl._claim_refresh_slot("005930") is True   # 첫 점유 성공
    assert wl._claim_refresh_slot("005930") is False  # 윈도우 내 재시도 → skip
    assert wl._claim_refresh_slot("000660") is True   # 다른 종목은 독립
    get_settings.cache_clear()


def test_claim_refresh_slot_no_redis_allows(monkeypatch):
    from app.api import watchlist as wl
    monkeypatch.setattr("app.core.redis.get_redis", lambda: None)
    assert wl._claim_refresh_slot("005930") is True


# ── refresh 엔드포인트 ────────────────────────────────────────

def test_refresh_all_enqueues_and_skips(client, monkeypatch):
    user_id = uuid4()
    app.dependency_overrides[get_db] = lambda: object()

    items = [SimpleNamespace(symbol="005930"), SimpleNamespace(symbol="000660")]

    class _FakeWatchlistSvc:
        def __init__(self, _db):
            pass

        def list_watchlists(self, _uid):
            return items

    enqueued: list[str] = []
    monkeypatch.setattr("app.api.watchlist.WatchlistService", _FakeWatchlistSvc)
    monkeypatch.setattr("app.api.watchlist._enqueue_symbol_sync", lambda bt, s: enqueued.append(s))
    # 005930 은 throttle, 000660 은 통과
    monkeypatch.setattr("app.api.watchlist._claim_refresh_slot", lambda s: s != "005930")

    r = client.post(f"/api/watchlists/{user_id}/refresh")
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["status"] == "refreshing"
    assert body["symbols"] == ["000660"]
    assert body["skipped"] == ["005930"]
    assert enqueued == ["000660"]


def test_refresh_symbol_not_in_watchlist_404(client, monkeypatch):
    user_id = uuid4()
    app.dependency_overrides[get_db] = lambda: object()

    class _FakeWatchlistSvc:
        def __init__(self, _db):
            pass

        def list_watchlists(self, _uid):
            return [SimpleNamespace(symbol="005930")]

    monkeypatch.setattr("app.api.watchlist.WatchlistService", _FakeWatchlistSvc)
    monkeypatch.setattr("app.api.watchlist._enqueue_symbol_sync", lambda bt, s: None)
    monkeypatch.setattr("app.api.watchlist._claim_refresh_slot", lambda s: True)

    r = client.post(f"/api/watchlists/{user_id}/035720/refresh")
    assert r.status_code == 404
