from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html import unescape
from urllib.parse import urlparse
import re

KST = timezone(timedelta(hours=9))

import requests

from app.config import get_settings

TAG_RE = re.compile(r"<[^>]+>")
WHITESPACE_RE = re.compile(r"\s+")


@dataclass(slots=True)
class NaverNewsItem:
    title: str
    description: str
    link: str
    original_link: str | None
    published_at: datetime | None
    source_name: str | None


class NaverNewsClient:
    """Thin client for the Naver news search API."""

    base_url = "https://openapi.naver.com/v1/search/news.json"

    def __init__(self) -> None:
        settings = get_settings()
        self.client_id = settings.naver_news_client_id
        self.client_secret = settings.naver_news_client_secret
        self.session = requests.Session()
        self.session.headers.update(
            {
                "X-Naver-Client-Id": self.client_id,
                "X-Naver-Client-Secret": self.client_secret,
                "User-Agent": "TickerTaka/0.1",
            }
        )

    def search_news(
        self,
        query: str,
        display: int,
        *,
        start: int = 1,
        sort: str = "date",
        timeout: float = 10.0,
    ) -> list[NaverNewsItem]:
        response = self.session.get(
            self.base_url,
            params={"query": query, "display": display, "start": start, "sort": sort},
            timeout=timeout,
        )
        response.raise_for_status()
        payload = response.json()
        return [self._to_item(item) for item in payload.get("items", [])]

    def _to_item(self, item: dict) -> NaverNewsItem:
        original_link = item.get("originallink") or None
        link = item.get("link", "")
        source_url = original_link or link
        return NaverNewsItem(
            title=self._clean_text(item.get("title", "")),
            description=self._clean_text(item.get("description", "")),
            link=link,
            original_link=original_link,
            published_at=self._parse_pub_date(item.get("pubDate")),
            source_name=self._infer_source_name(source_url),
        )

    @staticmethod
    def _clean_text(value: str) -> str:
        return WHITESPACE_RE.sub(" ", unescape(TAG_RE.sub(" ", value))).strip()

    @staticmethod
    def _parse_pub_date(value: str | None) -> datetime | None:
        if not value:
            return None
        parsed = parsedate_to_datetime(value)
        if parsed.tzinfo is None:
            # Naver API rarely omits timezone; assume KST for Korean news source
            parsed = parsed.replace(tzinfo=KST)
        return parsed.astimezone(UTC)

    @staticmethod
    def _infer_source_name(url: str) -> str | None:
        hostname = urlparse(url).hostname
        if not hostname:
            return None
        hostname = hostname.lower()
        if hostname.startswith("www."):
            hostname = hostname[4:]
        return hostname
