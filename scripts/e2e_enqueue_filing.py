"""감성분석 워커 E2E용 트리거 — filing_cache 에서 공시 1건을 재큐잉한다.

실행(앱 venv, 레포 루트):
    python -m scripts.e2e_enqueue_filing

dart_receipt_no 가 있는 filing_cache 행 1건을 골라 analysis_jobs 에 pending 으로 넣는다.
(enqueue 는 동일 source/version 이면 pending 으로 재설정 → 몇 번을 돌려도 안전.)
이후 워커(`python -m app.workers.analysis_worker`)가 vLLM(또는 Ollama)으로 보강 처리한다.
"""
from __future__ import annotations

from sqlalchemy import select

from app.config import get_settings
from app.core.db import session_scope
from app.models import FilingCache
from app.repositories.analysis_jobs_repository import AnalysisJobRepository


def main() -> None:
    settings = get_settings()
    with session_scope() as session:
        row = session.scalars(
            select(FilingCache)
            .where(FilingCache.dart_receipt_no.isnot(None))
            .order_by(FilingCache.retrieved_at.desc())
            .limit(1)
        ).first()
        if row is None:
            print("filing_cache 에 dart_receipt_no 있는 행이 없음 — 먼저 공시를 인덱싱하세요.")
            return
        AnalysisJobRepository(session).enqueue(
            source_type="filing",
            source_id=row.id,
            symbol=row.symbol,
            prompt_version=settings.analysis_prompt_version,
        )
        print(f"enqueued filing id={row.id} symbol={row.symbol} receipt={row.dart_receipt_no}")
        print(f"prompt_version={settings.analysis_prompt_version}")


if __name__ == "__main__":
    main()
