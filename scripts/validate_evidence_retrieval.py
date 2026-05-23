from __future__ import annotations

from dataclasses import asdict
from uuid import uuid4

from app.core.db import SessionLocal
from app.domain.evidence_indexing import EvidenceIndexingService
from app.domain.evidence_retrieval import EvidenceRetrievalService
from app.external.chroma_client import ChromaClient, FILING_COLLECTION_NAME, NEWS_COLLECTION_NAME
from app.external.embedding import DeterministicEmbeddingClient
from app.models import FilingCache, NewsCache

NEWS_VALIDATE_COLLECTION = "news_validate_retrieval"
FILING_VALIDATE_COLLECTION = "filing_validate_retrieval"


def main() -> None:
    chroma = ChromaClient()
    chroma.delete_collection(NEWS_VALIDATE_COLLECTION)
    chroma.delete_collection(FILING_VALIDATE_COLLECTION)

    with SessionLocal() as session:
        symbol = "000020"
        news_row = NewsCache(
            symbol=symbol,
            title="테스트 뉴스 제목",
            summary="반도체 업황 개선",
            source_name="테스트뉴스",
            source_url=f"https://news.example.com/{uuid4()}",
        )
        filing_row = FilingCache(
            symbol=symbol,
            filing_title="분기보고서 제출",
            filing_type="분기보고서",
            content=None,
            summary=None,
            dart_receipt_no=f"{uuid4().hex[:14]}",
            source_url=f"https://dart.example.com/{uuid4()}",
        )
        session.add(news_row)
        session.add(filing_row)
        session.flush()

        embedding = DeterministicEmbeddingClient()
        indexer = EvidenceIndexingService(
            session,
            chroma_client=chroma,
            embedding_client=embedding,
            collection_name=NEWS_VALIDATE_COLLECTION,
        )
        chroma.upsert(
            NEWS_VALIDATE_COLLECTION,
            documents=[
                indexer.build_news_document(
                    news_row,
                    content="삼성전자 메모리 반등과 수익성 개선 전망이 뉴스 본문에 담겨 있다.",
                )
            ],
            embedding_client=embedding,
        )
        chroma.upsert(
            FILING_VALIDATE_COLLECTION,
            documents=[
                indexer.build_filing_document(
                    filing_row,
                    content="분기보고서 본문에는 매출 성장과 설비투자 계획이 포함되어 있다.",
                )
            ],
            embedding_client=embedding,
        )

        retrieval = EvidenceRetrievalService(
            session,
            chroma_client=chroma,
            embedding_client=embedding,
            news_collection_name=NEWS_VALIDATE_COLLECTION,
            filing_collection_name=FILING_VALIDATE_COLLECTION,
        )
        hits = retrieval.search_symbol_evidence(
            query="반도체 실적과 분기보고서 근거",
            symbol=symbol,
            top_k=2,
        )

        assert len(hits) == 2
        source_types = {hit["source_type"] for hit in hits}
        assert "NEWS" in source_types
        assert "DART" in source_types
        print(
            {
                "symbol": symbol,
                "hit_count": len(hits),
                "source_types": sorted(source_types),
                "top_titles": [hit["source_title"] for hit in hits],
            }
        )
        session.rollback()

    chroma.delete_collection(NEWS_VALIDATE_COLLECTION)
    chroma.delete_collection(FILING_VALIDATE_COLLECTION)


if __name__ == "__main__":
    main()
