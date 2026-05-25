"""Live test: SK하이닉스(000660) watchlist 등록 후 background sync로 실제 DB 적재 확인.

전제:
- `python -m scripts.seed`로 phase2-test-user@example.com 시드 완료
- ticker_metadata에 000660 존재
- Redis 컨테이너 기동 (`docker compose up -d redis`)
- NAVER_NEWS_CLIENT_ID / NAVER_NEWS_CLIENT_SECRET 설정 (.env 또는 .env.local)

사용법:
- 기본 실행 (sync 후 적재 결과 보여주고 종료, row는 남겨둠):
    python -m scripts.live_test_watchlist_sync
  → DB에서 직접 확인 가능

- 정리만 실행 (watchlist + news_cache row 삭제):
    python -m scripts.live_test_watchlist_sync --cleanup
"""
from __future__ import annotations

import argparse
import logging

from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.config import get_settings
from app.core.db import session_scope
from app.domain.news_ingestion import NewsIngestionService
from app.main import app
from app.models import AppUser, NewsCache, TickerMetadata, Watchlist

SEED_EMAIL = "phase2-test-user@example.com"
SYMBOL = "000660"


class ExtraFormatter(logging.Formatter):
    """Formatter that surfaces structured log extras inline."""

    EXTRA_KEYS = (
        "symbol",
        "fetched",
        "inserted",
        "updated",
        "skipped",
        "filtered",
        "body_failed",
        "grouped",
        "body_quota_saved",
        "body_attempts",
        "body_saved",
        "trimmed_rows",
        "elapsed_ms",
    )

    def format(self, record: logging.LogRecord) -> str:
        base = super().format(record)
        extras = {key: getattr(record, key) for key in self.EXTRA_KEYS if hasattr(record, key)}
        if extras:
            extras_str = " ".join(f"{key}={value}" for key, value in extras.items())
            return f"{base} | {extras_str}"
        return base


def configure_logging() -> None:
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    handler = logging.StreamHandler()
    handler.setFormatter(ExtraFormatter("%(asctime)s %(name)s %(levelname)s %(message)s"))
    root.handlers = [handler]
    for noisy in ("sqlalchemy", "sqlalchemy.engine", "urllib3", "httpx", "httpcore", "asyncio"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_user_id():
    with session_scope() as session:
        user = session.scalar(select(AppUser).where(AppUser.email == SEED_EMAIL))
        if user is None:
            raise RuntimeError(
                f"seed user not found: {SEED_EMAIL}. run `python -m scripts.seed` first."
            )
        return user.id


def verify_ticker() -> str:
    with session_scope() as session:
        ticker = session.get(TickerMetadata, SYMBOL)
        if ticker is None:
            raise RuntimeError(
                f"ticker {SYMBOL} not found in ticker_metadata. import metadata first."
            )
        return ticker.name_kr


def ensure_redis_alive() -> None:
    client = NewsIngestionService._build_redis_client(get_settings().redis_url)
    if client is None:
        raise RuntimeError(
            "redis client unavailable. start `docker compose up -d redis` and retry."
        )
    try:
        client.ping()
    except Exception as exc:
        raise RuntimeError(f"redis ping failed: {exc}") from exc


def cleanup(user_id) -> tuple[int, int]:
    with session_scope() as session:
        watchlist_result = session.execute(
            delete(Watchlist).where(
                Watchlist.user_id == user_id, Watchlist.symbol == SYMBOL
            )
        )
        news_result = session.execute(delete(NewsCache).where(NewsCache.symbol == SYMBOL))
    return int(watchlist_result.rowcount or 0), int(news_result.rowcount or 0)


def fetch_rows() -> list[NewsCache]:
    with session_scope() as session:
        rows = list(
            session.scalars(
                select(NewsCache)
                .where(NewsCache.symbol == SYMBOL)
                .order_by(
                    NewsCache.published_at.desc().nullslast(),
                    NewsCache.retrieved_at.desc(),
                )
            )
        )
        for row in rows:
            session.expunge(row)
    return rows


def run_sync_cycle() -> None:
    name_kr = verify_ticker()
    ensure_redis_alive()
    user_id = get_user_id()
    print(f"target  : {SYMBOL} ({name_kr})")
    print(f"user_id : {user_id}\n")

    print("[1/3] cleanup before ...")
    wl, nc = cleanup(user_id)
    print(f"        cleared watchlist={wl} news_cache={nc}\n")

    print("[2/3] POST /api/watchlists ...")
    with TestClient(app) as client:
        response = client.post(
            "/api/watchlists",
            json={
                "user_id": str(user_id),
                "symbol": SYMBOL,
                "memo": "live test SK하이닉스",
            },
        )
    if response.status_code != 201:
        raise RuntimeError(
            f"watchlist create failed: {response.status_code} {response.text}"
        )
    body = response.json()
    print(f"        status={response.status_code} sync_enqueued={body['sync_enqueued']}")
    print(
        f"        watchlist_id={body['watchlist']['id']} "
        f"ticker_name_kr={body['watchlist']['ticker_name_kr']}\n"
    )

    print("[3/3] news_cache 적재 결과 ...")
    rows = fetch_rows()
    content_rows = sum(1 for r in rows if r.content)
    sources = sorted({r.source_name for r in rows if r.source_name})
    print(f"        total_rows         = {len(rows)}")
    print(f"        content_not_null   = {content_rows} / {len(rows)}  (option B expects 0)")
    print(f"        distinct_sources   = {len(sources)} -> {sources}")
    print()
    print("--- 최근 5건 ---")
    for row in rows[:5]:
        published = row.published_at.isoformat() if row.published_at else "(no pub_at)"
        content_len = len(row.content or "")
        print(f"- [{published}] {row.source_name or '(unknown)'}")
        print(f"  title  : {row.title}")
        print(f"  url    : {row.source_url}")
        print(f"  content: {content_len}자 (option B expects 0)")

    print()
    print("적재된 row는 DB에 그대로 남아있습니다.")
    print("DB에서 직접 확인한 뒤 정리하려면:")
    print("    python -m scripts.live_test_watchlist_sync --cleanup")


def run_cleanup_only() -> None:
    user_id = get_user_id()
    print(f"cleanup target: {SYMBOL} for user_id={user_id}")
    wl, nc = cleanup(user_id)
    print(f"cleared watchlist={wl} news_cache={nc}")
    print("done.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--cleanup",
        action="store_true",
        help="watchlist + news_cache 정리만 수행하고 종료",
    )
    args = parser.parse_args()

    configure_logging()

    if args.cleanup:
        run_cleanup_only()
    else:
        run_sync_cycle()


if __name__ == "__main__":
    main()
