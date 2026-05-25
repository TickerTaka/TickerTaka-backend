from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from datetime import datetime
from io import BytesIO
from zoneinfo import ZoneInfo
from zipfile import BadZipFile, ZipFile

from bs4 import BeautifulSoup
import requests
from tenacity import retry, stop_after_attempt, wait_exponential

from app.config import get_settings
from app.core.redis import get_redis, make_key
from app.external.dart.corp_code import CorpCodeProvider
from app.external.dart.financial_account_map import FINANCIAL_ACCOUNT_NAME_MAP


REPORT_CODES = {
    1: "11013",
    2: "11012",
    3: "11014",
    4: "11011",
}
KST = ZoneInfo("Asia/Seoul")
_MIN_FILING_TEXT_LEN = 50


@dataclass(slots=True, frozen=True)
class DartFilingItem:
    corp_code: str
    corp_name: str
    stock_code: str | None
    report_name: str
    receipt_no: str
    receipt_date: str
    filing_type: str | None = None


class DartApiError(RuntimeError):
    pass


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
        self.corp_code_provider = CorpCodeProvider(session=self.session)

    def get_corp_code_by_stock_code(self, stock_code: str) -> str | None:
        return self.corp_code_provider.get_corp_code(stock_code.strip())

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

    def list_filings(
        self,
        corp_code: str,
        *,
        begin_date: date,
        end_date: date,
        page_count: int = 100,
        last_report_only: bool = False,
    ) -> list[DartFilingItem]:
        if not self.settings.dart_api_key:
            raise DartApiError("DART_API_KEY is required")

        response = self._get(
            "https://opendart.fss.or.kr/api/list.json",
            params={
                "crtfc_key": self.settings.dart_api_key,
                "corp_code": corp_code,
                "bgn_de": begin_date.strftime("%Y%m%d"),
                "end_de": end_date.strftime("%Y%m%d"),
                "last_reprt_at": "Y" if last_report_only else "N",
                "sort": "date",
                "sort_mth": "desc",
                "page_no": 1,
                "page_count": page_count,
            },
        )
        payload = response.json()
        status = str(payload.get("status") or "")
        if status == "013":
            return []
        if status != "000":
            message = payload.get("message") or "unknown DART API error"
            raise DartApiError(f"DART list.json failed: status={status} message={message}")
        return [self._to_filing_item(item) for item in payload.get("list", [])]

    @staticmethod
    def build_viewer_url(receipt_no: str) -> str:
        return f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={receipt_no}"

    def fetch_document_xml(self, receipt_no: str) -> bytes:
        if not self.settings.dart_api_key:
            raise DartApiError("DART_API_KEY is required")

        response = self._get(
            "https://opendart.fss.or.kr/api/document.xml",
            params={
                "crtfc_key": self.settings.dart_api_key,
                "rcept_no": receipt_no,
            },
        )
        return response.content

    def extract_document_text(self, zip_bytes: bytes) -> str:
        try:
            with ZipFile(BytesIO(zip_bytes)) as archive:
                names = [
                    name
                    for name in archive.namelist()
                    if name.lower().endswith((".html", ".htm", ".xml"))
                ]
                if not names:
                    return ""
                html_names = [name for name in names if name.lower().endswith((".html", ".htm"))]
                candidates = html_names or names
                selected_name = max(candidates, key=lambda name: archive.getinfo(name).file_size)
                raw = archive.read(selected_name)
        except (BadZipFile, KeyError, OSError):
            return ""

        text = self._decode_document_bytes(raw)
        if not text:
            return ""

        try:
            soup = BeautifulSoup(text, "html.parser")
            extracted = soup.get_text(separator="\n")
        except Exception:
            return ""

        lines = [line.strip() for line in extracted.splitlines()]
        return "\n".join(line for line in lines if line)

    def fetch_filing_text(self, receipt_no: str) -> str:
        try:
            zip_bytes = self.fetch_document_xml(receipt_no)
        except requests.RequestException as exc:
            raise DartApiError(f"DART document.xml request failed: receipt_no={receipt_no}") from exc

        text = self.extract_document_text(zip_bytes)
        if len(text.strip()) < _MIN_FILING_TEXT_LEN:
            raise DartApiError(f"DART document.xml text was empty or too short: receipt_no={receipt_no}")
        return text

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

    @staticmethod
    def _decode_document_bytes(raw: bytes) -> str:
        for encoding in ("utf-8", "euc-kr", "cp949"):
            try:
                return raw.decode(encoding)
            except UnicodeDecodeError:
                continue
        return raw.decode("utf-8", errors="ignore")

    @staticmethod
    def _to_filing_item(item: dict) -> DartFilingItem:
        return DartFilingItem(
            corp_code=str(item.get("corp_code") or "").strip(),
            corp_name=str(item.get("corp_name") or "").strip(),
            stock_code=(str(item.get("stock_code") or "").strip() or None),
            report_name=str(item.get("report_nm") or "").strip(),
            receipt_no=str(item.get("rcept_no") or "").strip(),
            receipt_date=str(item.get("rcept_dt") or "").strip(),
            filing_type=(
                str(item.get("pblntf_ty") or item.get("pblntf_detail_ty") or "").strip()
                or None
            ),
        )
