from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from datetime import datetime
from io import BytesIO
import re
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo
from zipfile import BadZipFile, ZipFile

from bs4 import BeautifulSoup, Tag
import requests
from tenacity import retry, stop_after_attempt, wait_exponential

from app.config import get_settings
from app.core.redis import get_redis, make_key
from app.external.dart.corp_code import CorpCodeProvider
from app.external.dart.financial_account_map import FINANCIAL_ACCOUNT_NAME_MAP

if TYPE_CHECKING:
    from app.external.chroma_client import ChromaDocument


REPORT_CODES = {
    1: "11013",
    2: "11012",
    3: "11014",
    4: "11011",
}
KST = ZoneInfo("Asia/Seoul")
_MIN_FILING_TEXT_LEN = 50
_EPS_ACCOUNT_NAMES = {
    "기본주당이익",
    "희석주당이익",
    "보통주기본주당이익",
    "주당순이익",
}


def _split_text(text: str, max_chars: int) -> list[str]:
    """Split text on line boundaries with an approximate character limit."""
    if len(text) <= max_chars:
        return [text]
    chunks: list[str] = []
    lines = text.split("\n")
    current: list[str] = []
    current_len = 0
    for line in lines:
        line_len = len(line) + 1
        if current_len + line_len > max_chars and current:
            chunks.append("\n".join(current))
            current = [line]
            current_len = line_len
        else:
            current.append(line)
            current_len += line_len
    if current:
        chunks.append("\n".join(current))
    return chunks


def _span_to_int(value: str | int | None) -> int:
    try:
        parsed = int(value or 1)
    except (TypeError, ValueError):
        return 1
    return max(parsed, 1)


def _to_optional_float(value) -> float | None:
    try:
        if value in (None, "", "-"):
            return None
        return float(str(value).replace(",", ""))
    except Exception:
        return None


def _to_optional_int(value) -> int | None:
    try:
        if value in (None, "", "-"):
            return None
        return int(str(value).replace(",", ""))
    except Exception:
        return None


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


