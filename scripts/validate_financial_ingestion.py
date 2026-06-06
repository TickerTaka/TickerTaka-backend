from __future__ import annotations

from dataclasses import dataclass

import app.domain.financial_ingestion as financial_module
from app.external.dart.client import FinancialStatementRecord
from app.models import MarketType


class FakeRedis:
    def __init__(self) -> None:
        self.data: dict[str, str] = {}

    def set(self, key, value, nx=False, ex=None):
        if nx and key in self.data:
            return False
        self.data[key] = str(value)
        return True

    def get(self, key):
        return self.data.get(key)

    def delete(self, key):
        self.data.pop(key, None)


class FakeSession:
    def __init__(self, symbol: str) -> None:
        self.symbol = symbol

    def get(self, model, key):
        if key != self.symbol:
            return None
        return type("Ticker", (), {"symbol": key, "market": MarketType.KOSPI})()

    def flush(self):
        return None


class FakeFinancialRepo:
    def __init__(self, session) -> None:
        self.rows: list[dict] = []

    def upsert_many(self, rows):
        self.rows = list(rows)
        return len(rows)

    def trim_rows_for_symbol(self, symbol: str, max_rows: int) -> int:
        overflow = max(0, len(self.rows) - max_rows)
        if overflow:
            self.rows = self.rows[-max_rows:]
        return overflow

    def update_latest_valuation(self, symbol: str, *, per: float | None, pbr: float | None) -> bool:
        return True


class FakePriceRepo:
    def __init__(self, session) -> None:
        pass

    def list_recent(self, symbol: str, limit: int = 1):
        return []


class FakeCorpCodeProvider:
    def get_corp_code(self, symbol: str) -> str | None:
        return "00126380"


@dataclass(slots=True)
class FakeDartClient:
    rows: list[FinancialStatementRecord]

    def fetch_financials(self, *, corp_code: str, years: list[int], fs_div_priority=("CFS", "OFS")) -> list[FinancialStatementRecord]:
        return [row for row in self.rows if row.fiscal_year in years]


def main() -> None:
    symbol = "000020"
    rows = [
        FinancialStatementRecord(
            fiscal_year=2025,
            fiscal_quarter=quarter,
            revenue=1000.0 + quarter,
            operating_profit=100.0 + quarter,
            net_income=80.0 + quarter,
            total_assets=5000.0,
            total_liabilities=1500.0,
            total_equity=3500.0,
            source_url=f"https://dart.example.com/{quarter}",
        )
        for quarter in (1, 2, 3, 4)
    ]

    original_repo = financial_module.FinancialCacheRepository
    original_price_repo = financial_module.PriceCacheRepository
    try:
        financial_module.FinancialCacheRepository = FakeFinancialRepo
        financial_module.PriceCacheRepository = FakePriceRepo
        service = financial_module.FinancialIngestionService(
            FakeSession(symbol),
            dart_client=FakeDartClient(rows),
            corp_code_provider=FakeCorpCodeProvider(),
            redis_client=FakeRedis(),
        )
        result = service.sync_financials_for_ticker(symbol, force=True, backfill_years=2)
        print(
            {
                "fetched_periods": result.fetched_periods,
                "saved_rows": result.saved_rows,
                "trimmed_rows": result.trimmed_rows,
            }
        )
    finally:
        financial_module.FinancialCacheRepository = original_repo
        financial_module.PriceCacheRepository = original_price_repo


if __name__ == "__main__":
    main()
