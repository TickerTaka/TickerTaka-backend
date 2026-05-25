from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.domain.intraday_quote import IntradayQuoteService
from app.external.quote_client import QuoteSnapshot


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.ttls: dict[str, int] = {}

    def get(self, key: str):
        return self.values.get(key)

    def setex(self, key: str, ttl: int, value: str) -> None:
        self.values[key] = value
        self.ttls[key] = ttl


class FakeQuoteClient:
    def __init__(self) -> None:
        self.calls = 0

    def fetch_quote(self, symbol: str) -> QuoteSnapshot:
        self.calls += 1
        return QuoteSnapshot(
            symbol=symbol,
            price=100000 + self.calls,
            prev_close=99900,
            change=100 + self.calls,
            change_rate=0.1,
            volume=123456,
            source="fake",
            is_delayed=True,
            ts=datetime.now(UTC),
        )


def main() -> None:
    fake_redis = FakeRedis()
    fake_client = FakeQuoteClient()
    service = IntradayQuoteService(redis_client=fake_redis, quote_client=fake_client)

    first = service.get_latest_quote("000660", max_age_seconds=300)
    second = service.get_latest_quote("000660", max_age_seconds=300)
    assert fake_client.calls == 1
    assert first.price == second.price

    # Force stale cache
    stale = QuoteSnapshot(
        symbol="000660",
        price=99999,
        prev_close=99800,
        change=199,
        change_rate=0.2,
        volume=1,
        source="fake",
        is_delayed=True,
        ts=datetime.now(UTC) - timedelta(minutes=10),
    )
    service._store_cached_quote("000660", stale)
    third = service.get_latest_quote("000660", max_age_seconds=300)
    assert fake_client.calls == 2
    assert third.price != stale.price

    print(
        {
            "symbol": "000660",
            "initial_source": first.source,
            "cache_hit_calls": 1,
            "stale_refresh_calls": fake_client.calls,
            "ttl_keys": list(fake_redis.ttls.keys()),
        }
    )


if __name__ == "__main__":
    main()
