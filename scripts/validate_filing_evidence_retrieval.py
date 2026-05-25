from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import func, select

from app.core.db import SessionLocal
from app.domain.evidence_indexing import EvidenceIndexingService
from app.external.chroma_client import ChromaClient, FILING_COLLECTION_NAME
from app.external.embedding import DeterministicEmbeddingClient
from app.models import FilingCache, TickerMetadata

FILING_VALIDATE_COLLECTION = "filing_validate_reindex"


@dataclass(slots=True)
class ValidationResult:
    symbol: str
    scanned_rows: int
    indexed_rows: int
    skipped_rows: int
    failed_rows: int
    collection_count: int
    fetched_id: str | None


class FakeDartClient:
    def __init__(self, payloads: dict[str, str]) -> None:
        self.payloads = payloads

    def fetch_filing_text(self, receipt_no: str) -> str:
        payload = self.payloads.get(receipt_no)
        if payload is None:
            raise RuntimeError(f"missing fake filing for {receipt_no}")
        return payload


def get_empty_cache_symbol(session) -> str:
    stmt = (
        select(TickerMetadata.symbol)
        .outerjoin(FilingCache, FilingCache.symbol == TickerMetadata.symbol)
        .group_by(TickerMetadata.symbol)
        .having(func.count(FilingCache.id) == 0)
        .order_by(TickerMetadata.symbol)
        .limit(1)
    )
    symbol = session.scalar(stmt)
    if symbol is None:
        raise RuntimeError("no empty-cache ticker available for filing evidence validation")
    return symbol


def main() -> None:
    chroma = ChromaClient()
    embeddings = DeterministicEmbeddingClient()

    with SessionLocal() as session:
        symbol = get_empty_cache_symbol(session)
        now = datetime.now(UTC).replace(microsecond=0)
        receipt_no = f"{now:%Y%m%d}{uuid4().hex[:6]}"
        row = FilingCache(
            symbol=symbol,
            filing_title=f"{symbol} filing evidence validation",
            filing_type="B",
            content=None,
            summary=None,
            dart_receipt_no=receipt_no,
            source_url=f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={receipt_no}",
            disclosed_at=now - timedelta(hours=2),
            retrieved_at=now - timedelta(hours=1),
            ttl_until=now + timedelta(days=7),
        )
        session.add(row)
        session.commit()
        row_uuid = row.id
        row_id = str(row.id)

    try:
        chroma.delete_collection(FILING_VALIDATE_COLLECTION)
        with SessionLocal() as session:
            service = EvidenceIndexingService(
                session,
                chroma_client=chroma,
                embedding_client=embeddings,
                dart_client=FakeDartClient(
                    {
                        receipt_no: "검증용 공시 본문입니다. 실적과 자금조달 계획을 설명합니다. " * 20,
                    }
                ),
            )
            result = service.reindex_filing_for_symbol(
                symbol,
                collection_name=FILING_VALIDATE_COLLECTION,
            )

        payload = chroma.get(FILING_VALIDATE_COLLECTION, ids=[row_id])
        fetched_id = payload["ids"][0] if payload.get("ids") else None
        print(
            json.dumps(
                asdict(
                    ValidationResult(
                        symbol=symbol,
                        scanned_rows=result.scanned_rows,
                        indexed_rows=result.indexed_rows,
                        skipped_rows=result.skipped_rows,
                        failed_rows=result.failed_rows,
                        collection_count=chroma.count(FILING_VALIDATE_COLLECTION),
                        fetched_id=fetched_id,
                    )
                ),
                ensure_ascii=False,
            )
        )
    finally:
        chroma.delete_collection(FILING_VALIDATE_COLLECTION)
        with SessionLocal() as session:
            db_row = session.get(FilingCache, row_uuid)
            if db_row is not None:
                session.delete(db_row)
                session.commit()


if __name__ == "__main__":
    main()
