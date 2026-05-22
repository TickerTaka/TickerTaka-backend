from __future__ import annotations

import argparse
import sys

from sqlalchemy import select

from app.core.db import session_scope
from app.domain.evidence_indexing import EvidenceIndexer, FILING_COLLECTION
from app.domain.evidence_retrieval import EvidenceRetriever
from app.external.chroma_client import ChromaClient
from app.models import FilingCache


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate filing evidence indexing and retrieval.")
    parser.add_argument("--symbol", required=True, help="ticker symbol, e.g. 005930")
    parser.add_argument("--query", default="매출 영업이익 실적")
    args = parser.parse_args()

    with session_scope() as session:
        row_count = session.scalar(
            select(FilingCache.id)
            .where(FilingCache.symbol == args.symbol)
            .where(FilingCache.dart_receipt_no.is_not(None))
            .limit(1)
        )
        if row_count is None:
            print(f"[FAIL] no filing_cache rows for symbol={args.symbol}")
            sys.exit(1)

        indexer = EvidenceIndexer(session)
        result = indexer.reindex_symbol(args.symbol)
        print(
            "[REINDEX] "
            f"source=filing symbol={args.symbol} rows={result.total} "
            f"indexed={result.indexed} skipped={result.skipped} failed={result.failed}"
        )
        for error in result.errors:
            print(f"[FAIL] {error}")

        chroma_count = ChromaClient().count(FILING_COLLECTION)
        print(f"[OK] chroma filing count={chroma_count}")
        if chroma_count == 0:
            print("[FAIL] Chroma filing collection is empty")
            sys.exit(1)

        retriever = EvidenceRetriever(session)
        items = retriever.retrieve_filings(args.symbol, args.query, limit=3)
        if not items:
            print("[FAIL] retrieval returned no evidence")
            sys.exit(1)

        for item in items:
            print(f"- score={item.score:.4f} title={item.title} url={item.source_url}")
            if "dart.fss.or.kr" not in item.source_url:
                print(f"[FAIL] non-DART source_url: {item.source_url}")
                sys.exit(1)

    print("[PASS] validate_filing_evidence_retrieval")


if __name__ == "__main__":
    main()
