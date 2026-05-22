from __future__ import annotations

from dataclasses import dataclass
import logging

from sqlalchemy.orm import Session

from app.external.article_scraper import ArticleScraper
from app.external.chroma_client import ChromaClient, ChromaDocument, NEWS_COLLECTION_NAME
from app.external.embedding import EmbeddingClient, get_embedding_client
from app.models import NewsCache
from app.repositories.news_cache_repository import NewsCacheRepository

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ReindexNewsResult:
    symbol: str
    scanned_rows: int = 0
    indexed_rows: int = 0
    skipped_rows: int = 0
    failed_rows: int = 0


class EvidenceIndexingService:
    def __init__(
        self,
        session: Session,
        *,
        chroma_client: ChromaClient | None = None,
        embedding_client: EmbeddingClient | None = None,
        article_scraper: ArticleScraper | None = None,
    ) -> None:
        self.session = session
        self.repo = NewsCacheRepository(session)
        self.chroma_client = chroma_client or ChromaClient()
        self.embedding_client = embedding_client or get_embedding_client()
        self.article_scraper = article_scraper or ArticleScraper()

    def reindex_news_for_symbol(
        self,
        symbol: str,
        *,
        reset: bool = False,
        force: bool = False,
    ) -> ReindexNewsResult:
        if reset:
            self.chroma_client.delete(where={"symbol": symbol}, name=NEWS_COLLECTION_NAME)

        rows = self.repo.list_by_symbol(symbol)
        result = ReindexNewsResult(symbol=symbol, scanned_rows=len(rows))
        documents: list[ChromaDocument] = []

        for row in rows:
            try:
                scraped = self.article_scraper.scrape(row.source_url)
            except Exception:
                logger.exception("reindex scrape failed for %s", row.source_url)
                result.failed_rows += 1
                continue

            if not scraped.content or not scraped.content.strip():
                result.skipped_rows += 1
                continue

            document_text = f"{row.title}\n\n{scraped.content.strip()}".strip()
            documents.append(
                ChromaDocument(
                    id=str(row.id),
                    document=document_text,
                    metadata={
                        "symbol": row.symbol,
                        "source_id": str(row.id),
                        "source_type": "news",
                        "source_url": row.source_url,
                        "published_at": row.published_at.isoformat() if row.published_at else None,
                        "force": force,
                    },
                )
            )
            result.indexed_rows += 1

        if documents:
            self.chroma_client.upsert(
                NEWS_COLLECTION_NAME,
                documents=documents,
                embedding_client=self.embedding_client,
            )
        return result