@dataclass(slots=True)
class DartValuationInputs:
    fiscal_year: int
    fiscal_quarter: int
    eps: float | None
    shares_outstanding: int | None


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

    def fetch_valuation_inputs(
        self,
        *,
        corp_code: str,
        year: int,
        quarter: int,
        fs_div_priority: tuple[str, ...] = ("CFS", "OFS"),
    ) -> DartValuationInputs | None:
        reprt_code = REPORT_CODES.get(quarter)
        if reprt_code is None:
            return None

        payload = None
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
                break

        eps = None
        if payload:
            for row in payload["list"]:
                account_name = str(row.get("account_nm", "")).strip()
                if account_name in _EPS_ACCOUNT_NAMES:
                    eps = _to_optional_float(row.get("thstrm_amount"))
                    if eps is not None:
                        break

        shares_outstanding = self._fetch_shares_outstanding(
            corp_code=corp_code,
            year=year,
            reprt_code=reprt_code,
        )
        if eps is None and shares_outstanding is None:
            return None
        return DartValuationInputs(
            fiscal_year=year,
            fiscal_quarter=quarter,
            eps=eps,
            shares_outstanding=shares_outstanding,
        )

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

    def _fetch_shares_outstanding(
        self,
        *,
        corp_code: str,
        year: int,
        reprt_code: str,
    ) -> int | None:
        report_candidates = [(year, reprt_code)]
        if reprt_code != REPORT_CODES[4]:
            report_candidates.append((year, REPORT_CODES[4]))
        report_candidates.append((year - 1, REPORT_CODES[4]))

        seen: set[tuple[int, str]] = set()
        for candidate_year, candidate_reprt_code in report_candidates:
            if candidate_year <= 0:
                continue
            key = (candidate_year, candidate_reprt_code)
            if key in seen:
                continue
            seen.add(key)

            response = self._get(
                "https://opendart.fss.or.kr/api/stockTotqySttus.json",
                params={
                    "crtfc_key": self.settings.dart_api_key,
                    "corp_code": corp_code,
                    "bsns_year": str(candidate_year),
                    "reprt_code": candidate_reprt_code,
                },
            )
            payload = response.json()
            if payload.get("status") != "000" or not payload.get("list"):
                continue

            preferred_rows = []
            fallback_rows = []
            for row in payload["list"]:
                label = " ".join(
                    str(row.get(key, "")).strip()
                    for key in ("se", "stock_knd", "isu_knd_nm")
                ).strip()
                if "보통" in label or "의결권 있는 주식" in label:
                    preferred_rows.append(row)
                else:
                    fallback_rows.append(row)

            for row in preferred_rows + fallback_rows:
                for field_key in ("istc_totqy", "distb_stock_co", "stock_totqy"):
                    parsed = _to_optional_int(row.get(field_key))
                    if parsed and parsed > 0:
                        return parsed
        return None

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

    def _extract_document_html(self, zip_bytes: bytes) -> str:
        """Extract the largest DART HTML/XML document from a document.xml zip."""
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
        return self._decode_document_bytes(raw)

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

    @staticmethod
    def _expand_table_to_grid(table: Tag) -> list[list[str]]:
        """Expand rowspan/colspan table cells into a logical 2D grid."""
        grid: dict[tuple[int, int], str] = {}
        row_idx = 0
        for tr in table.find_all("tr"):
            if tr.find_parent("table") is not table:
                continue
            col_idx = 0
            for cell in tr.find_all(["td", "th"], recursive=False):
                while (row_idx, col_idx) in grid:
                    col_idx += 1
                text = " ".join(cell.get_text(" ", strip=True).split())
                rowspan = _span_to_int(cell.get("rowspan", 1))
                colspan = _span_to_int(cell.get("colspan", 1))
                for r in range(rowspan):
                    for c in range(colspan):
                        grid[(row_idx + r, col_idx + c)] = text
                col_idx += colspan
            row_idx += 1

        if not grid:
            return []
        max_row = max(r for r, _ in grid) + 1
        max_col = max(c for _, c in grid) + 1
        return [[grid.get((r, c), "") for c in range(max_col)] for r in range(max_row)]

    @staticmethod
    def _serialize_grid(grid: list[list[str]]) -> str:
        """Serialize a logical table grid into RAG-friendly row text."""
        if not grid:
            return ""

        prefix = ""
        row_start = 0
        first_row_values = [cell.strip() for cell in grid[0] if cell.strip()]
        if len(first_row_values) == 1 and len(grid) > 1:
            prefix = f"[{first_row_values[0]}] "
            row_start = 1

        remaining = grid[row_start:]
        if not remaining:
            return prefix.strip()

        if len(remaining) == 1:
            cells = [cell.strip() for cell in remaining[0] if cell.strip() and cell.strip() != "-"]
            return (prefix + " | ".join(cells)).strip()

        headers = [header.strip() for header in remaining[0]]
        lines: list[str] = []

        for row in remaining[1:]:
            parts: list[str] = []
            for index, value in enumerate(row):
                value = value.strip()
                if not value or value == "-":
                    continue
                header = headers[index] if index < len(headers) else ""
                parts.append(f"{header}: {value}" if header else value)
            if parts:
                lines.append(prefix + " | ".join(parts))

        if not lines:
            for row in remaining:
                cells = [cell.strip() for cell in row if cell.strip() and cell.strip() != "-"]
                if cells:
                    lines.append(prefix + " | ".join(cells))

        return "\n".join(lines)

    @staticmethod
    def _extract_footnotes(table: Tag) -> list[str]:
        """Collect footnotes that immediately follow a table."""
        footnote_pattern = re.compile(r"^\(\*\d*\)|^\(주\d*\)|^※|^\*\s")
        footnotes: list[str] = []
        sibling = table.next_sibling
        count = 0
        while sibling is not None and count < 10:
            if hasattr(sibling, "name") and sibling.name == "table":
                break
            text = ""
            if hasattr(sibling, "get_text"):
                text = sibling.get_text(" ", strip=True)
            elif isinstance(sibling, str):
                text = sibling.strip()
            if text and footnote_pattern.match(text):
                footnotes.append(text)
            sibling = sibling.next_sibling
            count += 1
        return footnotes

    def serialize_table_full(self, table: Tag) -> str:
        grid = self._expand_table_to_grid(table)
        text = self._serialize_grid(grid)
        footnotes = self._extract_footnotes(table)
        if footnotes:
            text += "\n[각주]\n" + "\n".join(footnotes)
        return text

    def extract_document_text_v2(self, zip_bytes: bytes) -> str:
        """Convert a DART filing document into structure-preserving RAG text."""
        html = self._extract_document_html(zip_bytes)
        if not html:
            return ""
        try:
            soup = BeautifulSoup(html, "html.parser")
        except Exception:
            return ""

        for tag in soup.find_all(["script", "style", "head"]):
            tag.decompose()

        body = soup.find("body") or soup
        nested_table_ids: set[int] = set()
        for table in body.find_all("table"):
            for nested in table.find_all("table"):
                nested_table_ids.add(id(nested))

        seen_table_ids: set[int] = set()
        sections: list[str] = []

        for element in body.find_all(["h1", "h2", "h3", "h4", "p", "table"]):
            if element.name in ("h1", "h2", "h3", "h4"):
                if element.find_parent("table"):
                    continue
                heading = element.get_text(" ", strip=True)
                if heading:
                    sections.append(f"## {heading}")
            elif element.name == "p":
                if element.find_parent("table"):
                    continue
                text = element.get_text(" ", strip=True)
                if text:
                    sections.append(text)
            elif element.name == "table":
                if id(element) in nested_table_ids or id(element) in seen_table_ids:
                    continue
                seen_table_ids.add(id(element))
                table_text = self.serialize_table_full(element)
                if table_text.strip():
                    sections.append(table_text)

        result = "\n".join(sections)
        result = re.sub(r"\n{3,}", "\n\n", result)
        return result.strip()

    def build_filing_chunks(
        self,
        zip_bytes: bytes,
        *,
        symbol: str,
        filing_id: str,
        filing_title: str,
        disclosed_at: str,
        max_chunk_chars: int = 1200,
    ) -> list[ChromaDocument]:
        """Build section-level Chroma documents for a DART filing."""
        from app.external.chroma_client import ChromaDocument

        text = self.extract_document_text_v2(zip_bytes)
        if not text.strip():
            return []

        raw_sections = re.split(r"\n## ", text)
        all_chunks: list[ChromaDocument] = []

        for section_idx, section_text in enumerate(raw_sections):
            section_text = section_text.strip()
            if not section_text:
                continue

            if section_idx == 0 and not text.startswith("## "):
                section_title = filing_title
                section_body = section_text
            else:
                first_line, _, rest = section_text.partition("\n")
                section_title = first_line.strip().lstrip("#").strip() or filing_title
                section_body = rest.strip() or section_text

            for chunk_idx, chunk_text in enumerate(_split_text(section_body, max_chunk_chars)):
                if not chunk_text.strip():
                    continue
                doc_id = f"{filing_id}:s{section_idx}:c{chunk_idx}"
                document = f"{filing_title}\n{section_title}\n\n{chunk_text}".strip()
                metadata: dict[str, str | int] = {
                    "symbol": symbol,
                    "source_type": "filing",
                    "source_id": filing_id,
                    "filing_title": filing_title,
                    "section": section_title,
                    "chunk_index": chunk_idx,
                }
                if disclosed_at:
                    metadata["disclosed_at"] = disclosed_at
                all_chunks.append(ChromaDocument(id=doc_id, document=document, metadata=metadata))

        return all_chunks

    def fetch_filing_text(self, receipt_no: str) -> str:
        try:
            zip_bytes = self.fetch_document_xml(receipt_no)
        except requests.RequestException as exc:
            raise DartApiError(f"DART document.xml request failed: receipt_no={receipt_no}") from exc

        text = self.extract_document_text_v2(zip_bytes)
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
