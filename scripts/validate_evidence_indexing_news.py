from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import func, select

from app.core.db import SessionLocal
from app.domain.evidence_indexing import EvidenceIndexingService
from app.external.article_scraper import ScrapedArticle
from app.external.chroma_client import ChromaClient
from app.external.embedding import DeterministicEmbeddingClient
from app.models import NewsCache, TickerMetadata

NEWS_VALIDATE_COLLECTION = "news_validate_reindex"


@dataclass(slots=True)
class ValidationResult:
    symbol: str
    scanned_rows: int
    indexed_rows: int
    skipped_rows: int
    failed_rows: int
    collection_count: int
    fetched_id: str | None


class FakeArticleScraper:
    def __init__(self, payloads: dict[str, ScrapedArticle]) -> None:
        self.payloads = payloads

    def scrape(self, url: str, timeout: tuple[float, float] = (3.0, 7.0)) -> ScrapedArticle:
        payload = self.payloads.get(url)
        if payload is None:
            raise RuntimeError(f"missing fake article for {url}")
        return payload


def get_empty_cache_symbol(session) -> str:
    stmt = (
        select(TickerMetadata.symbol)
        .outerjoin(NewsCache, NewsCache.symbol == TickerMetadata.symbol)
        .group_by(TickerMetadata.symbol)
        .having(func.count(NewsCache.id) == 0)
        .order_by(TickerMetadata.symbol)
        .limit(1)
    )
    symbol = session.scalar(stmt)
    if symbol is None:
        raise RuntimeError("no empty-cache ticker available for evidence indexing validation")
    return symbol


def main() -> None:
    chroma = ChromaClient()
    embeddings = DeterministicEmbeddingClient()

    with SessionLocal() as session:
        symbol = get_empty_cache_symbol(session)
        now = datetime.now(UTC).replace(microsecond=0)
        row = NewsCache(
            symbol=symbol,
            title=f"{symbol} evidence indexing validation headline",
            content=None,
            summary="validation summary",
            source_name="example.com",
            source_url=f"https://news.example.com/evidence-index/{uuid4().hex}",
            published_at=now - timedelta(minutes=3),
            retrieved_at=now - timedelta(minutes=1),
            ttl_until=now + timedelta(days=30),
        )
        session.add(row)
        session.commit()
        row_uuid = row.id
        row_id = str(row.id)
        row_url = row.source_url
        row_published_at = row.published_at

    try:
        chroma.delete_collection(NEWS_VALIDATE_COLLECTION)
        with SessionLocal() as session:
            service = EvidenceIndexingService(
                session,
                chroma_client=chroma,
                embedding_client=embeddings,
                collection_name=NEWS_VALIDATE_COLLECTION,
                article_scraper=FakeArticleScraper(
                    {
                        row_url: ScrapedArticle(
                            content="검증용 본문입니다. 기사 핵심 내용과 시장 반응을 설명합니다. " * 20,
                            summary="검증용 요약",
                            source_name="example.com",
                            canonical_url=row_url,
                            published_at=row_published_at,
                        )
                    }
                ),
            )
            result = service.reindex_news_for_symbol(symbol)

        payload = chroma.get(NEWS_VALIDATE_COLLECTION, ids=[row_id])
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
                        collection_count=chroma.count(NEWS_VALIDATE_COLLECTION),
                        fetched_id=fetched_id,
                    )
                ),
                ensure_ascii=False,
            )
        )
    finally:
        chroma.delete_collection(NEWS_VALIDATE_COLLECTION)
        with SessionLocal() as session:
            db_row = session.get(NewsCache, row_uuid)
            if db_row is not None:
                session.delete(db_row)
                session.commit()


if __name__ == "__main__":
    main()
