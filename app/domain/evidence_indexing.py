from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.external.chroma_client import ChromaClient
from app.external.dart import DartApiError, DartClient
from app.external.embedding import EmbeddingClient
from app.models import FilingCache


FILING_COLLECTION = "filing"
_MIN_TEXT_LEN = 50


@dataclass
class IndexingResult:
    total: int = 0
    indexed: int = 0
    skipped: int = 0
    failed: int = 0
    errors: list[str] = field(default_factory=list)


class EvidenceIndexer:
    def __init__(
        self,
        session: Session,
        *,
        chroma: ChromaClient | None = None,
        embedder: EmbeddingClient | None = None,
        dart: DartClient | None = None,
    ) -> None:
        self.session = session
        self.chroma = chroma or ChromaClient()
        self.embedder = embedder or EmbeddingClient()
        self.dart = dart or DartClient()

    def index_filing_rows(self, rows: list[FilingCache], *, force: bool = False) -> IndexingResult:
        result = IndexingResult(total=len(rows))
        if not rows:
            return result

        doc_ids = [self._filing_doc_id(row) for row in rows]
        existing_ids = set() if force else self.chroma.get_existing_ids(FILING_COLLECTION, doc_ids)

        for row in rows:
            doc_id = self._filing_doc_id(row)
            if doc_id in existing_ids:
                result.skipped += 1
                continue

            if not row.dart_receipt_no:
                result.skipped += 1
                continue

            try:
                filing_text = self.dart.fetch_filing_text(row.dart_receipt_no)
            except DartApiError as exc:
                result.failed += 1
                result.errors.append(f"{row.dart_receipt_no}: {exc}")
                continue

            if len(filing_text.strip()) < _MIN_TEXT_LEN:
                result.failed += 1
                result.errors.append(f"{row.dart_receipt_no}: extracted text too short")
                continue

            document = f"{row.filing_title}\n\n{filing_text}"
            try:
                embedding = self.embedder.embed_texts([document])[0]
                self.chroma.upsert_documents(
                    FILING_COLLECTION,
                    ids=[doc_id],
                    documents=[document],
                    embeddings=[embedding],
                    metadatas=[self._filing_metadata(row)],
                )
            except Exception as exc:
                result.failed += 1
                result.errors.append(f"{row.dart_receipt_no}: {exc}")
                continue

            result.indexed += 1

        return result

    def reindex_symbol(self, symbol: str, *, force: bool = False) -> IndexingResult:
        rows = list(
            self.session.scalars(
                select(FilingCache)
                .where(FilingCache.symbol == symbol)
                .where(FilingCache.dart_receipt_no.is_not(None))
                .order_by(FilingCache.disclosed_at.desc().nullslast(), FilingCache.retrieved_at.desc())
            )
        )
        return self.index_filing_rows(rows, force=force)

    def reset_symbol(self, symbol: str) -> None:
        self.chroma.delete_by_symbol(FILING_COLLECTION, symbol)

    @staticmethod
    def _filing_doc_id(row: FilingCache) -> str:
        return f"filing:{row.id}"

    @staticmethod
    def _filing_metadata(row: FilingCache) -> dict:
        metadata = {
            "source_id": str(row.id),
            "source_type": "filing",
            "symbol": row.symbol,
            "dart_receipt_no": row.dart_receipt_no or "",
            "filing_title": row.filing_title,
            "source_url": row.source_url,
            "published_at": row.disclosed_at.isoformat() if row.disclosed_at else "",
            "chunk_idx": 0,
        }
        return {key: value for key, value in metadata.items() if value is not None}
