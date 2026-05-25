from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import func, select

from app.core.db import SessionLocal
from app.domain.filing_ingestion import FilingIngestionService
from app.external.dart import DartFilingItem
from app.models import FilingCache, TickerMetadata


@dataclass(slots=True)
class ValidationResult:
    symbol: str
    fetched: int
    inserted: int
    updated: int
    skipped: int
    final_rows: int


class FakeDartClient:
    def __init__(self, corp_code: str | None, items: list[DartFilingItem]) -> None:
        self.corp_code = corp_code
        self.items = items

    def get_corp_code_by_stock_code(self, stock_code: str) -> str | None:
        return self.corp_code

    def list_filings(
        self,
        corp_code: str,
        *,
        begin_date: date,
        end_date: date,
        page_count: int = 100,
        last_report_only: bool = False,
    ) -> list[DartFilingItem]:
        return list(self.items[:page_count])

    @staticmethod
    def build_viewer_url(receipt_no: str) -> str:
        return f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={receipt_no}"


def get_test_symbol(session) -> str:
    symbol = session.scalar(select(TickerMetadata.symbol).order_by(TickerMetadata.symbol).limit(1))
    if symbol is None:
        raise RuntimeError("ticker_metadata is empty")
    return symbol


def main() -> None:
    with SessionLocal() as session:
        symbol = get_test_symbol(session)
        base = datetime.now(UTC).replace(microsecond=0)
        receipt_no = f"{base:%Y%m%d}000001"
        existing = FilingCache(
            symbol=symbol,
            filing_title="기존 공시",
            filing_type="B",
            content=None,
            summary=None,
            dart_receipt_no=receipt_no,
            source_url=FakeDartClient.build_viewer_url(receipt_no),
            disclosed_at=base - timedelta(days=2),
            retrieved_at=base - timedelta(days=1),
            ttl_until=base + timedelta(days=5),
        )
        session.add(existing)
        session.flush()

        items = [
            DartFilingItem(
                corp_code="00126380",
                corp_name="테스트기업",
                stock_code=symbol,
                report_name="신규 공시",
                receipt_no=f"{base:%Y%m%d}000002",
                receipt_date=(base.date() - timedelta(days=1)).strftime("%Y%m%d"),
                filing_type="B",
            ),
            DartFilingItem(
                corp_code="00126380",
                corp_name="테스트기업",
                stock_code=symbol,
                report_name="기존 공시 갱신",
                receipt_no=receipt_no,
                receipt_date=base.date().strftime("%Y%m%d"),
                filing_type="I",
            ),
            DartFilingItem(
                corp_code="00126380",
                corp_name="테스트기업",
                stock_code=symbol,
                report_name="",
                receipt_no=f"{base:%Y%m%d}000003",
                receipt_date=base.date().strftime("%Y%m%d"),
                filing_type="B",
            ),
        ]

        service = FilingIngestionService(
            session,
            dart_client=FakeDartClient("00126380", items),
        )
        result = service.sync_filings_for_ticker(symbol, limit=10)
        final_rows = session.scalar(
            select(func.count(FilingCache.id)).where(FilingCache.symbol == symbol)
        ) or 0

        print(
            json.dumps(
                asdict(
                    ValidationResult(
                        symbol=symbol,
                        fetched=result.fetched_count,
                        inserted=result.inserted_count,
                        updated=result.updated_count,
                        skipped=result.skipped_count,
                        final_rows=final_rows,
                    )
                ),
                ensure_ascii=False,
            )
        )
        session.rollback()


if __name__ == "__main__":
    main()
