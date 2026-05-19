from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse

import requests

try:
    import trafilatura
    from trafilatura.metadata import extract_metadata
except ModuleNotFoundError:  # pragma: no cover - depends on runtime environment
    trafilatura = None
    extract_metadata = None


@dataclass(slots=True)
class ScrapedArticle:
    content: str | None
    summary: str | None
    source_name: str | None
    canonical_url: str | None
    published_at: datetime | None


class ArticleScraper:
    """Article body extraction wrapper."""

    def __init__(self) -> None:
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "TickerTaka/0.1"})

    def scrape(self, url: str, timeout: tuple[float, float] = (3.0, 7.0)) -> ScrapedArticle:
        if trafilatura is None or extract_metadata is None:
            raise RuntimeError("trafilatura is not installed")
        response = self.session.get(url, timeout=timeout)
        response.raise_for_status()
        html = response.text
        content = trafilatura.extract(
            html,
            include_comments=False,
            include_links=False,
            include_formatting=False,
        )
        metadata = extract_metadata(html)
        summary = self._build_summary(content)
        source_name = getattr(metadata, "sitename", None) or self._infer_source_name(url)
        canonical_url = getattr(metadata, "canonical", None)
        published_at = self._parse_datetime(getattr(metadata, "date", None))
        return ScrapedArticle(
            content=content.strip() if content else None,
            summary=summary,
            source_name=source_name,
            canonical_url=canonical_url,
            published_at=published_at,
        )

    @staticmethod
    def _build_summary(content: str | None, limit: int = 280) -> str | None:
        if not content:
            return None
        text = " ".join(content.split())
        return text[:limit].rstrip() if text else None

    @staticmethod
    def _infer_source_name(url: str) -> str | None:
        hostname = urlparse(url).hostname
        if not hostname:
            return None
        return hostname[4:] if hostname.startswith("www.") else hostname

    @staticmethod
    def _parse_datetime(value: str | None) -> datetime | None:
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            try:
                parsed = parsedate_to_datetime(value)
            except (TypeError, ValueError):
                return None
        if parsed.tzinfo is None:
            # Most Korean news sites omit tz; default to KST for safety
            parsed = parsed.replace(tzinfo=timezone(timedelta(hours=9)))
        return parsed.astimezone(UTC)
