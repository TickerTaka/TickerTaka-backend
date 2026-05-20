"""실 Redis 연결 기반 lock/cooldown 검증.

외부 API/스크래퍼는 Fake로 두고 Redis만 실연결로 검증한다.
- redis://localhost:6379/0 에 redis-py로 직접 접속
- 종료 시 검증 대상 키(news-sync:lock:*, news-sync:last-run:*) 정리
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import redis
from sqlalchemy import select

from app.config import get_settings
from app.core.db import SessionLocal
from app.domain.news_ingestion import NewsIngestionService
from app.models import TickerMetadata
from scripts.validate_news_ingestion import (
    FakeArticleScraper,
    FakeNaverNewsClient,
    build_item,
    build_scraped,
    get_test_ticker,
)


@dataclass(slots=True)
class CheckResult:
    name: str
    passed: bool
    detail: str


def cleanup_keys(client: redis.Redis, symbol: str) -> None:
    client.delete(NewsIngestionService._lock_key(symbol))
    client.delete(NewsIngestionService._last_run_key(symbol))


def make_service(session, ticker, items, articles, redis_client):
    return NewsIngestionService(
        session,
        news_client=FakeNaverNewsClient(items),
        article_scraper=FakeArticleScraper(articles),
        redis_client=redis_client,
    )


def check_ping(client: redis.Redis) -> CheckResult:
    try:
        ok = client.ping()
        return CheckResult("redis_ping", bool(ok), f"PING -> {ok}")
    except Exception as exc:
        return CheckResult("redis_ping", False, f"ping error: {exc!r}")


def check_normal_run(client: redis.Redis, ticker) -> CheckResult:
    cleanup_keys(client, ticker.symbol)
    base = datetime.now(UTC).replace(microsecond=0)
    url = f"https://news.example.com/{ticker.symbol}/redis-integration/normal"
    item = build_item(ticker, url, "redis normal run headline check", base)
    articles = {url: build_scraped(url, item.title, base)}

    with SessionLocal() as session:
        service = make_service(session, ticker, [item], articles, client)
        try:
            raw = service.sync_news_for_ticker(ticker.symbol, mode="initial", force=True, limit=1)
        finally:
            session.rollback()

    lock_key = NewsIngestionService._lock_key(ticker.symbol)
    last_run_key = NewsIngestionService._last_run_key(ticker.symbol)
    lock_after = client.get(lock_key)
    last_run = client.get(last_run_key)
    last_run_ttl = client.ttl(last_run_key)

    cleanup_keys(client, ticker.symbol)

    passed = (
        raw.fetched_count == 1
        and raw.skipped_count == 0
        and lock_after is None
        and last_run is not None
        and 86000 < last_run_ttl <= 86400  # 24h TTL
    )
    detail = (
        f"fetched={raw.fetched_count} skipped={raw.skipped_count} "
        f"lock_after={lock_after} last_run={last_run} last_run_ttl={last_run_ttl}"
    )
    return CheckResult("normal_run_releases_lock_and_sets_last_run", passed, detail)


def check_lock_already_held(client: redis.Redis, ticker) -> CheckResult:
    cleanup_keys(client, ticker.symbol)
    lock_key = NewsIngestionService._lock_key(ticker.symbol)
    # 다른 워커가 락을 잡고 있는 상황을 흉내냄
    client.set(lock_key, "external-holder", nx=True, ex=600)

    base = datetime.now(UTC).replace(microsecond=0)
    url = f"https://news.example.com/{ticker.symbol}/redis-integration/lock"
    item = build_item(ticker, url, "redis lock skip headline check", base)

    with SessionLocal() as session:
        service = make_service(session, ticker, [item], {}, client)
        try:
            raw = service.sync_news_for_ticker(ticker.symbol, mode="initial", force=True, limit=1)
        finally:
            session.rollback()

    # 외부 홀더의 락은 그대로 유지되어야 한다
    lock_value = client.get(lock_key)
    lock_ttl = client.ttl(lock_key)

    cleanup_keys(client, ticker.symbol)

    passed = (
        raw.skipped_count == 1
        and raw.fetched_count == 0
        and raw.inserted_count == 0
        and lock_value == "external-holder"
        and lock_ttl > 0
    )
    detail = (
        f"skipped={raw.skipped_count} fetched={raw.fetched_count} "
        f"lock_value={lock_value} lock_ttl={lock_ttl}"
    )
    return CheckResult("lock_held_skips_and_preserves_holder", passed, detail)


def check_lock_ttl_during_run(client: redis.Redis, ticker) -> CheckResult:
    """sync_news_for_ticker 실행 중 lock 키의 TTL이 600초 윈도우 내인지 확인.

    실행이 끝나면 lock은 해제되므로, 실행 중간 시점을 잡기 위해
    FakeArticleScraper 안에서 측정한다.
    """
    cleanup_keys(client, ticker.symbol)
    lock_key = NewsIngestionService._lock_key(ticker.symbol)
    captured = {"ttl": None, "value": None}

    class CapturingScraper:
        def scrape(self, url, timeout=(3.0, 7.0)):
            captured["ttl"] = client.ttl(lock_key)
            captured["value"] = client.get(lock_key)
            return build_scraped(url, "ttl capture", datetime.now(UTC))

    base = datetime.now(UTC).replace(microsecond=0)
    url = f"https://news.example.com/{ticker.symbol}/redis-integration/ttl"
    item = build_item(ticker, url, "redis lock ttl headline check", base)

    with SessionLocal() as session:
        service = NewsIngestionService(
            session,
            news_client=FakeNaverNewsClient([item]),
            article_scraper=CapturingScraper(),
            redis_client=client,
        )
        try:
            service.sync_news_for_ticker(ticker.symbol, mode="initial", force=True, limit=1)
        finally:
            session.rollback()

    cleanup_keys(client, ticker.symbol)

    ttl = captured["ttl"]
    value = captured["value"]
    passed = ttl is not None and 0 < ttl <= NewsIngestionService.LOCK_TTL_SECONDS and value is not None
    detail = f"captured_ttl={ttl} captured_value={value}"
    return CheckResult("lock_ttl_within_600s_window", passed, detail)


def check_cooldown_skip(client: redis.Redis, ticker) -> CheckResult:
    cleanup_keys(client, ticker.symbol)
    last_run_key = NewsIngestionService._last_run_key(ticker.symbol)
    # 마지막 실행이 5분 전 = 15분 윈도우 내 → 스킵되어야 함
    client.set(last_run_key, (datetime.now(UTC) - timedelta(minutes=5)).timestamp(), ex=86400)

    base = datetime.now(UTC).replace(microsecond=0)
    url = f"https://news.example.com/{ticker.symbol}/redis-integration/cooldown"
    item = build_item(ticker, url, "redis cooldown headline check", base)

    with SessionLocal() as session:
        service = make_service(session, ticker, [item], {}, client)
        try:
            raw = service.sync_news_for_ticker(ticker.symbol, mode="refresh", force=False, limit=1)
        finally:
            session.rollback()

    cleanup_keys(client, ticker.symbol)

    passed = raw.skipped_count == 1 and raw.fetched_count == 0 and raw.inserted_count == 0
    detail = f"skipped={raw.skipped_count} fetched={raw.fetched_count} inserted={raw.inserted_count}"
    return CheckResult("cooldown_within_window_skips", passed, detail)


def check_redis_unavailable_fails_closed(ticker) -> CheckResult:
    """Redis가 죽었을 때 sync가 fail-closed로 skip되는지 확인.

    redis://localhost:6390 — 컨테이너가 떠있지 않은 포트로 클라이언트를 만든다.
    redis-py는 lazy connect이므로 SET 호출 시점에 ConnectionError가 던져지고,
    NewsIngestionService._acquire_lock의 try/except가 None을 돌려줘서 skip 처리되어야 한다.
    """
    dead_client = redis.Redis.from_url(
        "redis://localhost:6390/0",
        decode_responses=True,
        socket_connect_timeout=1.0,
        socket_timeout=1.0,
    )

    base = datetime.now(UTC).replace(microsecond=0)
    url = f"https://news.example.com/{ticker.symbol}/redis-integration/dead"
    item = build_item(ticker, url, "redis dead fail closed headline check", base)
    articles = {url: build_scraped(url, item.title, base)}

    with SessionLocal() as session:
        service = make_service(session, ticker, [item], articles, dead_client)
        try:
            raw = service.sync_news_for_ticker(ticker.symbol, mode="initial", force=True, limit=1)
        finally:
            session.rollback()

    passed = (
        raw.skipped_count == 1
        and raw.fetched_count == 0
        and raw.inserted_count == 0
    )
    detail = (
        f"skipped={raw.skipped_count} fetched={raw.fetched_count} "
        f"inserted={raw.inserted_count}"
    )
    return CheckResult("redis_unavailable_fails_closed", passed, detail)


def check_cooldown_passed(client: redis.Redis, ticker) -> CheckResult:
    cleanup_keys(client, ticker.symbol)
    last_run_key = NewsIngestionService._last_run_key(ticker.symbol)
    # 마지막 실행이 16분 전 = 15분 윈도우 밖 → 통과해야 함
    client.set(last_run_key, (datetime.now(UTC) - timedelta(minutes=16)).timestamp(), ex=86400)

    base = datetime.now(UTC).replace(microsecond=0)
    url = f"https://news.example.com/{ticker.symbol}/redis-integration/cooldown-passed"
    item = build_item(ticker, url, "redis cooldown passed headline check", base)
    articles = {url: build_scraped(url, item.title, base)}

    with SessionLocal() as session:
        service = make_service(session, ticker, [item], articles, client)
        try:
            raw = service.sync_news_for_ticker(ticker.symbol, mode="refresh", force=False, limit=1)
        finally:
            session.rollback()

    cleanup_keys(client, ticker.symbol)

    passed = raw.fetched_count == 1 and raw.inserted_count == 1 and raw.skipped_count == 0
    detail = f"fetched={raw.fetched_count} inserted={raw.inserted_count} skipped={raw.skipped_count}"
    return CheckResult("cooldown_outside_window_runs", passed, detail)


def main() -> None:
    settings = get_settings()
    client = redis.Redis.from_url(settings.redis_url, decode_responses=True)

    with SessionLocal() as session:
        ticker = get_test_ticker(session)
    print(f"using ticker: symbol={ticker.symbol} name_kr={ticker.name_kr} market={ticker.market}")
    print(f"redis url: {settings.redis_url}")
    print()

    checks = [
        check_ping(client),
        check_normal_run(client, ticker),
        check_lock_already_held(client, ticker),
        check_lock_ttl_during_run(client, ticker),
        check_cooldown_skip(client, ticker),
        check_cooldown_passed(client, ticker),
        check_redis_unavailable_fails_closed(ticker),
    ]

    all_passed = True
    for c in checks:
        status = "PASS" if c.passed else "FAIL"
        print(f"[{status}] {c.name}")
        print(f"       {c.detail}")
        if not c.passed:
            all_passed = False

    print()
    print("ALL PASSED" if all_passed else "FAILED")


if __name__ == "__main__":
    main()
