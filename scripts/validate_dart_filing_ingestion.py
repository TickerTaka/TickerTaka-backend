from __future__ import annotations

import argparse

from sqlalchemy import select

from app.core.db import session_scope
from app.domain.filing_ingestion import FilingIngestionService
from app.models import FilingCache, TickerMetadata


def get_default_symbol() -> str:
    with session_scope() as session:
        symbol = session.scalar(select(TickerMetadata.symbol).order_by(TickerMetadata.symbol).limit(1))
        if symbol is None:
            raise RuntimeError("ticker_metadata is empty")
        return symbol


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate DART filing ingestion for one symbol.")
    parser.add_argument("--symbol", default=None, help="ticker symbol, e.g. 005930")
    parser.add_argument("--lookback-days", type=int, default=30)
    parser.add_argument("--limit", type=int, default=10)
    args = parser.parse_args()

    symbol = args.symbol or get_default_symbol()

    with session_scope() as session:
        service = FilingIngestionService(session)
        result = service.sync_filings_for_ticker(
            symbol,
            lookback_days=args.lookback_days,
            limit=args.limit,
        )
        rows = [
            (
                row.disclosed_at,
                row.dart_receipt_no,
                row.filing_title,
                row.source_url,
            )
            for row in session.scalars(
                select(FilingCache)
                .where(FilingCache.symbol == symbol)
                .order_by(FilingCache.disclosed_at.desc().nullslast(), FilingCache.retrieved_at.desc())
                .limit(5)
            )
        ]

    print(
        "[RESULT] "
        f"symbol={symbol} fetched={result.fetched_count} inserted={result.inserted_count} "
        f"updated={result.updated_count} skipped={result.skipped_count} elapsed_ms={result.elapsed_ms}"
    )
    for disclosed_at, receipt_no, filing_title, source_url in rows:
        print(f"- {disclosed_at} {receipt_no} {filing_title} {source_url}")


if __name__ == "__main__":
    main()
