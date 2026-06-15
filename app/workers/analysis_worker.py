"""Qwen 비동기 보강 워커 (별도 프로세스).

실행:  python -m app.workers.analysis_worker

동기 인덱싱이 룰+FinBERT baseline 을 즉시 저장하고 analysis_jobs 에 보강 작업을 큐잉한다.
이 워커가 큐를 폴링해 무거운 Qwen 구조화 분석을 후처리하고 evidence_analysis 를 갱신한다.
모델은 워커 프로세스에만 1회 로드되어 웹 프로세스 메모리를 압박하지 않는다.
"""
from __future__ import annotations

import logging
import signal
import time

from app.config import get_settings
from app.core.db import session_scope
from app.core.tracing import get_langfuse
from app.domain.evidence_analysis import (
    SOURCE_TYPE_FILING,
    SOURCE_TYPE_NEWS,
    EvidenceAnalysisService,
)
from app.external.article_scraper import ArticleScraper
from app.external.dart import DartClient
from app.models import AnalysisJob
from app.repositories.analysis_jobs_repository import AnalysisJobRepository
from app.repositories.filing_cache_repository import FilingCacheRepository
from app.repositories.news_cache_repository import NewsCacheRepository

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("analysis_worker")

_running = True


def _handle_signal(signum, _frame) -> None:  # pragma: no cover - signal path
    global _running
    logger.info("received signal %s, shutting down after current batch", signum)
    _running = False


def _process_job(job: AnalysisJob, *, service: EvidenceAnalysisService,
                 news_repo: NewsCacheRepository, filing_repo: FilingCacheRepository,
                 scraper: ArticleScraper, dart: DartClient) -> None:
    source_id = str(job.source_id)
    if job.source_type == SOURCE_TYPE_NEWS:
        row = news_repo.get_by_ids([source_id]).get(source_id)
        if row is None:
            raise ValueError(f"news row {source_id} not found")
        content = scraper.scrape(row.source_url).content
        service.enrich_news_row(row, content=content)
    elif job.source_type == SOURCE_TYPE_FILING:
        row = filing_repo.get_by_ids([source_id]).get(source_id)
        if row is None:
            raise ValueError(f"filing row {source_id} not found")
        if not row.dart_receipt_no:
            raise ValueError(f"filing row {source_id} has no dart_receipt_no")
        zip_bytes = dart.fetch_document_xml(row.dart_receipt_no)
        filing_text = dart.extract_document_text_v2(zip_bytes)
        service.enrich_filing_row(row, filing_text)
    else:
        raise ValueError(f"unknown source_type {job.source_type}")


def run_once() -> int:
    """한 배치를 처리하고 처리한 작업 수를 반환."""
    settings = get_settings()
    processed = 0
    scraper = ArticleScraper()
    dart = DartClient()
    try:
        with session_scope() as session:
            job_repo = AnalysisJobRepository(session)
            service = EvidenceAnalysisService(session)
            news_repo = NewsCacheRepository(session)
            filing_repo = FilingCacheRepository(session)
            jobs = job_repo.claim_batch(settings.analysis_worker_batch_size)
            for job in jobs:
                try:
                    _process_job(
                        job, service=service, news_repo=news_repo, filing_repo=filing_repo,
                        scraper=scraper, dart=dart,
                    )
                    job_repo.mark_done(job)
                    processed += 1
                except Exception as exc:  # noqa: BLE001 - job 격리
                    logger.exception("analysis job %s failed", job.id)
                    job_repo.mark_failed(
                        job, error=f"{type(exc).__name__}: {exc}",
                        max_attempts=settings.analysis_worker_max_attempts,
                    )
    finally:
        # 워커는 단발/배치 프로세스라 flush 안 하면 trace 유실. 처리분 있을 때만 전송.
        if processed:
            lf = get_langfuse()
            if lf is not None:
                lf.flush()
    return processed


def main() -> None:
    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)
    settings = get_settings()
    logger.info(
        "analysis worker started (model=%s, batch=%s, poll=%ss)",
        settings.analysis_generation_model,
        settings.analysis_worker_batch_size,
        settings.analysis_worker_poll_interval,
    )
    while _running:
        try:
            processed = run_once()
        except Exception:  # noqa: BLE001 - 루프 생존
            logger.exception("worker batch failed")
            processed = 0
        if processed == 0:
            time.sleep(settings.analysis_worker_poll_interval)
    # 종료 시 잔여 trace 전송
    lf = get_langfuse()
    if lf is not None:
        lf.flush()


if __name__ == "__main__":
    main()
