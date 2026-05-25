from __future__ import annotations

from sqlalchemy import select

from app.core.db import session_scope
from app.domain.evidence_indexing import EvidenceIndexingService
from app.models import FilingCache


def main() -> None:
    results: list[dict[str, int | str]] = []
    with session_scope() as session:
        symbols = list(
            session.scalars(
                select(FilingCache.symbol).distinct().order_by(FilingCache.symbol)
            )
        )
        service = EvidenceIndexingService(session)
        for symbol in symbols:
            result = service.reindex_filing_for_symbol(symbol)
            results.append(
                {
                    "symbol": result.symbol,
                    "scanned_rows": result.scanned_rows,
                    "indexed_rows": result.indexed_rows,
                    "skipped_rows": result.skipped_rows,
                    "failed_rows": result.failed_rows,
                }
            )
    print({"count": len(results), "results": results})


if __name__ == "__main__":
    main()
