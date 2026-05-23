from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zipfile import ZipFile
from xml.etree import ElementTree

import requests

from app.config import get_settings


@dataclass(slots=True)
class CorpCodeProvider:
    cache_path: Path = field(default_factory=lambda: Path("seeds/corp_code_map.json"))
    refresh_interval_days: int = 365
    session: requests.Session | None = None
    _mapping: dict[str, str] | None = field(default=None, init=False, repr=False)

    def get_corp_code(self, symbol: str) -> str | None:
        return self.load_mapping().get(symbol)

    def load_mapping(self, force_refresh: bool = False) -> dict[str, str]:
        if self._mapping is not None and not force_refresh:
            return self._mapping
        if not force_refresh and self._is_cache_fresh():
            self._mapping = self._read_cache()
            return self._mapping
        self._mapping = self._download_mapping()
        self._write_cache(self._mapping)
        return self._mapping

    def _is_cache_fresh(self) -> bool:
        if not self.cache_path.exists():
            return False
        modified = datetime.fromtimestamp(self.cache_path.stat().st_mtime, UTC)
        return modified >= datetime.now(UTC) - timedelta(days=self.refresh_interval_days)

    def _read_cache(self) -> dict[str, str]:
        return json.loads(self.cache_path.read_text(encoding="utf-8"))

    def _write_cache(self, mapping: dict[str, str]) -> None:
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.cache_path.write_text(
            json.dumps(mapping, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )

    def _download_mapping(self) -> dict[str, str]:
        settings = get_settings()
        if not settings.dart_api_key:
            raise RuntimeError("DART_API_KEY is required to download corp code map")
        session = self.session or requests.Session()
        response = session.get(
            "https://opendart.fss.or.kr/api/corpCode.xml",
            params={"crtfc_key": settings.dart_api_key},
            timeout=30,
        )
        response.raise_for_status()

        from io import BytesIO

        mapping: dict[str, str] = {}
        with ZipFile(BytesIO(response.content)) as zip_file:
            with zip_file.open("CORPCODE.xml") as xml_file:
                root = ElementTree.parse(xml_file).getroot()
        for item in root.findall("list"):
            stock_code = (item.findtext("stock_code") or "").strip()
            corp_code = (item.findtext("corp_code") or "").strip()
            if stock_code and corp_code:
                mapping[stock_code] = corp_code
        return mapping
