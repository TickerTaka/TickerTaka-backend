from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse
from uuid import uuid4
import logging
import re

from sqlalchemy.orm import Session

from app.config import get_settings
from app.external.article_scraper import ArticleScraper, ScrapedArticle
from app.external.naver_news import NaverNewsClient, NaverNewsItem
from app.models import NewsCache, TickerMetadata
from app.repositories.news_cache_repository import NewsCacheRepository

logger = logging.getLogger(__name__)

try:
    import redis
except ModuleNotFoundError:  # pragma: no cover - depends on runtime environment
    redis = None

TRACKING_QUERY_KEYS = {"fbclid", "gclid", "ref"}
TRACKING_QUERY_PREFIXES = ("utm_",)
TITLE_TOKEN_RE = re.compile(r"[^\w\s]")


@dataclass(slots=True)
class SyncNewsResult:
    fetched_count: int = 0
    inserted_count: int = 0
    updated_count: int = 0
    skipped_count: int = 0
    body_failed_count: int = 0
    grouped_count: int = 0
    body_saved_count: int = 0
    trimmed_rows_count: int = 0
    trimmed_content_count: int = 0
    elapsed_ms: int = 0


@dataclass(slots=True)
class NewsCandidate:
    item: NaverNewsItem
    normalized_url: str
    existing: NewsCache | None
    title_tokens: set[str]


