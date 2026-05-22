from __future__ import annotations

import argparse
import json
from dataclasses import asdict

from app.core.db import session_scope
from app.domain.evidence_indexing import EvidenceIndexingService
from app.external.chroma_client import NEWS_COLLECTION_NAME
from app.repositories.watchlist_repository import WatchlistRepository


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Reindex local ChromaDB from PostgreSQL cache rows.")
    parser.add_argument("--symbol", help="Single ticker symbol to reindex")
    parser.add_argument("--source", default="news", choices=["news"])
    parser.add_argument("--reset", action="store_true", help="Delete existing documents for target symbol first")
    parser.add_argument("--force", action="store_true", help="Force metadata marker only; kept for interface stability")
    parser.add_argument("--all-watchlist", action="store_true", help="Reindex distinct watchlist symbols")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    with session_scope() as session:
        service = EvidenceIndexingService(session)
        symbols: list[str] = []

        if args.symbol:
            symbols = [args.symbol]
        elif args.all_watchlist:
            symbols = WatchlistRepository(session).list_distinct_symbols()
        else:
            raise SystemExit("either --symbol or --all-watchlist is required")

        if args.reset:
            service.chroma_client.delete_collection(NEWS_COLLECTION_NAME)

        for symbol in symbols:
            result = service.reindex_news_for_symbol(symbol, reset=False, force=args.force)
            print(json.dumps(asdict(result), ensure_ascii=False))


if __name__ == "__main__":
    main()
