from __future__ import annotations

from dataclasses import dataclass
import logging

from sqlalchemy.orm import Session

from app.external.article_scraper import ArticleScraper
from app.external.chroma_client import (
    ChromaClient,
    ChromaDocument,
    FILING_COLLECTION_NAME,
    NEWS_COLLECTION_NAME,
)
from app.external.dart import DartApiError, DartClient
from app.external.embedding import EmbeddingClient, get_embedding_client
from app.models import FilingCache, NewsCache
from app.repositories.filing_cache_repository import FilingCacheRepository
from app.repositories.news_cache_repository import NewsCacheRepository

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ReindexNewsResult:
    symbol: str
    scanned_rows: int = 0
    indexed_rows: int = 0
    skipped_rows: int = 0
    failed_rows: int = 0


@dataclass(slots=True)
class ReindexFilingResult:
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
        dart_client: DartClient | None = None,
        collection_name: str = NEWS_COLLECTION_NAME,
    ) -> None:
        self.session = session
        self.news_repo = NewsCacheRepository(session)
        self.filing_repo = FilingCacheRepository(session)
        self.chroma_client = chroma_client or ChromaClient()
        self.embedding_client = embedding_client or get_embedding_client()
        self.article_scraper = article_scraper or ArticleScraper()
        self.dart_client = dart_client or DartClient()
        self.collection_name = collection_name

    def reindex_news_for_symbol(
        self,
        symbol: str,
        *,
        reset: bool = False,
    ) -> ReindexNewsResult:
        if reset:
            self.chroma_client.delete(where={"symbol": symbol}, name=self.collection_name)

        rows = self.news_repo.list_by_symbol(symbol)
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
                        **(
                            {"published_at": row.published_at.isoformat()}
                            if row.published_at
                            else {}
                        ),
                    },
                )
            )
            result.indexed_rows += 1

        if documents:
            self.chroma_client.upsert(
                self.collection_name,
                documents=documents,
                embedding_client=self.embedding_client,
            )
        return result

    def reindex_filing_for_symbol(
        self,
        symbol: str,
        *,
        reset: bool = False,
        collection_name: str = FILING_COLLECTION_NAME,
    ) -> ReindexFilingResult:
        if reset:
            self.chroma_client.delete(where={"symbol": symbol}, name=collection_name)

        rows = self.filing_repo.list_by_symbol(symbol)
        result = ReindexFilingResult(symbol=symbol, scanned_rows=len(rows))
        documents: list[ChromaDocument] = []

        for row in rows:
            if not row.dart_receipt_no:
                result.skipped_rows += 1
                continue

            try:
                filing_text = self.dart_client.fetch_filing_text(row.dart_receipt_no)
            except DartApiError:
                logger.exception("filing reindex fetch failed for %s", row.dart_receipt_no)
                result.failed_rows += 1
                continue

            if len(filing_text.strip()) < 50:
                result.skipped_rows += 1
                continue

            documents.append(self.build_filing_document(row, content=filing_text))
            result.indexed_rows += 1

        if documents:
            self.chroma_client.upsert(
                collection_name,
                documents=documents,
                embedding_client=self.embedding_client,
            )
        return result

    @staticmethod
    def build_news_document(
        row: NewsCache,
        *,
        content: str,
    ) -> ChromaDocument:
        document_text = f"{row.title}\n\n{content.strip()}".strip()
        return ChromaDocument(
            id=str(row.id),
            document=document_text,
            metadata={
                "symbol": row.symbol,
                "source_id": str(row.id),
                "source_type": "news",
                "source_url": row.source_url,
                **(
                    {"published_at": row.published_at.isoformat()}
                    if row.published_at
                    else {}
                ),
            },
        )

    @staticmethod
    def build_filing_document(
        row: FilingCache,
        *,
        content: str,
    ) -> ChromaDocument:
        document_text = f"{row.filing_title}\n\n{content.strip()}".strip()
        return ChromaDocument(
            id=str(row.id),
            document=document_text,
            metadata={
                "symbol": row.symbol,
                "source_id": str(row.id),
                "source_type": "filing",
                "source_url": row.source_url,
                **(
                    {"published_at": row.disclosed_at.isoformat()}
                    if row.disclosed_at
                    else {}
                ),
                **({"dart_receipt_no": row.dart_receipt_no} if row.dart_receipt_no else {}),
            },
        )