class NewsIngestionService:
    """Implements the news_cache ingestion policy."""

    INITIAL_FETCH_COUNT = 15
    REFRESH_FETCH_COUNT = 5
    BODY_CRAWL_LIMIT = 5
    MAX_CACHE_ROWS = 100
    MAX_CONTENT_ROWS = 10
    COOLDOWN_MINUTES = 15
    LOCK_TTL_SECONDS = 600
    RECENT_GROUPING_HOURS = 24

    def __init__(
        self,
        session: Session,
        *,
        news_client: NaverNewsClient | None = None,
        article_scraper: ArticleScraper | None = None,
        redis_client: redis.Redis | None = None,
    ) -> None:
        settings = get_settings()
        self.session = session
        self.repo = NewsCacheRepository(session)
        self.news_client = news_client or NaverNewsClient()
        self.article_scraper = article_scraper or ArticleScraper()
        self.redis_client = redis_client or self._build_redis_client(settings.redis_url)

    def sync_news_for_ticker(
        self,
        symbol: str,
        mode: str = "initial",
        force: bool = False,
        limit: int | None = None,
    ) -> SyncNewsResult:
        started = datetime.now(UTC)
        result = SyncNewsResult()

        ticker = self.session.get(TickerMetadata, symbol)
        if ticker is None:
            raise ValueError(f"ticker not found: {symbol}")

        lock_token = self._acquire_lock(symbol)
        if lock_token is None:
            logger.info(
                "news sync skipped: lock unavailable or already held for %s",
                symbol,
            )
            result.skipped_count += 1
            return result

        try:
            if not force and self._is_within_cooldown(symbol):
                logger.info("news sync skipped: cooldown active for %s", symbol)
                result.skipped_count += 1
                return result

            fetch_limit = limit or (self.INITIAL_FETCH_COUNT if mode == "initial" else self.REFRESH_FETCH_COUNT)
            items = self.news_client.search_news(ticker.name_kr, display=fetch_limit, sort="date")
            result.fetched_count = len(items)

            candidates = self._build_candidates(symbol, items)
            recent_rows = self.repo.get_recent_by_symbol(symbol, since_hours=self.RECENT_GROUPING_HOURS)
            body_candidates = self._select_body_candidates(candidates, recent_rows)
            result.grouped_count = len(body_candidates)

            body_urls = {
                candidate.normalized_url: self._scrape_candidate(candidate, result)
                for candidate in body_candidates
            }

            for candidate in candidates:
                scraped = body_urls.get(candidate.normalized_url)
                if candidate.existing is None:
                    row = self._build_news_row(symbol, candidate, scraped)
                    self.repo.save(row)
                    result.inserted_count += 1
                    if row.content:
                        result.body_saved_count += 1
                else:
                    if candidate.existing.content is None and scraped and scraped.content:
                        candidate.existing.content = scraped.content
                        candidate.existing.summary = scraped.summary or candidate.existing.summary
                        candidate.existing.source_name = scraped.source_name or candidate.existing.source_name
                        result.updated_count += 1
                        result.body_saved_count += 1
                    else:
                        result.skipped_count += 1

            result.trimmed_rows_count = self.repo.trim_rows_for_symbol(symbol, self.MAX_CACHE_ROWS)
            result.trimmed_content_count = self.repo.trim_content_for_symbol(symbol, self.MAX_CONTENT_ROWS)
            self._set_last_run(symbol, started)
            self.session.flush()
        finally:
            self._release_lock(symbol, lock_token)
            result.elapsed_ms = int((datetime.now(UTC) - started).total_seconds() * 1000)

        logger.info(
            "news sync finished",
            extra={
                "symbol": symbol,
                "fetched": result.fetched_count,
                "inserted": result.inserted_count,
                "updated": result.updated_count,
                "skipped": result.skipped_count,
                "body_failed": result.body_failed_count,
                "grouped": result.grouped_count,
                "body_saved": result.body_saved_count,
                "trimmed_rows": result.trimmed_rows_count,
                "trimmed_content": result.trimmed_content_count,
                "elapsed_ms": result.elapsed_ms,
            },
        )
        return result

    def _build_candidates(self, symbol: str, items: list[NaverNewsItem]) -> list[NewsCandidate]:
        urls = [self.normalize_url(item.original_link or item.link) for item in items if (item.original_link or item.link)]
        existing_map = self.repo.get_by_source_urls(urls)
        candidates: list[NewsCandidate] = []
        for item in items:
            raw_url = item.original_link or item.link
            if not raw_url:
                continue
            normalized = self.normalize_url(raw_url)
            candidates.append(
                NewsCandidate(
                    item=item,
                    normalized_url=normalized,
                    existing=existing_map.get(normalized),
                    title_tokens=self.normalize_title(item.title),
                )
            )
        return candidates

    def _select_body_candidates(
        self,
        candidates: list[NewsCandidate],
        recent_rows: list[NewsCache],
    ) -> list[NewsCandidate]:
        selected: list[NewsCandidate] = []
        groups: list[tuple[set[str], datetime | None]] = []
        recent_titles: list[tuple[set[str], bool, datetime | None]] = [
            (self.normalize_title(row.title), row.content is not None, row.published_at)
            for row in recent_rows
            if row.title
        ]

        def group_has_content(tokens: set[str], tokens_published_at: datetime | None) -> bool:
            for existing_tokens, has_content, existing_published_at in recent_titles:
                if has_content and self._titles_similar(
                    tokens, existing_tokens, tokens_published_at, existing_published_at
                ):
                    return True
            return False

        epoch_floor = datetime.min.replace(tzinfo=UTC)

        new_candidates = [c for c in candidates if c.existing is None]
        new_candidates.sort(
            key=lambda c: (
                c.item.published_at or epoch_floor,
                bool(c.item.original_link),
            ),
            reverse=True,
        )

        for candidate in new_candidates:
            if len(selected) >= self.BODY_CRAWL_LIMIT:
                break
            if len(candidate.title_tokens) < 5:
                selected.append(candidate)
                groups.append((candidate.title_tokens, candidate.item.published_at))
                continue
            if group_has_content(candidate.title_tokens, candidate.item.published_at):
                continue
            if any(
                self._titles_similar(
                    candidate.title_tokens,
                    group_tokens,
                    candidate.item.published_at,
                    group_published_at,
                )
                for group_tokens, group_published_at in groups
            ):
                continue
            selected.append(candidate)
            groups.append((candidate.title_tokens, candidate.item.published_at))

        if len(selected) >= self.BODY_CRAWL_LIMIT:
            return selected

        null_candidates = [c for c in candidates if c.existing is not None and c.existing.content is None]
        null_candidates.sort(
            key=lambda c: c.existing.published_at or epoch_floor,
            reverse=True,
        )
        for candidate in null_candidates:
            if len(selected) >= self.BODY_CRAWL_LIMIT:
                break
            candidate_published_at = (
                candidate.existing.published_at if candidate.existing else candidate.item.published_at
            )
            if any(
                self._titles_similar(
                    candidate.title_tokens,
                    group_tokens,
                    candidate_published_at,
                    group_published_at,
                )
                for group_tokens, group_published_at in groups
            ):
                continue
            selected.append(candidate)
            groups.append((candidate.title_tokens, candidate_published_at))

        return selected

    def _build_news_row(
        self,
        symbol: str,
        candidate: NewsCandidate,
        scraped: ScrapedArticle | None,
    ) -> NewsCache:
        published_at = scraped.published_at if scraped and scraped.published_at else candidate.item.published_at
        ttl_anchor = published_at or datetime.now(UTC)
        ttl_until = ttl_anchor + timedelta(days=30)
        return NewsCache(
            symbol=symbol,
            title=candidate.item.title,
            content=scraped.content if scraped else None,
            summary=(scraped.summary if scraped and scraped.summary else candidate.item.description) or None,
            source_name=(scraped.source_name if scraped and scraped.source_name else candidate.item.source_name),
            source_url=candidate.normalized_url,
            published_at=published_at,
            ttl_until=ttl_until,
        )

    def _scrape_candidate(
        self, candidate: NewsCandidate, result: SyncNewsResult
    ) -> ScrapedArticle | None:
        try:
            return self.article_scraper.scrape(candidate.normalized_url)
        except Exception:
            result.body_failed_count += 1
            logger.exception("article scrape failed for %s", candidate.normalized_url)
            return None

    @staticmethod
    def normalize_url(url: str) -> str:
        parsed = urlparse(url.strip())
        scheme = (parsed.scheme or "https").lower()
        netloc = parsed.netloc.lower()
        if netloc.startswith("www."):
            netloc = netloc[4:]
        path = parsed.path.rstrip("/") or "/"
        query_params = []
        for key, value in parse_qsl(parsed.query, keep_blank_values=False):
            lowered = key.lower()
            if lowered in TRACKING_QUERY_KEYS or any(lowered.startswith(prefix) for prefix in TRACKING_QUERY_PREFIXES):
                continue
            query_params.append((key, value))
        query = urlencode(query_params)
        return urlunparse((scheme, netloc, path, "", query, ""))

    @staticmethod
    def normalize_title(title: str) -> set[str]:
        cleaned = TITLE_TOKEN_RE.sub(" ", title).lower()
        return {token for token in cleaned.split() if len(token) > 1}

    @staticmethod
    def _titles_similar(
        first: set[str],
        second: set[str],
        first_published_at: datetime | None,
        second_published_at: datetime | None,
    ) -> bool:
        if len(first) < 5 or len(second) < 5:
            return False
        union = first | second
        if not union:
            return False
        similarity = len(first & second) / len(union)
        if similarity < 0.7:
            return False
        if first_published_at and second_published_at:
            if abs(first_published_at - second_published_at) > timedelta(hours=6):
                return False
        return True

    @staticmethod
    def _build_redis_client(redis_url: str) -> redis.Redis | None:
        if redis is None:
            return None
        try:
            return redis.Redis.from_url(redis_url, decode_responses=True)
        except Exception:
            logger.exception("failed to create redis client")
            return None

    def _acquire_lock(self, symbol: str) -> str | None:
        # fail-closed: Redis lock is required by the ingestion policy.
        # If Redis is unreachable, we skip the sync rather than running unlocked.
        if self.redis_client is None:
            logger.error("redis client unavailable; skipping sync for %s", symbol)
            return None
        token = str(uuid4())
        try:
            acquired = self.redis_client.set(
                self._lock_key(symbol), token, nx=True, ex=self.LOCK_TTL_SECONDS
            )
            return token if acquired else None
        except Exception:
            logger.exception("redis lock error; skipping sync for %s", symbol)
            return None

    def _release_lock(self, symbol: str, token: str | None) -> None:
        if self.redis_client is None or token is None:
            return
        try:
            if self.redis_client.get(self._lock_key(symbol)) == token:
                self.redis_client.delete(self._lock_key(symbol))
        except Exception:
            logger.exception("failed to release redis lock for %s", symbol)

    def _is_within_cooldown(self, symbol: str) -> bool:
        if self.redis_client is None:
            return False
        try:
            raw = self.redis_client.get(self._last_run_key(symbol))
            if raw is None:
                return False
            last_run = datetime.fromtimestamp(float(raw), tz=UTC)
            return datetime.now(UTC) - last_run < timedelta(minutes=self.COOLDOWN_MINUTES)
        except Exception:
            logger.exception("failed to read cooldown for %s", symbol)
            return False

    def _set_last_run(self, symbol: str, started_at: datetime) -> None:
        if self.redis_client is None:
            return
        try:
            self.redis_client.set(self._last_run_key(symbol), started_at.timestamp(), ex=60 * 60 * 24)
        except Exception:
            logger.exception("failed to persist cooldown for %s", symbol)

    @staticmethod
    def _lock_key(symbol: str) -> str:
        return f"news-sync:lock:{symbol}"

    @staticmethod
    def _last_run_key(symbol: str) -> str:
        return f"news-sync:last-run:{symbol}"
