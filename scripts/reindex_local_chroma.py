from __future__ import annotations

import argparse
import sys

from app.core.db import session_scope
from app.domain.evidence_indexing import EvidenceIndexer


def main() -> None:
    parser = argparse.ArgumentParser(description="Reindex DART filings into local ChromaDB.")
    parser.add_argument("--symbol", required=True, help="ticker symbol, e.g. 005930")
    parser.add_argument("--force", action="store_true", help="re-fetch and overwrite existing documents")
    parser.add_argument("--reset", action="store_true", help="delete existing symbol documents before indexing")
    args = parser.parse_args()

    with session_scope() as session:
        indexer = EvidenceIndexer(session)
        if args.reset:
            indexer.reset_symbol(args.symbol)
        result = indexer.reindex_symbol(args.symbol, force=args.force or args.reset)

    print(
        "[REINDEX] "
        f"source=filing symbol={args.symbol} rows={result.total} "
        f"indexed={result.indexed} skipped={result.skipped} failed={result.failed}"
    )
    for error in result.errors:
        print(f"[FAIL] {error}")

    if result.failed > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
