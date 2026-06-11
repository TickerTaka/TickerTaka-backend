from __future__ import annotations

from dataclasses import dataclass, replace
from functools import lru_cache
import logging
import re
from typing import Any

from rank_bm25 import BM25Okapi
from sqlalchemy.orm import Session

from app.config import get_settings
from app.core.db import session_scope
from app.external.chroma_client import (
    ChromaClient,
    FILING_COLLECTION_NAME,
    NEWS_COLLECTION_NAME,
)
from app.external.embedding import EmbeddingClient, get_embedding_client
from app.models import FilingCache, NewsCache, SourceType
from app.repositories.evidence_analysis_repository import EvidenceAnalysisRepository
from app.repositories.filing_cache_repository import FilingCacheRepository
from app.repositories.news_cache_repository import NewsCacheRepository

logger = logging.getLogger(__name__)

_DEFAULT_EXCERPT_LENGTH = 320
_TOKEN_PATTERN = re.compile(r"[0-9A-Za-z가-힣_]+")
_CATEGORY_QUERY_MAP = {
    "technical": "{symbol_name} 기술적 분석 차트 이동평균 RSI MACD 거래량 추세",
    "financial": "{symbol_name} 실적 재무 분기보고서 매출 영업이익 순이익 부채",
    "market": "{symbol_name} 업황 경쟁사 수요 공급 시장 반응 뉴스 공시",
    "macro": "{symbol_name} 거시경제 금리 환율 경기 사이클 업황",
    "synthesis": "{symbol_name} 투자 판단 핵심 리스크 성장 실적 시장",
}


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
    sentiment: str | None = None
    impact_score: int | None = None
    analysis_summary: str | None = None
    key_points: list[str] | None = None
    risks: list[str] | None = None
    rank: int | None = None
    score_type: str | None = None

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
        if self.sentiment is not None:
            payload["sentiment"] = self.sentiment
        if self.impact_score is not None:
            payload["impact_score"] = self.impact_score
        if self.analysis_summary:
            payload["analysis_summary"] = self.analysis_summary
        if self.key_points is not None:
            payload["key_points"] = self.key_points
        if self.risks is not None:
            payload["risks"] = self.risks
        if self.rank is not None:
            payload["rank"] = self.rank
        if self.score_type is not None:
            payload["score_type"] = self.score_type
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
        self.analysis_repo = EvidenceAnalysisRepository(session)
        self.news_collection_name = news_collection_name
        self.filing_collection_name = filing_collection_name

    def search_symbol_evidence(
        self,
        *,
        query: str,
        symbol: str,
        top_k: int = 3,
    ) -> list[dict[str, Any]]:
        settings = get_settings()
        requested_k = max(1, top_k)
        source_k = max(requested_k, 3)

        vector_news = self._search_news(query=query, symbol=symbol, limit=source_k)
        vector_filings = self._search_filings(query=query, symbol=symbol, limit=source_k)

        if not settings.rag_hybrid_enabled:
            merged = [*vector_news, *vector_filings]
            merged.sort(key=lambda item: item.score)
            return [item.to_dict() for item in _assign_ranks(merged[:requested_k])]

        lexical_limit = max(source_k, settings.rag_lexical_candidate_limit)
        lexical_news = self._search_news_lexical(query=query, symbol=symbol, limit=lexical_limit)
        lexical_filings = self._search_filings_lexical(query=query, symbol=symbol, limit=lexical_limit)

        fused = self._fuse_rankings(
            ranked_lists=[vector_news, vector_filings, lexical_news, lexical_filings],
            rrf_k=max(1, settings.rag_rrf_k),
        )
        reranked = self._maybe_rerank(query=query, hits=fused, top_k=requested_k)
        return [item.to_dict() for item in _assign_ranks(reranked[:requested_k])]

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
        analyses = self._safe_get_analyses("news", list(rows.keys()))

        hits: list[RetrievedEvidence] = []
        for item_id, document, metadata, distance in zip(ids, documents, metadatas, distances, strict=False):
            row = rows.get(str(item_id))
            if row is None:
                continue
            hits.append(
                self._build_news_hit(
                    row=row,
                    document=document,
                    metadata=metadata or {},
                    distance=distance,
                    analysis=analyses.get(str(row.id)),
                )
            )
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
        source_ids = [
            self._source_id_for_hit(item_id=item_id, metadata=metadata)
            for item_id, metadata in zip(ids, metadatas, strict=False)
        ]
        rows = self.filing_repo.get_by_ids(source_ids)
        analyses = self._safe_get_analyses("filing", list(rows.keys()))

        hits: list[RetrievedEvidence] = []
        for item_id, document, metadata, distance in zip(ids, documents, metadatas, distances, strict=False):
            source_id = self._source_id_for_hit(item_id=item_id, metadata=metadata)
            row = rows.get(str(source_id))
            if row is None:
                continue
            hits.append(
                self._build_filing_hit(
                    row=row,
                    document=document,
                    metadata=metadata or {},
                    distance=distance,
                    analysis=analyses.get(str(row.id)),
                )
            )
        return hits

    def _search_news_lexical(self, *, query: str, symbol: str, limit: int) -> list[RetrievedEvidence]:
        rows = self.news_repo.list_by_symbol(symbol)[:limit]
        analyses = self._safe_get_analyses("news", [str(row.id) for row in rows])
        candidates = [
            (
                str(row.id),
                _news_lexical_document(row),
                row,
                analyses.get(str(row.id)),
            )
            for row in rows
            if _news_lexical_document(row)
        ]
        return self._run_bm25(
            query=query,
            candidates=candidates,
            builder=lambda row, document, score, analysis: RetrievedEvidence(
                source_type=SourceType.NEWS.value,
                source_title=row.title,
                excerpt=_excerpt_document(document, row.title),
                source_url=row.source_url,
                source_label=row.source_name or "NEWS",
                score=score,
                news_cache_id=str(row.id),
                score_type="bm25",
                **_analysis_kwargs(analysis),
            ),
        )

    def _search_filings_lexical(self, *, query: str, symbol: str, limit: int) -> list[RetrievedEvidence]:
        rows = self.filing_repo.list_by_symbol(symbol)[:limit]
        analyses = self._safe_get_analyses("filing", [str(row.id) for row in rows])
        candidates = [
            (
                str(row.id),
                _filing_lexical_document(row),
                row,
                analyses.get(str(row.id)),
            )
            for row in rows
            if _filing_lexical_document(row)
        ]
        return self._run_bm25(
            query=query,
            candidates=candidates,
            builder=lambda row, document, score, analysis: RetrievedEvidence(
                source_type=SourceType.DART.value,
                source_title=row.filing_title,
                excerpt=_excerpt_document(document, row.filing_title),
                source_url=row.source_url,
                source_label=row.filing_type or "DART",
                score=score,
                filing_cache_id=str(row.id),
                score_type="bm25",
                **_analysis_kwargs(analysis),
            ),
        )

    def _run_bm25(
        self,
        *,
        query: str,
        candidates: list[tuple[str, str, Any, Any]],
        builder,
    ) -> list[RetrievedEvidence]:
        if not candidates:
            return []
        tokenized_docs = [_tokenize(text) for _, text, _, _ in candidates]
        query_tokens = _tokenize(query)
        if not query_tokens or not any(tokenized_docs):
            return []
        bm25 = BM25Okapi(tokenized_docs)
        scores = bm25.get_scores(query_tokens)
        ranked_pairs = sorted(
            zip(candidates, scores, strict=False),
            key=lambda pair: pair[1],
            reverse=True,
        )
        hits: list[RetrievedEvidence] = []
        for (item_id, document, row, analysis), score in ranked_pairs:
            if score <= 0:
                continue
            hits.append(builder(row, document, float(score), analysis))
        return hits

    def _fuse_rankings(
        self,
        *,
        ranked_lists: list[list[RetrievedEvidence]],
        rrf_k: int,
    ) -> list[RetrievedEvidence]:
        fused_scores: dict[str, float] = {}
        canonical_hits: dict[str, RetrievedEvidence] = {}

        for ranked in ranked_lists:
            for index, hit in enumerate(ranked, start=1):
                key = _hit_key(hit)
                fused_scores[key] = fused_scores.get(key, 0.0) + 1.0 / (rrf_k + index)
                canonical_hits.setdefault(key, hit)

        merged: list[RetrievedEvidence] = []
        for key, score in sorted(fused_scores.items(), key=lambda item: item[1], reverse=True):
            hit = canonical_hits[key]
            merged.append(_replace_hit(hit, score=score, score_type="rrf"))
        return merged

    def _maybe_rerank(
        self,
        *,
        query: str,
        hits: list[RetrievedEvidence],
        top_k: int,
    ) -> list[RetrievedEvidence]:
        settings = get_settings()
        if not settings.rag_reranker_enabled or not hits:
            return hits[:top_k]

        rerank_limit = max(top_k, settings.rag_reranker_top_n)
        rerank_targets = hits[:rerank_limit]
        try:
            model = _get_reranker(settings.rag_reranker_model)
            pairs = [(query, f"{hit.source_title}\n{hit.excerpt}") for hit in rerank_targets]
            scores = model.predict(pairs)
        except Exception:
            logger.exception("reranker failed; falling back to RRF ranking")
            return hits[:top_k]

        reranked = sorted(
            zip(rerank_targets, scores, strict=False),
            key=lambda pair: float(pair[1]),
            reverse=True,
        )
        merged = [_replace_hit(hit, score=float(score), score_type="reranker") for hit, score in reranked]
        if len(hits) > rerank_limit:
            merged.extend(hits[rerank_limit:])
        return merged[:top_k]

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

    def _safe_get_analyses(self, source_type: str, source_ids: list[str]) -> dict[str, Any]:
        try:
            return self.analysis_repo.get_by_sources(source_type, source_ids)
        except Exception:
            logger.exception("evidence analysis lookup failed for %s", source_type)
            return {}

    @staticmethod
    def _build_news_hit(
        *,
        row: NewsCache,
        document: str,
        metadata: dict[str, Any],
        distance: float | None,
        analysis: Any = None,
    ) -> RetrievedEvidence:
        return RetrievedEvidence(
            source_type=SourceType.NEWS.value,
            source_title=row.title,
            excerpt=_excerpt_document(document, row.title),
            source_url=row.source_url,
            source_label=row.source_name or "NEWS",
            score=float(distance if distance is not None else 0.0),
            news_cache_id=str(row.id),
            score_type="distance",
            **_analysis_kwargs(analysis),
        )

    @staticmethod
    def _build_filing_hit(
        *,
        row: FilingCache,
        document: str,
        metadata: dict[str, Any],
        distance: float | None,
        analysis: Any = None,
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
            score_type="distance",
            **_analysis_kwargs(analysis),
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

    @staticmethod
    def _source_id_for_hit(*, item_id: Any, metadata: Any) -> Any:
        if isinstance(metadata, dict) and metadata.get("source_id"):
            return metadata["source_id"]
        if isinstance(item_id, str) and ":s" in item_id:
            return item_id.split(":s", 1)[0]
        return item_id


def search_evidence_for_symbol(
    *,
    query: str,
    symbol: str,
    top_k: int = 3,
) -> list[dict[str, Any]]:
    with session_scope() as session:
        service = EvidenceRetrievalService(session)
        return service.search_symbol_evidence(query=query, symbol=symbol, top_k=top_k)


def build_category_query(
    *,
    symbol: str,
    symbol_name: str,
    category: str,
) -> str:
    template = _CATEGORY_QUERY_MAP.get(category, _CATEGORY_QUERY_MAP["synthesis"])
    return template.format(symbol=symbol, symbol_name=symbol_name or symbol)


def format_evidence_context(evidences: list[dict[str, Any]]) -> str:
    if not evidences:
        return "[근거 검색] 관련 뉴스/공시 근거 없음"

    lines = ["[근거 검색 요약]"]
    for index, evidence in enumerate(evidences, start=1):
        source_type = evidence.get("source_type", "OTHER")
        title = evidence.get("source_title", "제목 없음")
        excerpt = evidence.get("excerpt", "")
        label = evidence.get("source_label") or source_type
        sentiment = evidence.get("sentiment")
        impact_score = evidence.get("impact_score")
        analysis_summary = evidence.get("analysis_summary")
        header_parts = [f"{index}. [{source_type}]"]
        if sentiment:
            header_parts.append(f"[{sentiment}]")
        if impact_score is not None:
            header_parts.append(f"[영향도 {int(impact_score):+d}]")
        header_parts.append(f"{title} ({label})")
        lines.append(" ".join(header_parts))
        if analysis_summary:
            lines.append(f"   요약: {analysis_summary}")
        key_points = evidence.get("key_points") or []
        if key_points:
            lines.append(f"   핵심 근거: {', '.join(map(str, key_points[:3]))}")
        risks = evidence.get("risks") or []
        if risks:
            lines.append(f"   리스크: {', '.join(map(str, risks[:3]))}")
        if excerpt:
            lines.append(f"   원문 발췌: {excerpt}")
    return "\n".join(lines)


def _excerpt_document(document: str, title: str | None) -> str:
    normalized = document.strip()
    if title and normalized.startswith(title):
        normalized = normalized[len(title) :].strip()
    if normalized.startswith("\n"):
        normalized = normalized.lstrip()
    if len(normalized) <= _DEFAULT_EXCERPT_LENGTH:
        return normalized
    return normalized[:_DEFAULT_EXCERPT_LENGTH].rstrip() + "..."


def _analysis_kwargs(analysis: Any) -> dict[str, Any]:
    if analysis is None:
        return {}
    return {
        "sentiment": analysis.sentiment,
        "impact_score": analysis.impact_score,
        "analysis_summary": analysis.summary,
        "key_points": list(analysis.key_points or []),
        "risks": list(analysis.risks or []),
    }


def _tokenize(text: str) -> list[str]:
    normalized = text.strip().lower()
    if not normalized:
        return []
    return _TOKEN_PATTERN.findall(normalized)


def _news_lexical_document(row: NewsCache) -> str:
    return "\n".join(part for part in [row.title, row.summary or ""] if part).strip()


def _filing_lexical_document(row: FilingCache) -> str:
    return "\n".join(part for part in [row.filing_title, row.summary or "", row.content or ""] if part).strip()


def _hit_key(hit: RetrievedEvidence) -> str:
    if hit.news_cache_id:
        return f"news:{hit.news_cache_id}"
    if hit.filing_cache_id:
        return f"filing:{hit.filing_cache_id}"
    return f"{hit.source_type}:{hit.source_url}"


def _replace_hit(
    hit: RetrievedEvidence,
    *,
    score: float | None = None,
    score_type: str | None = None,
    rank: int | None = None,
) -> RetrievedEvidence:
    updates: dict[str, Any] = {}
    if score is not None:
        updates["score"] = score
    if score_type is not None:
        updates["score_type"] = score_type
    if rank is not None:
        updates["rank"] = rank
    return replace(hit, **updates)


def _assign_ranks(hits: list[RetrievedEvidence]) -> list[RetrievedEvidence]:
    return [_replace_hit(hit, rank=index) for index, hit in enumerate(hits, start=1)]


@lru_cache(maxsize=4)
def _get_reranker(model_name: str):
    from sentence_transformers import CrossEncoder

    return CrossEncoder(model_name)
