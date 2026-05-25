from __future__ import annotations

import argparse
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select

from app.core.db import SessionLocal
from app.domain.news_ingestion import NewsIngestionService
from app.models import NewsCache, TickerMetadata


@dataclass
class InMemoryRedis:
    store: dict[str, str]

    def __init__(self) -> None:
        self.store = {}

    def set(self, key: str, value: str | float, nx: bool = False, ex: int | None = None) -> bool:
        if nx and key in self.store:
            return False
        self.store[key] = str(value)
        return True

    def get(self, key: str) -> str | None:
        return self.store.get(key)

    def delete(self, key: str) -> int:
        return int(self.store.pop(key, None) is not None)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run live Naver/article sync for one symbol.")
    parser.add_argument("--symbol", default="005930", help="Ticker symbol to sync")
    parser.add_argument("--mode", default="initial", choices=["initial", "refresh"])
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--commit", action="store_true", help="Commit DB changes instead of rollback")
    parser.add_argument(
        "--use-real-redis",
        action="store_true",
        help="Use configured Redis client instead of in-memory test client",
    )
    return parser.parse_args()


def print_rows(rows: list[NewsCache]) -> None:
    for index, row in enumerate(rows, start=1):
        print(
            {
                "n": index,
                "title": row.title[:80],
                "source_name": row.source_name,
                "source_url": row.source_url,
                "published_at": row.published_at.isoformat() if row.published_at else None,
                "content_is_null": row.content is None,
                "summary_len": len(row.summary) if row.summary else 0,
            }
        )


def main() -> None:
    args = parse_args()
    with SessionLocal() as session:
        ticker = session.get(TickerMetadata, args.symbol)
        if ticker is None:
            raise SystemExit(f"ticker not found: {args.symbol}")

        service_kwargs: dict[str, Any] = {}
        if not args.use_real_redis:
            service_kwargs["redis_client"] = InMemoryRedis()

        service = NewsIngestionService(session, **service_kwargs)
        result = service.sync_news_for_ticker(
            args.symbol,
            mode=args.mode,
            force=True,
            limit=args.limit,
        )

        rows = list(
            session.scalars(
                select(NewsCache)
                .where(NewsCache.symbol == args.symbol)
                .order_by(NewsCache.retrieved_at.desc(), NewsCache.published_at.desc().nullslast())
                .limit(10)
            )
        )

        print(
            {
                "symbol": ticker.symbol,
                "name_kr": ticker.name_kr,
                "mode": args.mode,
                "limit": args.limit,
                "commit": args.commit,
                "use_real_redis": args.use_real_redis,
            }
        )
        print(
            {
                "fetched": result.fetched_count,
                "inserted": result.inserted_count,
                "updated": result.updated_count,
                "skipped": result.skipped_count,
                "filtered": result.filtered_count,
                "body_failed": result.body_failed_count,
                "grouped": result.grouped_count,
                "body_saved": result.body_saved_count,
                "trimmed_rows": result.trimmed_rows_count,
                "elapsed_ms": result.elapsed_ms,
                "policy": "option_b_pg_content_null",
            }
        )
        print_rows(rows)

        if args.commit:
            session.commit()
            print("COMMIT")
        else:
            session.rollback()
            print("ROLLBACK")


if __name__ == "__main__":
    main()
