from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse
from uuid import uuid4
import logging
import re
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.config import get_settings
from app.core.redis import build_redis_client, make_key
from app.domain.evidence_indexing import EvidenceIndexingService
from app.external.article_scraper import ArticleScraper, ScrapedArticle
from app.external.chroma_client import ChromaClient, NEWS_COLLECTION_NAME
from app.external.embedding import EmbeddingClient, get_embedding_client
from app.external.naver_news import NaverNewsClient, NaverNewsItem
from app.models import NewsCache, TickerMetadata
from app.repositories.news_cache_repository import NewsCacheRepository

logger = logging.getLogger(__name__)

try:
    import redis
except Exception:  # pragma: no cover - depends on runtime environment
    redis = None

TRACKING_QUERY_KEYS = {"fbclid", "gclid", "ref"}
TRACKING_QUERY_PREFIXES = ("utm_",)
TITLE_TOKEN_RE = re.compile(r"[^\w\s]")
HANGUL_RE = re.compile(r"[가-힣]")
WHITESPACE_RE = re.compile(r"\s+")
AD_MARKERS = ("[광고]", " 광고 ", "ad:", "sponsored", "pr newswire", "globenewswire", "보도자료")
MIRROR_MARKERS = ("무단전재", "재배포", "기사제보")
TITLE_EXCLUDE_MARKERS = ("[포토]", "[사진]", "[그래픽]", "[표]", "[인포그래픽]")
KST = ZoneInfo("Asia/Seoul")


