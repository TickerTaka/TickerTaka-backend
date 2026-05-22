from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from io import BytesIO
from typing import ClassVar
import zipfile
import xml.etree.ElementTree as ET

from bs4 import BeautifulSoup
import requests

from app.config import get_settings


_MIN_FILING_TEXT_LEN = 50


@dataclass(frozen=True)
class DartCorpCode:
    corp_code: str
    corp_name: str
    stock_code: str | None
    modify_date: str | None


@dataclass(frozen=True)
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


class DartClient:
    """Thin client for DART OpenAPI disclosure metadata."""

    base_url = "https://opendart.fss.or.kr/api"
    viewer_base_url = "https://dart.fss.or.kr/dsaf001/main.do"
    _corp_codes_by_stock_code_cache: ClassVar[dict[str, DartCorpCode] | None] = None

    def __init__(self) -> None:
        settings = get_settings()
        self.api_key = settings.dart_api_key
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "TickerTaka/0.1"})

    def get_corp_code_by_stock_code(self, stock_code: str) -> DartCorpCode | None:
        return self.get_corp_codes_by_stock_code().get(stock_code)

    def get_corp_codes_by_stock_code(self) -> dict[str, DartCorpCode]:
        if self.__class__._corp_codes_by_stock_code_cache is None:
            corp_codes = self.fetch_corp_codes()
            self.__class__._corp_codes_by_stock_code_cache = {
                item.stock_code: item
                for item in corp_codes
                if item.stock_code
            }
        return self.__class__._corp_codes_by_stock_code_cache

    def fetch_corp_codes(self, *, timeout: float = 20.0) -> list[DartCorpCode]:
        self._ensure_api_key()
        response = self.session.get(
            f"{self.base_url}/corpCode.xml",
            params={"crtfc_key": self.api_key},
            timeout=timeout,
        )
        response.raise_for_status()
        return self._parse_corp_code_zip(response.content)

    def list_filings(
        self,
        corp_code: str,
        *,
        begin_date: date,
        end_date: date,
        page_count: int = 100,
        last_report_only: bool = False,
        timeout: float = 10.0,
    ) -> list[DartFilingItem]:
        self._ensure_api_key()
        response = self.session.get(
            f"{self.base_url}/list.json",
            params={
                "crtfc_key": self.api_key,
                "corp_code": corp_code,
                "bgn_de": begin_date.strftime("%Y%m%d"),
                "end_de": end_date.strftime("%Y%m%d"),
                "last_reprt_at": "Y" if last_report_only else "N",
                "sort": "date",
                "sort_mth": "desc",
                "page_no": 1,
                "page_count": page_count,
            },
            timeout=timeout,
        )
        response.raise_for_status()
        payload = response.json()
        status = str(payload.get("status", ""))
        if status == "013":
            return []
        if status != "000":
            message = payload.get("message") or "unknown DART API error"
            raise DartApiError(f"DART list.json failed: status={status} message={message}")
        return [self._to_filing_item(item) for item in payload.get("list", [])]

    @classmethod
    def build_viewer_url(cls, receipt_no: str) -> str:
        return f"{cls.viewer_base_url}?rcpNo={receipt_no}"

    def fetch_document_xml(self, receipt_no: str, *, timeout: float = 20.0) -> bytes:
        self._ensure_api_key()
        response = self.session.get(
            f"{self.base_url}/document.xml",
            params={
                "crtfc_key": self.api_key,
                "rcept_no": receipt_no,
            },
            timeout=timeout,
        )
        response.raise_for_status()
        return response.content

    def extract_document_text(self, zip_bytes: bytes) -> str:
        try:
            with zipfile.ZipFile(BytesIO(zip_bytes)) as archive:
                names = [
                    name
                    for name in archive.namelist()
                    if name.lower().endswith((".html", ".htm", ".xml"))
                ]
                if not names:
                    return ""

                html_names = [name for name in names if name.lower().endswith((".html", ".htm"))]
                candidates = html_names or names
                selected_name = max(
                    candidates,
                    key=lambda name: archive.getinfo(name).file_size,
                )
                raw = archive.read(selected_name)
        except (zipfile.BadZipFile, KeyError, OSError):
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

    def _ensure_api_key(self) -> None:
        if not self.api_key:
            raise DartApiError("DART_API_KEY is not configured")

    @staticmethod
    def _decode_document_bytes(raw: bytes) -> str:
        for encoding in ("utf-8", "euc-kr", "cp949"):
            try:
                return raw.decode(encoding)
            except UnicodeDecodeError:
                continue
        return raw.decode("utf-8", errors="ignore")

    @staticmethod
    def _parse_corp_code_zip(content: bytes) -> list[DartCorpCode]:
        try:
            with zipfile.ZipFile(BytesIO(content)) as archive:
                xml_name = next((name for name in archive.namelist() if name.lower().endswith(".xml")), None)
                if xml_name is None:
                    raise DartApiError("DART corpCode.xml response did not contain an XML file")
                xml_bytes = archive.read(xml_name)
        except zipfile.BadZipFile as exc:
            raise DartApiError("DART corpCode.xml response was not a ZIP file") from exc

        root = ET.fromstring(xml_bytes)
        corp_codes: list[DartCorpCode] = []
        for node in root.findall("list"):
            stock_code = (node.findtext("stock_code") or "").strip() or None
            corp_codes.append(
                DartCorpCode(
                    corp_code=(node.findtext("corp_code") or "").strip(),
                    corp_name=(node.findtext("corp_name") or "").strip(),
                    stock_code=stock_code,
                    modify_date=(node.findtext("modify_date") or "").strip() or None,
                )
            )
        return [item for item in corp_codes if item.corp_code]

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
