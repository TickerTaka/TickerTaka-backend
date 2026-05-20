from __future__ import annotations

import argparse
from dataclasses import asdict
import json

from app.domain.news_cache_scheduler import (
    run_scheduled_news_cleanup,
    run_scheduled_watchlist_refresh,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run scheduled news cache maintenance.")
    parser.add_argument(
        "--mode",
        choices=("refresh", "cleanup", "all"),
        default="all",
        help="Which scheduler path to run.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Bypass per-symbol cooldown during refresh.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Override refresh fetch limit per symbol.",
    )
    args = parser.parse_args()

    if args.mode in {"refresh", "all"}:
        refresh_result = run_scheduled_watchlist_refresh(force=args.force, limit=args.limit)
        print(json.dumps({"mode": "refresh", **asdict(refresh_result)}, ensure_ascii=False))

    if args.mode in {"cleanup", "all"}:
        cleanup_result = run_scheduled_news_cleanup()
        print(json.dumps({"mode": "cleanup", **asdict(cleanup_result)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