@dataclass(slots=True)
class SyncNewsResult:
    fetched_count: int = 0
    inserted_count: int = 0
    updated_count: int = 0
    skipped_count: int = 0
    body_failed_count: int = 0
    grouped_count: int = 0
    body_quota_saved_count: int = 0
    body_attempts_count: int = 0
    body_saved_count: int = 0
    filtered_count: int = 0
    dedup_skipped_count: int = 0
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

    INITIAL_FETCH_COUNT = 30
    REFRESH_FETCH_COUNT = 5
    BODY_CRAWL_LIMIT = 10
    BODY_ATTEMPTS_PER_GROUP = 3
    MAX_CACHE_ROWS = 10
    MAX_ARTICLE_AGE_DAYS = 7
    MIN_TITLE_LENGTH = 8
    MIN_CONTENT_LENGTH = 120
    COOLDOWN_MINUTES = 15
    LOCK_TTL_SECONDS = 600
    RECENT_GROUPING_HOURS = 24
    DAILY_API_COUNT_TTL_SECONDS = 60 * 60 * 48

    def __init__(
        self,
        session: Session,
        *,
        news_client: NaverNewsClient | None = None,
        article_scraper: ArticleScraper | None = None,
        redis_client: redis.Redis | None = None,
        chroma_client: ChromaClient | None = None,
        embedding_client: EmbeddingClient | None = None,
    ) -> None:
        settings = get_settings()
        self.session = session
        self.repo = NewsCacheRepository(session)
        self.news_client = news_client or NaverNewsClient()
        self.article_scraper = article_scraper or ArticleScraper()
        self.redis_client = redis_client or build_redis_client(settings.redis_url)
        self.chroma_client = chroma_client
        self.embedding_client = embedding_client

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
            items = self.news_client.search_news(ticker.name_kr, display=fetch_limit, sort="sim")
            self._record_daily_api_call()
            result.fetched_count = len(items)

            candidates = self._build_candidates(symbol, items)
            candidates = self._prefilter_candidates(ticker, candidates, result)
            recent_rows = self.repo.get_recent_by_symbol(symbol, since_hours=self.RECENT_GROUPING_HOURS)
            body_candidate_groups = self._select_body_candidate_groups(candidates, recent_rows)
            result.grouped_count = len(body_candidate_groups)
            result.body_quota_saved_count = max(0, len(candidates) - len(body_candidate_groups))

            body_urls: dict[str, ScrapedArticle] = {}
            for group in body_candidate_groups:
                for candidate in group:
                    result.body_attempts_count += 1
                    scraped = self._scrape_candidate(candidate, result)
                    if scraped is None or not (scraped.content and scraped.content.strip()):
                        continue
                    if not self._passes_storage_filters(ticker, candidate, scraped):
                        continue
                    body_urls[candidate.normalized_url] = scraped
                    break

            for candidate in candidates:
                scraped = body_urls.get(candidate.normalized_url)
                if candidate.existing is not None:
                    result.dedup_skipped_count += 1
                    continue
                if self._has_recent_similar_topic(candidate, recent_rows):
                    result.dedup_skipped_count += 1
                    continue
                if not self._passes_storage_filters(ticker, candidate, scraped):
                    result.filtered_count += 1
                    continue
                row = self._build_news_row(symbol, candidate, scraped)
                self.repo.save(row)
                self._upsert_chroma_row(row, scraped)
                result.inserted_count += 1
                result.body_saved_count += 1
                recent_rows.insert(0, row)

            trimmed_ids = self.repo.trim_rows_for_symbol_returning_ids(symbol, self.MAX_CACHE_ROWS)
            result.trimmed_rows_count = len(trimmed_ids)
            self._delete_chroma_documents(trimmed_ids)
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
                "body_quota_saved": result.body_quota_saved_count,
                "body_attempts": result.body_attempts_count,
                "body_saved": result.body_saved_count,
                "dedup_skipped": result.dedup_skipped_count,
                "trimmed_rows": result.trimmed_rows_count,
                "trimmed_content": result.trimmed_content_count,
                "elapsed_ms": result.elapsed_ms,
            },
        )
        return result

    def _upsert_chroma_row(self, row: NewsCache, scraped: ScrapedArticle | None) -> None:
        if scraped is None or not scraped.content or not scraped.content.strip():
            return
        try:
            client = self._get_chroma_client()
            embedding_client = self._get_embedding_client()
            client.upsert(
                NEWS_COLLECTION_NAME,
                documents=[
                    EvidenceIndexingService.build_news_document(
                        row,
                        content=scraped.content,
                    )
                ],
                embedding_client=embedding_client,
            )
        except Exception:
            logger.exception("direct chroma upsert failed for news row %s", row.id)

    def _delete_chroma_documents(self, ids: list[str]) -> None:
        if not ids:
            return
        try:
            client = self._get_chroma_client()
            client.delete(NEWS_COLLECTION_NAME, ids=ids)
        except Exception:
            logger.exception("direct chroma delete failed for %s ids", len(ids))

    def _get_chroma_client(self) -> ChromaClient:
        if self.chroma_client is None:
            self.chroma_client = ChromaClient()
        return self.chroma_client

    def _get_embedding_client(self) -> EmbeddingClient:
        if self.embedding_client is None:
            self.embedding_client = get_embedding_client()
        return self.embedding_client

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

    def _prefilter_candidates(
        self,
        ticker: TickerMetadata,
        candidates: list[NewsCandidate],
        result: SyncNewsResult,
    ) -> list[NewsCandidate]:
        filtered: list[NewsCandidate] = []
        for candidate in candidates:
            if self._passes_prefilter(ticker, candidate):
                filtered.append(candidate)
            else:
                result.filtered_count += 1
        return filtered

    def _select_body_candidate_groups(
        self,
        candidates: list[NewsCandidate],
        recent_rows: list[NewsCache],
    ) -> list[list[NewsCandidate]]:
        selected_groups: list[list[NewsCandidate]] = []
        group_keys: list[tuple[set[str], datetime | None]] = []
        recent_titles: list[tuple[set[str], bool, datetime | None]] = [
            # Option B: PG content is always NULL, but existing rows still mean
            # "this topic already produced a valid body and was persisted".
            (self.normalize_title(row.title), True, row.published_at)
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

        def find_matching_group(
            tokens: set[str], published_at: datetime | None
        ) -> int | None:
            for idx, (group_tokens, group_published_at) in enumerate(group_keys):
                if self._titles_similar(tokens, group_tokens, published_at, group_published_at):
                    return idx
            return None

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
            tokens = candidate.title_tokens
            published_at = candidate.item.published_at

            if len(tokens) < 5:
                if len(selected_groups) >= self.BODY_CRAWL_LIMIT:
                    continue
                selected_groups.append([candidate])
                group_keys.append((tokens, published_at))
                continue

            if group_has_content(tokens, published_at):
                continue

            matched = find_matching_group(tokens, published_at)
            if matched is None:
                if len(selected_groups) >= self.BODY_CRAWL_LIMIT:
                    continue
                selected_groups.append([candidate])
                group_keys.append((tokens, published_at))
            else:
                if len(selected_groups[matched]) < self.BODY_ATTEMPTS_PER_GROUP:
                    selected_groups[matched].append(candidate)

        return selected_groups

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
            content=None,
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
            scraped = self.article_scraper.scrape(candidate.normalized_url)
        except Exception:
            result.body_failed_count += 1
            logger.exception("article scrape failed for %s", candidate.normalized_url)
            return None
        if scraped is None or not (scraped.content and scraped.content.strip()):
            result.body_failed_count += 1
            logger.info(
                "article scrape returned empty content for %s",
                candidate.normalized_url,
            )
            return scraped
        return scraped

    def _passes_prefilter(self, ticker: TickerMetadata, candidate: NewsCandidate) -> bool:
        item = candidate.item
        published_at = item.published_at
        if published_at and published_at < datetime.now(UTC) - timedelta(days=self.MAX_ARTICLE_AGE_DAYS):
            return False

        normalized_title = self._normalize_text(item.title)
        if len(normalized_title) < self.MIN_TITLE_LENGTH:
            return False
        if self._contains_title_exclude_marker(item.title):
            return False

        metadata_text = self._metadata_text(candidate)
        if self._contains_block_marker(metadata_text):
            return False

        if self._is_korean_market(ticker) and not (
            self._contains_hangul(metadata_text)
            or self._contains_symbol_reference(metadata_text, ticker.symbol)
        ):
            return False

        return True

    def _passes_storage_filters(
        self,
        ticker: TickerMetadata,
        candidate: NewsCandidate,
        scraped: ScrapedArticle | None,
    ) -> bool:
        if scraped is None or not scraped.content or not scraped.content.strip():
            return False

        title_text = self._normalize_text(candidate.item.title)
        metadata_text = self._metadata_text(candidate)
        body_text = self._normalize_text(scraped.content)

        if not self._matches_ticker_reference(title_text, metadata_text, body_text, ticker):
            return False

        if len(body_text) < self.MIN_CONTENT_LENGTH:
            return False

        return True

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
    def _normalize_text(value: str | None) -> str:
        return WHITESPACE_RE.sub(" ", (value or "")).strip()

    def _metadata_text(self, candidate: NewsCandidate) -> str:
        return self._normalize_text(f"{candidate.item.title} {candidate.item.description}")

    def _has_recent_similar_topic(self, candidate: NewsCandidate, recent_rows: list[NewsCache]) -> bool:
        tokens = candidate.title_tokens
        published_at = candidate.item.published_at
        for row in recent_rows:
            if not row.title:
                continue
            existing_tokens = self.normalize_title(row.title)
            existing_published_at = row.published_at
            if self._titles_similar(tokens, existing_tokens, published_at, existing_published_at):
                return True
        return False

    @staticmethod
    def _contains_hangul(value: str) -> bool:
        return HANGUL_RE.search(value) is not None

    @staticmethod
    def _contains_symbol_reference(value: str, symbol: str) -> bool:
        return bool(symbol and symbol in value)

    @staticmethod
    def _strip_whitespace(value: str) -> str:
        return WHITESPACE_RE.sub("", value)

    @classmethod
    def _contains_exact_name_reference(cls, value: str, name_kr: str | None) -> bool:
        if not name_kr:
            return False
        return cls._strip_whitespace(name_kr) in cls._strip_whitespace(value)

    @classmethod
    def _count_exact_name_reference(cls, value: str, name_kr: str | None) -> int:
        if not value or not name_kr:
            return 0
        return cls._strip_whitespace(value).count(cls._strip_whitespace(name_kr))

    @classmethod
    def _matches_ticker_reference(
        cls,
        title_text: str,
        metadata_text: str,
        body_text: str,
        ticker: TickerMetadata,
    ) -> bool:
        if cls._contains_exact_name_reference(title_text, ticker.name_kr):
            return True
        if cls._contains_symbol_reference(title_text, ticker.symbol):
            return True

        combined_text = f"{metadata_text} {body_text}".strip()
        if cls._contains_symbol_reference(combined_text, ticker.symbol):
            return True

        body_match_count = cls._count_exact_name_reference(body_text, ticker.name_kr)
        if body_match_count >= 2:
            return True

        return False

    @staticmethod
    def _contains_block_marker(value: str) -> bool:
        lowered = f" {value.lower()} "
        return any(marker in lowered for marker in AD_MARKERS + MIRROR_MARKERS)

    @staticmethod
    def _contains_title_exclude_marker(value: str) -> bool:
        compact = re.sub(r"\s+", "", value).lower()
        return any(re.sub(r"\s+", "", marker).lower() in compact for marker in TITLE_EXCLUDE_MARKERS)

    @staticmethod
    def _is_korean_market(ticker: TickerMetadata) -> bool:
        market = getattr(ticker.market, "value", str(ticker.market))
        return market in {"KOSPI", "KOSDAQ"}

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

    def _record_daily_api_call(self) -> None:
        if self.redis_client is None:
            return
        key = self._daily_api_count_key(datetime.now(UTC))
        try:
            count = self.redis_client.incr(key)
            if count == 1:
                self.redis_client.expire(key, self.DAILY_API_COUNT_TTL_SECONDS)
        except Exception:
            logger.exception("failed to record daily naver api usage")

    @staticmethod
    def _lock_key(symbol: str) -> str:
        return make_key("news-sync", "lock", symbol)

    @staticmethod
    def _last_run_key(symbol: str) -> str:
        return make_key("news-sync", "last-run", symbol)

    @staticmethod
    def _daily_api_count_key(now: datetime) -> str:
        return make_key("naver-api-count", now.astimezone(KST).date().isoformat())
