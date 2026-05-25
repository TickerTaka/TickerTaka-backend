from __future__ import annotations

import argparse
import json
from dataclasses import asdict

from app.domain.financial_cache_scheduler import FinancialCacheSchedulerService


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("refresh", "cleanup"), default="refresh")
    args = parser.parse_args()

    scheduler = FinancialCacheSchedulerService()
    if args.mode == "refresh":
        result = scheduler.run_watchlist_refresh()
    else:
        result = scheduler.run_cleanup()
    print(json.dumps({"mode": args.mode, **asdict(result)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
