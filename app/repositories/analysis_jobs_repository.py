from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models import (
    JOB_STATUS_DONE,
    JOB_STATUS_FAILED,
    JOB_STATUS_PENDING,
    JOB_STATUS_RUNNING,
    AnalysisJob,
)


class AnalysisJobRepository:
    """Postgres 기반 단순 작업 큐 (Redis 불필요).

    SELECT ... FOR UPDATE SKIP LOCKED 로 워커 간 경합 없이 claim 한다.
    """

    def __init__(self, session: Session) -> None:
        self.session = session

    def enqueue(
        self,
        *,
        source_type: str,
        source_id: str | UUID,
        symbol: str,
        prompt_version: str,
    ) -> None:
        """동일 (source_type, source_id, prompt_version)이면 pending 으로 재설정해 재보강 예약."""
        stmt = insert(AnalysisJob).values(
            source_type=source_type,
            source_id=UUID(str(source_id)),
            symbol=symbol,
            prompt_version=prompt_version,
            status=JOB_STATUS_PENDING,
            attempts=0,
        )
        stmt = stmt.on_conflict_do_update(
            constraint="uq_analysis_jobs_source_version",
            set_={
                "symbol": stmt.excluded.symbol,
                "status": JOB_STATUS_PENDING,
                "attempts": 0,
                "last_error": None,
                "locked_at": None,
                "updated_at": text("now()"),
            },
        )
        self.session.execute(stmt)
        self.session.flush()

    def claim_batch(self, limit: int) -> list[AnalysisJob]:
        """pending 작업을 잠그고 running 으로 전환해 반환."""
        stmt = (
            select(AnalysisJob)
            .where(AnalysisJob.status == JOB_STATUS_PENDING)
            .order_by(AnalysisJob.created_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        jobs = list(self.session.scalars(stmt))
        now = datetime.now(timezone.utc)
        for job in jobs:
            job.status = JOB_STATUS_RUNNING
            job.attempts += 1
            job.locked_at = now
        self.session.flush()
        return jobs

    def mark_done(self, job: AnalysisJob) -> None:
        job.status = JOB_STATUS_DONE
        job.last_error = None
        self.session.flush()

    def mark_failed(self, job: AnalysisJob, *, error: str, max_attempts: int) -> None:
        job.last_error = error[:2000]
        job.status = JOB_STATUS_FAILED if job.attempts >= max_attempts else JOB_STATUS_PENDING
        self.session.flush()
