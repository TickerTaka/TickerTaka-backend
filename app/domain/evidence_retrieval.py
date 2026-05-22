from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.evidence_indexing import FILING_COLLECTION
from app.external.chroma_client import ChromaClient
from app.external.embedding import EmbeddingClient
from app.models import FilingCache


@dataclass
class EvidenceItem:
    source_id: str
    source_type: str
    symbol: str
    title: str
    source_url: str
    text: str
    score: float


class EvidenceRetriever:
    def __init__(
        self,
        session: Session,
        *,
        chroma: ChromaClient | None = None,
        embedder: EmbeddingClient | None = None,
    ) -> None:
        self.session = session
        self.chroma = chroma or ChromaClient()
        self.embedder = embedder or EmbeddingClient()

    def retrieve_filings(self, symbol: str, query: str, limit: int = 5) -> list[EvidenceItem]:
        query_embedding = self.embedder.embed_query(query)
        result = self.chroma.query(
            FILING_COLLECTION,
            query_embeddings=[query_embedding],
            n_results=limit,
            where={"symbol": symbol},
        )

        ids = result.get("ids", [[]])[0] or []
        documents = result.get("documents", [[]])[0] or []
        metadatas = result.get("metadatas", [[]])[0] or []
        distances = result.get("distances", [[]])[0] or []

        source_ids = [
            str(metadata.get("source_id"))
            for metadata in metadatas
            if metadata and metadata.get("source_id")
        ]
        filing_rows = self._get_filing_rows_by_ids(source_ids)

        items: list[EvidenceItem] = []
        for idx, metadata in enumerate(metadatas):
            metadata = metadata or {}
            source_id = str(metadata.get("source_id") or "")
            row = filing_rows.get(source_id)
            distance = distances[idx] if idx < len(distances) and distances[idx] is not None else 1.0

            items.append(
                EvidenceItem(
                    source_id=source_id,
                    source_type=str(metadata.get("source_type") or "filing"),
                    symbol=str(metadata.get("symbol") or symbol),
                    title=row.filing_title if row else str(metadata.get("filing_title") or ""),
                    source_url=row.source_url if row else str(metadata.get("source_url") or ""),
                    text=documents[idx] if idx < len(documents) else "",
                    score=1.0 - float(distance),
                )
            )
        return items

    def _get_filing_rows_by_ids(self, source_ids: list[str]) -> dict[str, FilingCache]:
        uuids: list[UUID] = []
        for source_id in source_ids:
            try:
                uuids.append(UUID(source_id))
            except (TypeError, ValueError):
                continue

        if not uuids:
            return {}

        rows = self.session.scalars(select(FilingCache).where(FilingCache.id.in_(uuids))).all()
        return {str(row.id): row for row in rows}
