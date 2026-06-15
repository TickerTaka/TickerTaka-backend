"""실제 공시 1건으로 Qwen enrich 를 돌려 Langfuse trace 를 생성/확인한다.

워커(_process_job)와 동일 경로. persist=False 로 운영 evidence_analysis 는 건드리지 않는다.
"""
import time

from sqlalchemy import text

from app.core.db import session_scope
from app.core.tracing import get_langfuse
from app.domain.evidence_analysis import EvidenceAnalysisService
from app.external.dart import DartClient
from app.repositories.filing_cache_repository import FilingCacheRepository

RECEIPT = "20260211800864"  # 454910 매출액또는손익구조변경

lf = get_langfuse()
print("langfuse client:", type(lf).__name__ if lf else None)

dart = DartClient()
with session_scope() as session:
    fid = session.execute(
        text("SELECT id FROM filing_cache WHERE dart_receipt_no = :r LIMIT 1"),
        {"r": RECEIPT},
    ).scalar()
    print("filing id:", fid)
    filing_repo = FilingCacheRepository(session)
    row = filing_repo.get_by_ids([str(fid)])[str(fid)]
    print("title:", row.filing_title)

    print("fetching DART document ...")
    zip_bytes = dart.fetch_document_xml(row.dart_receipt_no)
    filing_text = dart.extract_document_text_v2(zip_bytes)
    print("doc chars:", len(filing_text))

    service = EvidenceAnalysisService(session)
    print("qwen_available:", service.qwen_available)

    t0 = time.time()
    result = service.enrich_filing_row(row, filing_text, persist=False)
    dt = time.time() - t0
    print(f"--- done in {dt:.1f}s ---")
    print("sentiment:", result.sentiment, "| impact:", result.impact_score, "| event_type:", result.event_type)
    print("FINAL summary:", result.summary)
    print("summary_provider:", result.raw_response.get("summary_provider"))
    print("key_points:", result.key_points)
    gen = result.raw_response.get("generative", {})
    print("qwen_status:", gen.get("status"),
          "| key_points_dropped:", gen.get("key_points_dropped"),
          "| grounding_survival:", gen.get("grounding_survival"),
          "| consistency:", gen.get("consistency"))

if lf:
    lf.flush()
    print("flushed. trace url:", lf.get_trace_url() if hasattr(lf, "get_trace_url") else "(check UI)")
