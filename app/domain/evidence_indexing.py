from __future__ import annotations

from dataclasses import dataclass
import logging

from sqlalchemy.orm import Session

from app.domain.evidence_analysis import EvidenceAnalysisService
from app.external.article_scraper import ArticleScraper
from app.external.chroma_client import (
    ChromaClient,
    ChromaDocument,
    FILING_COLLECTION_NAME,
    NEWS_COLLECTION_NAME,
)
from app.external.dart import DartClient
from app.external.embedding import EmbeddingClient, get_embedding_client
from app.models import FilingCache, NewsCache
from app.repositories.analysis_jobs_repository import AnalysisJobRepository
from app.repositories.filing_cache_repository import FilingCacheRepository
from app.repositories.news_cache_repository import NewsCacheRepository

logger = logging.getLogger(__name__)


def _build_summary(text: str, max_chars: int = 400) -> str:
    """Use the leading normalized filing text as a lightweight summary."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return " ".join(lines)[:max_chars]


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
        self.analysis_service = EvidenceAnalysisService(session)
        self.job_repo = AnalysisJobRepository(session)

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
        analysis_inputs: list[tuple[NewsCache, str]] = []

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
            analysis_inputs.append((row, scraped.content))
            result.indexed_rows += 1

        if documents:
            self.chroma_client.upsert(
                self.collection_name,
                documents=documents,
                embedding_client=self.embedding_client,
            )
            for row, content in analysis_inputs:
                try:
                    baseline = self.analysis_service.analyze_news_row(row, content=content)
                    self._maybe_enrich(
                        source_type="news",
                        source_id=row.id,
                        symbol=row.symbol,
                        needs_qwen=self.analysis_service.news_needs_qwen(baseline),
                        inline=lambda r=row, c=content: self.analysis_service.enrich_news_row(r, content=c),
                    )
                except Exception:
                    logger.exception("news evidence analysis failed for row %s", row.id)
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
        analysis_inputs: list[tuple[FilingCache, str]] = []

        for row in rows:
            if not row.dart_receipt_no:
                result.skipped_rows += 1
                continue

            try:
                zip_bytes = self.dart_client.fetch_document_xml(row.dart_receipt_no)
                filing_text = self.dart_client.extract_document_text_v2(zip_bytes)
            except Exception:
                logger.exception("filing reindex fetch failed for %s", row.dart_receipt_no)
                result.failed_rows += 1
                continue

            if len(filing_text.strip()) < 50:
                result.skipped_rows += 1
                continue

            self.filing_repo.update_summary(
                dart_receipt_no=row.dart_receipt_no,
                summary=_build_summary(filing_text),
            )
            chunks = self.dart_client.build_filing_chunks(
                zip_bytes,
                symbol=row.symbol,
                filing_id=str(row.id),
                filing_title=row.filing_title,
                disclosed_at=row.disclosed_at.isoformat() if row.disclosed_at else "",
            )
            if not chunks:
                result.skipped_rows += 1
                continue

            documents.extend(chunks)
            analysis_inputs.append((row, filing_text))
            result.indexed_rows += 1

        if documents:
            self.chroma_client.upsert(
                collection_name,
                documents=documents,
                embedding_client=self.embedding_client,
            )
            for row, filing_text in analysis_inputs:
                try:
                    self.analysis_service.analyze_filing_row(row, filing_text)
                    self._maybe_enrich(
                        source_type="filing",
                        source_id=row.id,
                        symbol=row.symbol,
                        needs_qwen=self.analysis_service.filing_needs_qwen(row.filing_title, filing_text),
                        inline=lambda r=row, t=filing_text: self.analysis_service.enrich_filing_row(r, t),
                    )
                except Exception:
                    logger.exception("filing evidence analysis failed for row %s", row.id)
        return result

    def _maybe_enrich(
        self,
        *,
        source_type: str,
        source_id: object,
        symbol: str,
        needs_qwen: bool,
        inline,
    ) -> None:
        """게이트 통과 시: 비동기면 큐 enqueue, 동기면 즉시 Qwen 보강."""
        if not needs_qwen:
            return
        if self.analysis_service.settings.analysis_async_enabled:
            self.job_repo.enqueue(
                source_type=source_type,
                source_id=source_id,
                symbol=symbol,
                prompt_version=self.analysis_service.settings.analysis_prompt_version,
            )
        else:
            inline()

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
