from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Any

from sqlalchemy.orm import Session

from app.core.db import session_scope
from app.external.chroma_client import (
    ChromaClient,
    FILING_COLLECTION_NAME,
    NEWS_COLLECTION_NAME,
)
from app.external.embedding import EmbeddingClient, get_embedding_client
from app.models import FilingCache, NewsCache, SourceType
from app.repositories.filing_cache_repository import FilingCacheRepository
from app.repositories.news_cache_repository import NewsCacheRepository

logger = logging.getLogger(__name__)

_DEFAULT_EXCERPT_LENGTH = 320


@dataclass(slots=True)
class RetrievedEvidence:
    source_type: str
    source_title: str
    excerpt: str
    source_url: str
    source_label: str | None
    score: float
    news_cache_id: str | None = None
    filing_cache_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "source_type": self.source_type,
            "source_title": self.source_title,
            "excerpt": self.excerpt,
            "source_url": self.source_url,
            "source_label": self.source_label,
            "score": self.score,
        }
        if self.news_cache_id:
            payload["news_cache_id"] = self.news_cache_id
        if self.filing_cache_id:
            payload["filing_cache_id"] = self.filing_cache_id
        return payload


class EvidenceRetrievalService:
    def __init__(
        self,
        session: Session,
        *,
        chroma_client: ChromaClient | None = None,
        embedding_client: EmbeddingClient | None = None,
        news_collection_name: str = NEWS_COLLECTION_NAME,
        filing_collection_name: str = FILING_COLLECTION_NAME,
    ) -> None:
        self.session = session
        self.chroma_client = chroma_client or ChromaClient()
        self.embedding_client = embedding_client or get_embedding_client()
        self.news_repo = NewsCacheRepository(session)
        self.filing_repo = FilingCacheRepository(session)
        self.news_collection_name = news_collection_name
        self.filing_collection_name = filing_collection_name

    def search_symbol_evidence(
        self,
        *,
        query: str,
        symbol: str,
        top_k: int = 3,
    ) -> list[dict[str, Any]]:
        requested_k = max(1, top_k)
        # Query both sources, then merge by score to keep retrieval behavior simple.
        source_k = max(requested_k, 3)
        merged = [
            *self._search_news(query=query, symbol=symbol, limit=source_k),
            *self._search_filings(query=query, symbol=symbol, limit=source_k),
        ]
        merged.sort(key=lambda item: item.score)
        return [item.to_dict() for item in merged[:requested_k]]

    def _search_news(self, *, query: str, symbol: str, limit: int) -> list[RetrievedEvidence]:
        result = self._safe_query_collection(
            self.news_collection_name,
            query=query,
            symbol=symbol,
            limit=limit,
        )
        ids = self._first_list(result.get("ids"))
        documents = self._first_list(result.get("documents"))
        metadatas = self._first_list(result.get("metadatas"))
        distances = self._first_list(result.get("distances"))
        rows = self.news_repo.get_by_ids(ids)

        hits: list[RetrievedEvidence] = []
        for item_id, document, metadata, distance in zip(ids, documents, metadatas, distances, strict=False):
            row = rows.get(str(item_id))
            if row is None:
                continue
            hits.append(self._build_news_hit(row=row, document=document, metadata=metadata or {}, distance=distance))
        return hits

    def _search_filings(self, *, query: str, symbol: str, limit: int) -> list[RetrievedEvidence]:
        result = self._safe_query_collection(
            self.filing_collection_name,
            query=query,
            symbol=symbol,
            limit=limit,
        )
        ids = self._first_list(result.get("ids"))
        documents = self._first_list(result.get("documents"))
        metadatas = self._first_list(result.get("metadatas"))
        distances = self._first_list(result.get("distances"))
        rows = self.filing_repo.get_by_ids(ids)

        hits: list[RetrievedEvidence] = []
        for item_id, document, metadata, distance in zip(ids, documents, metadatas, distances, strict=False):
            row = rows.get(str(item_id))
            if row is None:
                continue
            hits.append(self._build_filing_hit(row=row, document=document, metadata=metadata or {}, distance=distance))
        return hits

    def _safe_query_collection(
        self,
        collection_name: str,
        *,
        query: str,
        symbol: str,
        limit: int,
    ) -> dict[str, Any]:
        try:
            return self.chroma_client.query(
                collection_name,
                query_text=query,
                embedding_client=self.embedding_client,
                where={"symbol": symbol},
                k=limit,
            )
        except Exception:
            logger.exception("evidence query failed for %s/%s", collection_name, symbol)
            return {}

    @staticmethod
    def _build_news_hit(
        *,
        row: NewsCache,
        document: str,
        metadata: dict[str, Any],
        distance: float | None,
    ) -> RetrievedEvidence:
        return RetrievedEvidence(
            source_type=SourceType.NEWS.value,
            source_title=row.title,
            excerpt=_excerpt_document(document, row.title),
            source_url=row.source_url,
            source_label=row.source_name or "NEWS",
            score=float(distance if distance is not None else 0.0),
            news_cache_id=str(row.id),
        )

    @staticmethod
    def _build_filing_hit(
        *,
        row: FilingCache,
        document: str,
        metadata: dict[str, Any],
        distance: float | None,
    ) -> RetrievedEvidence:
        label = row.filing_type or metadata.get("source_type") or "DART"
        return RetrievedEvidence(
            source_type=SourceType.DART.value,
            source_title=row.filing_title,
            excerpt=_excerpt_document(document, row.filing_title),
            source_url=row.source_url,
            source_label=str(label),
            score=float(distance if distance is not None else 0.0),
            filing_cache_id=str(row.id),
        )

    @staticmethod
    def _first_list(value: Any) -> list[Any]:
        if not value:
            return []
        if isinstance(value, list) and value and isinstance(value[0], list):
            return list(value[0])
        if isinstance(value, list):
            return list(value)
        return []


def search_evidence_for_symbol(
    *,
    query: str,
    symbol: str,
    top_k: int = 3,
) -> list[dict[str, Any]]:
    with session_scope() as session:
        service = EvidenceRetrievalService(session)
        return service.search_symbol_evidence(query=query, symbol=symbol, top_k=top_k)


def _excerpt_document(document: str, title: str | None) -> str:
    normalized = document.strip()
    if title and normalized.startswith(title):
        normalized = normalized[len(title) :].strip()
    if normalized.startswith("\n"):
        normalized = normalized.lstrip()
    if len(normalized) <= _DEFAULT_EXCERPT_LENGTH:
        return normalized
    return normalized[:_DEFAULT_EXCERPT_LENGTH].rstrip() + "..."
