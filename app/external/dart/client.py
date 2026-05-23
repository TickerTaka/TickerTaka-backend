from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
from tenacity import retry, stop_after_attempt, wait_exponential

from app.config import get_settings
from app.core.redis import get_redis, make_key
from app.external.dart.financial_account_map import FINANCIAL_ACCOUNT_NAME_MAP


REPORT_CODES = {
    1: "11013",
    2: "11012",
    3: "11014",
    4: "11011",
}
KST = ZoneInfo("Asia/Seoul")


@dataclass(slots=True)
class FinancialStatementRecord:
    fiscal_year: int
    fiscal_quarter: int
    revenue: float | None
    operating_profit: float | None
    net_income: float | None
    total_assets: float | None
    total_liabilities: float | None
    total_equity: float | None
    source_url: str


class DartClient:
    def __init__(self, session: requests.Session | None = None) -> None:
        self.settings = get_settings()
        self.session = session or requests.Session()
        self.redis_client = get_redis()

    def fetch_financials(
        self,
        *,
        corp_code: str,
        years: list[int],
        fs_div_priority: tuple[str, ...] = ("CFS", "OFS"),
    ) -> list[FinancialStatementRecord]:
        records: list[FinancialStatementRecord] = []
        for year in years:
            for quarter, reprt_code in REPORT_CODES.items():
                record = self._fetch_single_period(
                    corp_code=corp_code,
                    year=year,
                    quarter=quarter,
                    reprt_code=reprt_code,
                    fs_div_priority=fs_div_priority,
                )
                if record is not None:
                    records.append(record)
        return records

    def _fetch_single_period(
        self,
        *,
        corp_code: str,
        year: int,
        quarter: int,
        reprt_code: str,
        fs_div_priority: tuple[str, ...],
    ) -> FinancialStatementRecord | None:
        payload = None
        selected_div = None
        for fs_div in fs_div_priority:
            response = self._get(
                "https://opendart.fss.or.kr/api/fnlttSinglAcnt.json",
                params={
                    "crtfc_key": self.settings.dart_api_key,
                    "corp_code": corp_code,
                    "bsns_year": str(year),
                    "reprt_code": reprt_code,
                    "fs_div": fs_div,
                },
            )
            data = response.json()
            if data.get("status") == "000" and data.get("list"):
                payload = data
                selected_div = fs_div
                break
        if not payload:
            return None

        mapped: dict[str, float | None] = {
            "revenue": None,
            "operating_profit": None,
            "net_income": None,
            "total_assets": None,
            "total_liabilities": None,
            "total_equity": None,
        }
        for row in payload["list"]:
            field_name = FINANCIAL_ACCOUNT_NAME_MAP.get(row.get("account_nm", ""))
            if field_name is None:
                continue
            amount = row.get("thstrm_amount")
            if amount in (None, "", "-"):
                mapped[field_name] = None
                continue
            mapped[field_name] = float(str(amount).replace(",", ""))

        source_url = (
            "https://opendart.fss.or.kr/dsaf001/main.do"
            f"?rcpNo={payload['list'][0].get('rcept_no', '')}&fs_div={selected_div}"
        )
        return FinancialStatementRecord(
            fiscal_year=year,
            fiscal_quarter=quarter,
            revenue=mapped["revenue"],
            operating_profit=mapped["operating_profit"],
            net_income=mapped["net_income"],
            total_assets=mapped["total_assets"],
            total_liabilities=mapped["total_liabilities"],
            total_equity=mapped["total_equity"],
            source_url=source_url,
        )

    @retry(wait=wait_exponential(multiplier=1, min=1, max=8), stop=stop_after_attempt(3), reraise=True)
    def _get(self, url: str, *, params: dict, timeout: int = 30) -> requests.Response:
        response = self.session.get(url, params=params, timeout=timeout)
        response.raise_for_status()
        self._record_daily_api_call()
        return response

    def _record_daily_api_call(self) -> None:
        if self.redis_client is None:
            return
        key = make_key("dart-api-count", datetime.now(KST).date().isoformat())
        count = self.redis_client.incr(key)
        if count == 1:
            self.redis_client.expire(key, 60 * 60 * 48)
