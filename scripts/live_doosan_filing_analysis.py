"""Live reproduction of the watchlist filing pipeline for 두산로보틱스 (454910).

Mirrors EvidenceIndexingService.reindex_filing_for_symbol's analysis path:
  list_filings -> fetch_document_xml -> extract_document_text_v2 -> analyze_text
with the real LocalHFSentimentAnalyzer (FinBERT) enabled. No DB/Chroma writes.
"""
from __future__ import annotations

from datetime import date, timedelta
from uuid import uuid4

from app.external.dart.client import DartClient
from app.domain.evidence_analysis import EvidenceAnalysisService

STOCK_CODE = "454910"
LOOKBACK_DAYS = 365
MAX_FILINGS = 15


def main() -> None:
    dart = DartClient()
    corp_code = dart.get_corp_code_by_stock_code(STOCK_CODE)
    print(f"corp_code={corp_code}")

    end = date.today()
    begin = end - timedelta(days=LOOKBACK_DAYS)
    filings = dart.list_filings(corp_code, begin_date=begin, end_date=end, page_count=100)
    print(f"listed {len(filings)} filings in {begin}~{end}; analyzing up to {MAX_FILINGS}\n")

    service = EvidenceAnalysisService(session=None)  # real FinBERT analyzer

    rows = []
    for item in filings[:MAX_FILINGS]:
        try:
            body = dart.fetch_filing_text(item.receipt_no)
        except Exception as exc:
            rows.append((item.report_name, "FETCH_FAIL", "", 0, str(exc)[:40]))
            continue
        result = service.analyze_text(
            source_type="filing",
            symbol=STOCK_CODE,
            title=item.report_name,
            text=body,
            source_id=uuid4(),
            persist=False,
        )
        hf = (result.raw_response or {}).get("hf", {})
        hf_label = hf.get("label", hf.get("status", "?"))
        rows.append((
            item.report_name,
            result.sentiment,
            f"{result.impact_score:+d}",
            len(body),
            f"hf={hf_label} model={result.model_name}",
        ))

    print(f"{'공시명':<40} {'sentiment':<10} {'imp':<4} {'len':>6}  detail")
    print("-" * 110)
    for name, sent, imp, blen, detail in rows:
        print(f"{name[:38]:<40} {sent:<10} {imp:<4} {blen:>6}  {detail}")

    sentiments = [r[1] for r in rows]
    from collections import Counter
    print("\nsentiment distribution:", dict(Counter(sentiments)))


if __name__ == "__main__":
    main()
