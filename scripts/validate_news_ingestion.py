from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from app.core.db import SessionLocal
from app.domain.news_ingestion import NewsIngestionService
from app.external.embedding import DeterministicEmbeddingClient
from app.external.article_scraper import ScrapedArticle
from app.external.naver_news import NaverNewsItem
from app.models import NewsCache, TickerMetadata


@dataclass(slots=True)
class TestTicker:
    symbol: str
    name_kr: str
    market: str


@dataclass(slots=True)
class ScenarioResult:
    name: str
    fetched: int
    inserted: int
    updated: int
    skipped: int
    filtered: int
    body_failed: int
    body_saved: int
    grouped: int
    body_quota_saved: int
    body_attempts: int
    trimmed_rows: int
    trimmed_content: int
    final_rows: int
    final_content_rows: int


class FakeRedis:
    def __init__(self, initial: dict[str, str] | None = None) -> None:
        self.store: dict[str, str] = dict(initial or {})
        self.expiry: dict[str, int] = {}

    def set(self, key: str, value: str | float, nx: bool = False, ex: int | None = None) -> bool:
        if nx and key in self.store:
            return False
        self.store[key] = str(value)
        if ex is not None:
            self.expiry[key] = ex
        return True

    def get(self, key: str) -> str | None:
        return self.store.get(key)

    def delete(self, key: str) -> int:
        self.expiry.pop(key, None)
        return int(self.store.pop(key, None) is not None)

    def incr(self, key: str) -> int:
        value = int(self.store.get(key, "0")) + 1
        self.store[key] = str(value)
        return value

    def expire(self, key: str, seconds: int) -> bool:
        if key not in self.store:
            return False
        self.expiry[key] = seconds
        return True


class FakeNaverNewsClient:
    def __init__(self, items: list[NaverNewsItem]) -> None:
        self.items = items

    def search_news(
        self,
        query: str,
        display: int,
        *,
        start: int = 1,
        sort: str = "date",
        timeout: float = 10.0,
    ) -> list[NaverNewsItem]:
        return self.items[:display]


class FakeArticleScraper:
    def __init__(self, payloads: dict[str, ScrapedArticle | Exception]) -> None:
        self.payloads = payloads

    def scrape(self, url: str, timeout: tuple[float, float] = (3.0, 7.0)) -> ScrapedArticle:
        payload = self.payloads.get(url)
        if payload is None:
            raise RuntimeError(f"missing fake article for {url}")
        if isinstance(payload, Exception):
            raise payload
        return payload


class NullChromaClient:
    def upsert(self, name, documents, *, embedding_client) -> None:
        return None

    def delete(self, name, *, ids=None, where=None) -> None:
        return None


TITLE_VARIANTS = [
    "alpha bravo charlie delta echo",
    "foxtrot golf hotel india juliet",
    "kilo lima mike november oscar",
    "papa quebec romeo sierra tango",
    "uniform victor whiskey xray yankee",
    "zulu amber bronze cobalt diamond",
    "ember frost granite harbor ivory",
    "jade karma linen marble nectar",
    "onyx prism quartz ripple solar",
    "topaz umbra velvet willow xenon",
    "yellow zephyr atlas beacon comet",
    "drift ember falcon glacier halo",
    "ionic jungle keystone lantern mosaic",
    "nebula orbit pebble radius summit",
    "thunder uplink vertex wonder zenith",
]


def build_item(
    ticker: TestTicker,
    url: str,
    title_suffix: str,
    published_at: datetime,
    *,
    description: str | None = None,
) -> NaverNewsItem:
    title = f"{ticker.name_kr} {title_suffix}"
    return NaverNewsItem(
        title=title,
        description=description or f"{title} description {ticker.symbol}",
        link=url,
        original_link=url,
        published_at=published_at,
        source_name="example.com",
    )


def build_symbol_item(
    ticker: TestTicker,
    url: str,
    title_suffix: str,
    published_at: datetime,
    *,
    description: str | None = None,
) -> NaverNewsItem:
    title = f"{ticker.symbol} {title_suffix}"
    return NaverNewsItem(
        title=title,
        description=description or f"{title} description",
        link=url,
        original_link=url,
        published_at=published_at,
        source_name="example.com",
    )


def build_scraped(url: str, title: str, published_at: datetime, *, min_length: int = 240) -> ScrapedArticle:
    repeated = "내용 " * max(1, min_length // 3)
    return ScrapedArticle(
        content=f"{title} 본문 {repeated}".strip(),
        summary=f"{title} summary",
        source_name="example.com",
        canonical_url=url,
        published_at=published_at,
    )


def build_short_scraped(url: str, title: str, published_at: datetime) -> ScrapedArticle:
    return ScrapedArticle(
        content=f"{title} 짧은 본문",
        summary=f"{title} short",
        source_name="example.com",
        canonical_url=url,
        published_at=published_at,
    )


def expect(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def get_test_ticker(session) -> TestTicker:
    stmt = (
        select(TickerMetadata)
        .outerjoin(NewsCache, NewsCache.symbol == TickerMetadata.symbol)
        .group_by(TickerMetadata.symbol)
        .having(func.count(NewsCache.id) == 0)
        .order_by(TickerMetadata.symbol)
        .limit(1)
    )
    ticker = session.scalar(stmt)
    if ticker is None:
        ticker = session.scalar(select(TickerMetadata).order_by(TickerMetadata.symbol).limit(1))
    if ticker is None:
        raise RuntimeError("ticker_metadata is empty")
    return TestTicker(
        symbol=ticker.symbol,
        name_kr=ticker.name_kr,
        market=getattr(ticker.market, "value", str(ticker.market)),
    )


def count_rows(session, symbol: str) -> tuple[int, int]:
    total_rows = session.scalar(select(func.count()).select_from(NewsCache).where(NewsCache.symbol == symbol)) or 0
    content_rows = session.scalar(
        select(func.count()).select_from(NewsCache).where(
            NewsCache.symbol == symbol,
            NewsCache.content.is_not(None),
        )
    ) or 0
    return int(total_rows), int(content_rows)


def to_result(name: str, raw_result, final_rows: int, final_content_rows: int) -> ScenarioResult:
    return ScenarioResult(
        name=name,
        fetched=raw_result.fetched_count,
        inserted=raw_result.inserted_count,
        updated=raw_result.updated_count,
        skipped=raw_result.skipped_count,
        filtered=raw_result.filtered_count,
        body_failed=raw_result.body_failed_count,
        body_saved=raw_result.body_saved_count,
        grouped=raw_result.grouped_count,
        body_quota_saved=raw_result.body_quota_saved_count,
        body_attempts=raw_result.body_attempts_count,
        trimmed_rows=raw_result.trimmed_rows_count,
        trimmed_content=raw_result.trimmed_content_count,
        final_rows=final_rows,
        final_content_rows=final_content_rows,
    )


def run_initial_insert_scenario(ticker: TestTicker) -> ScenarioResult:
    base = datetime.now(UTC).replace(microsecond=0)
    items: list[NaverNewsItem] = []
    articles: dict[str, ScrapedArticle | Exception] = {}

    for index in range(15):
        url = f"https://news.example.com/{ticker.symbol}/initial/{index}"
        item = build_symbol_item(ticker, url, TITLE_VARIANTS[index], base - timedelta(minutes=index))
        items.append(item)
        if index < 5:
            articles[url] = build_scraped(url, item.title, item.published_at or base)

    with SessionLocal() as session:
        service = NewsIngestionService(
            session,
            news_client=FakeNaverNewsClient(items),
            article_scraper=FakeArticleScraper(articles),
            redis_client=FakeRedis(),
            chroma_client=NullChromaClient(),
            embedding_client=DeterministicEmbeddingClient(),
        )
        service.BODY_CRAWL_LIMIT = 5
        try:
            raw = service.sync_news_for_ticker(ticker.symbol, mode="initial", force=True)
            final_rows, final_content_rows = count_rows(session, ticker.symbol)
            result = to_result("initial_insert", raw, final_rows, final_content_rows)
            expect(result.inserted == 5, "initial_insert: inserted should be 5")
            expect(result.body_saved == 5, "initial_insert: body_saved should be 5")
            expect(result.grouped == 5, "initial_insert: grouped should be 5")
            expect(result.final_rows == 5, "initial_insert: final_rows should be 5")
            expect(result.final_content_rows == 0, "initial_insert: final_content_rows should be 0")
            return result
        finally:
            session.rollback()


def run_duplicate_update_scenario(ticker: TestTicker) -> ScenarioResult:
    base = datetime.now(UTC).replace(microsecond=0)
    existing_null_url = f"https://news.example.com/{ticker.symbol}/refresh/null"
    existing_content_url = f"https://news.example.com/{ticker.symbol}/refresh/filled"
    new_url = f"https://news.example.com/{ticker.symbol}/refresh/new"

    with SessionLocal() as session:
        try:
            session.add_all(
                [
                    NewsCache(
                        symbol=ticker.symbol,
                        title=f"{ticker.name_kr} null content article distinct topic",
                        content=None,
                        summary="seed summary",
                        source_name="example.com",
                        source_url=existing_null_url,
                        published_at=base - timedelta(minutes=20),
                        ttl_until=base + timedelta(days=30),
                    ),
                    NewsCache(
                        symbol=ticker.symbol,
                        title=f"{ticker.name_kr} already filled article separate topic",
                        content="existing content",
                        summary="existing summary",
                        source_name="example.com",
                        source_url=existing_content_url,
                        published_at=base - timedelta(minutes=10),
                        ttl_until=base + timedelta(days=30),
                    ),
                ]
            )
            session.flush()

            items = [
                build_item(ticker, existing_null_url, "null content article distinct topic", base - timedelta(minutes=20)),
                build_item(ticker, existing_content_url, "already filled article separate topic", base - timedelta(minutes=10)),
                build_item(ticker, new_url, "brand new refresh article another topic", base),
            ]
            articles = {
                existing_null_url: build_scraped(existing_null_url, items[0].title, items[0].published_at or base),
                new_url: build_scraped(new_url, items[2].title, items[2].published_at or base),
            }

            service = NewsIngestionService(
                session,
                news_client=FakeNaverNewsClient(items),
                article_scraper=FakeArticleScraper(articles),
                redis_client=FakeRedis(),
                chroma_client=NullChromaClient(),
                embedding_client=DeterministicEmbeddingClient(),
            )
            raw = service.sync_news_for_ticker(ticker.symbol, mode="refresh", force=True, limit=3)
            final_rows, final_content_rows = count_rows(session, ticker.symbol)
            result = to_result("duplicate_update", raw, final_rows, final_content_rows)
            expect(result.inserted == 1, "duplicate_update: inserted should be 1")
            expect(result.updated == 0, "duplicate_update: updated should be 0")
            expect(result.skipped == 0, "duplicate_update: skipped should be 0")
            expect(result.filtered == 2, "duplicate_update: filtered should be 2")
            expect(result.body_saved == 1, "duplicate_update: body_saved should be 1")
            expect(result.final_rows == 3, "duplicate_update: final_rows should be 3")
            expect(result.final_content_rows == 1, "duplicate_update: final_content_rows should be 1")
            return result
        finally:
            session.rollback()


def run_trim_scenario(ticker: TestTicker) -> ScenarioResult:
    base = datetime.now(UTC).replace(microsecond=0)
    items: list[NaverNewsItem] = []
    articles: dict[str, ScrapedArticle | Exception] = {}

    for index in range(6):
        url = f"https://news.example.com/{ticker.symbol}/trim/{index}"
        item = build_symbol_item(ticker, url, TITLE_VARIANTS[index], base - timedelta(minutes=index))
        items.append(item)
        articles[url] = build_scraped(url, item.title, item.published_at or base)

    with SessionLocal() as session:
        service = NewsIngestionService(
            session,
            news_client=FakeNaverNewsClient(items),
            article_scraper=FakeArticleScraper(articles),
            redis_client=FakeRedis(),
            chroma_client=NullChromaClient(),
            embedding_client=DeterministicEmbeddingClient(),
        )
        service.MAX_CACHE_ROWS = 4
        service.BODY_CRAWL_LIMIT = 6
        try:
            raw = service.sync_news_for_ticker(ticker.symbol, mode="initial", force=True, limit=6)
            final_rows, final_content_rows = count_rows(session, ticker.symbol)
            result = to_result("trim_rows_and_content", raw, final_rows, final_content_rows)
            expect(result.trimmed_rows == 2, "trim_rows_and_content: trimmed_rows should be 2")
            expect(result.trimmed_content == 0, "trim_rows_and_content: trimmed_content should be 0")
            expect(result.final_rows == 4, "trim_rows_and_content: final_rows should be 4")
            expect(result.final_content_rows == 0, "trim_rows_and_content: final_content_rows should be 0")
            return result
        finally:
            session.rollback()


def run_drop_on_scrape_failure_scenario(ticker: TestTicker) -> ScenarioResult:
    base = datetime.now(UTC).replace(microsecond=0)
    url = f"https://news.example.com/{ticker.symbol}/partial/failure"
    item = build_item(ticker, url, "drop row after scraper failure", base)

    with SessionLocal() as session:
        service = NewsIngestionService(
            session,
            news_client=FakeNaverNewsClient([item]),
            article_scraper=FakeArticleScraper({url: RuntimeError("scrape failed")}),
            redis_client=FakeRedis(),
            chroma_client=NullChromaClient(),
            embedding_client=DeterministicEmbeddingClient(),
        )
        try:
            raw = service.sync_news_for_ticker(ticker.symbol, mode="initial", force=True, limit=1)
            row = session.scalar(select(NewsCache).where(NewsCache.symbol == ticker.symbol, NewsCache.source_url == url))
            final_rows, final_content_rows = count_rows(session, ticker.symbol)
            result = to_result("drop_on_scrape_failure", raw, final_rows, final_content_rows)
            expect(result.inserted == 0, "drop_on_scrape_failure: inserted should be 0")
            expect(result.body_failed == 1, "drop_on_scrape_failure: body_failed should be 1")
            expect(result.final_content_rows == 0, "drop_on_scrape_failure: content rows should be 0")
            expect(row is None, "drop_on_scrape_failure: row should not be inserted")
            return result
        finally:
            session.rollback()


def run_title_gap_scenario(ticker: TestTicker) -> ScenarioResult:
    base = datetime.now(UTC).replace(microsecond=0)
    first_url = f"https://news.example.com/{ticker.symbol}/gap/first"
    second_url = f"https://news.example.com/{ticker.symbol}/gap/second"
    shared_suffix = "alpha bravo charlie delta echo"
    first_item = build_item(ticker, first_url, shared_suffix, base)
    second_item = build_item(ticker, second_url, shared_suffix, base - timedelta(hours=7))

    with SessionLocal() as session:
        service = NewsIngestionService(
            session,
            news_client=FakeNaverNewsClient([first_item, second_item]),
            article_scraper=FakeArticleScraper(
                {
                    first_url: build_scraped(first_url, first_item.title, first_item.published_at or base),
                    second_url: build_scraped(second_url, second_item.title, second_item.published_at or base),
                }
            ),
            redis_client=FakeRedis(),
            chroma_client=NullChromaClient(),
            embedding_client=DeterministicEmbeddingClient(),
        )
        service.BODY_CRAWL_LIMIT = 2
        try:
            raw = service.sync_news_for_ticker(ticker.symbol, mode="initial", force=True, limit=2)
            final_rows, final_content_rows = count_rows(session, ticker.symbol)
            result = to_result("title_similarity_6h_gap", raw, final_rows, final_content_rows)
            expect(result.grouped == 2, "title_similarity_6h_gap: grouped should be 2")
            expect(result.body_saved == 2, "title_similarity_6h_gap: body_saved should be 2")
            return result
        finally:
            session.rollback()


def run_cooldown_scenario(ticker: TestTicker) -> ScenarioResult:
    base = datetime.now(UTC).replace(microsecond=0)
    item = build_item(ticker, f"https://news.example.com/{ticker.symbol}/cooldown", "cooldown check headline", base)
    redis_client = FakeRedis(
        {
            NewsIngestionService._last_run_key(ticker.symbol): str(datetime.now(UTC).timestamp()),
        }
    )

    with SessionLocal() as session:
        service = NewsIngestionService(
            session,
            news_client=FakeNaverNewsClient([item]),
            article_scraper=FakeArticleScraper({}),
            redis_client=redis_client,
            chroma_client=NullChromaClient(),
            embedding_client=DeterministicEmbeddingClient(),
        )
        try:
            raw = service.sync_news_for_ticker(ticker.symbol, mode="refresh", force=False, limit=1)
            final_rows, final_content_rows = count_rows(session, ticker.symbol)
            result = to_result("cooldown_skip", raw, final_rows, final_content_rows)
            expect(result.skipped == 1, "cooldown_skip: skipped should be 1")
            expect(result.fetched == 0, "cooldown_skip: fetched should be 0")
            expect(result.inserted == 0, "cooldown_skip: inserted should be 0")
            return result
        finally:
            session.rollback()


def run_lock_skip_scenario(ticker: TestTicker) -> ScenarioResult:
    base = datetime.now(UTC).replace(microsecond=0)
    item = build_item(ticker, f"https://news.example.com/{ticker.symbol}/lock", "lock check headline", base)
    redis_client = FakeRedis(
        {
            NewsIngestionService._lock_key(ticker.symbol): "occupied",
        }
    )

    with SessionLocal() as session:
        service = NewsIngestionService(
            session,
            news_client=FakeNaverNewsClient([item]),
            article_scraper=FakeArticleScraper({}),
            redis_client=redis_client,
            chroma_client=NullChromaClient(),
            embedding_client=DeterministicEmbeddingClient(),
        )
        try:
            raw = service.sync_news_for_ticker(ticker.symbol, mode="refresh", force=True, limit=1)
            final_rows, final_content_rows = count_rows(session, ticker.symbol)
            result = to_result("lock_skip", raw, final_rows, final_content_rows)
            expect(result.skipped == 1, "lock_skip: skipped should be 1")
            expect(result.fetched == 0, "lock_skip: fetched should be 0")
            expect(result.inserted == 0, "lock_skip: inserted should be 0")
            return result
        finally:
            session.rollback()


def run_ttl_accuracy_scenario(ticker: TestTicker) -> ScenarioResult:
    base = datetime.now(UTC).replace(microsecond=0)
    url = f"https://news.example.com/{ticker.symbol}/ttl"
    item = build_item(ticker, url, "ttl anchor headline", base)

    with SessionLocal() as session:
        service = NewsIngestionService(
            session,
            news_client=FakeNaverNewsClient([item]),
            article_scraper=FakeArticleScraper({url: build_scraped(url, item.title, base)}),
            redis_client=FakeRedis(),
            chroma_client=NullChromaClient(),
            embedding_client=DeterministicEmbeddingClient(),
        )
        try:
            raw = service.sync_news_for_ticker(ticker.symbol, mode="initial", force=True, limit=1)
            row = session.scalar(select(NewsCache).where(NewsCache.symbol == ticker.symbol, NewsCache.source_url == url))
            final_rows, final_content_rows = count_rows(session, ticker.symbol)
            result = to_result("ttl_accuracy", raw, final_rows, final_content_rows)
            expect(row is not None, "ttl_accuracy: row should exist")
            expect(row.ttl_until == base + timedelta(days=30), "ttl_accuracy: ttl_until should equal published_at + 30 days")
            return result
        finally:
            session.rollback()


def run_daily_api_counter_scenario(ticker: TestTicker) -> ScenarioResult:
    base = datetime.now(UTC).replace(microsecond=0)
    url = f"https://news.example.com/{ticker.symbol}/daily-api-counter"
    item = build_item(ticker, url, "daily api usage counter headline", base)
    redis_client = FakeRedis()

    with SessionLocal() as session:
        service = NewsIngestionService(
            session,
            news_client=FakeNaverNewsClient([item]),
            article_scraper=FakeArticleScraper({url: build_scraped(url, item.title, base)}),
            redis_client=redis_client,
            chroma_client=NullChromaClient(),
            embedding_client=DeterministicEmbeddingClient(),
        )
        try:
            raw = service.sync_news_for_ticker(ticker.symbol, mode="initial", force=True, limit=1)
            key = service._daily_api_count_key(base)
            final_rows, final_content_rows = count_rows(session, ticker.symbol)
            result = to_result("daily_api_counter", raw, final_rows, final_content_rows)
            expect(redis_client.get(key) == "1", "daily_api_counter: daily counter should be incremented once")
            expect(
                redis_client.expiry.get(key) == service.DAILY_API_COUNT_TTL_SECONDS,
                "daily_api_counter: daily counter ttl mismatch",
            )
            return result
        finally:
            session.rollback()


def run_whitespace_variant_scenario() -> ScenarioResult:
    """공백 변형 본문/제목에서도 name_kr 매칭이 되는지 단위 검증."""

    class _MockTicker:
        name_kr = "SK하이닉스"
        symbol = "000660"

    ticker = _MockTicker()

    title_with_space = "SK 하이닉스 호실적 발표"
    expect(
        NewsIngestionService._matches_ticker_reference(title_with_space, "", "", ticker),
        "whitespace_variant: title 'SK 하이닉스' should match name_kr 'SK하이닉스'",
    )

    body_two_hits = "오늘 SK 하이닉스가 발표했다. 추가로 SK 하이닉스의 신제품도 공개됐다."
    expect(
        NewsIngestionService._matches_ticker_reference("", "", body_two_hits, ticker),
        "whitespace_variant: body with two 'SK 하이닉스' should match",
    )

    body_one_hit = "산업 전반 동향에서 SK 하이닉스가 언급되었다."
    expect(
        not NewsIngestionService._matches_ticker_reference("", "", body_one_hit, ticker),
        "whitespace_variant: body with single 'SK 하이닉스' should NOT match (need >= 2)",
    )

    body_unrelated = "삼성전자는 호실적을 기록했다. 삼성전자가 또 발표했다."
    expect(
        not NewsIngestionService._matches_ticker_reference("", "", body_unrelated, ticker),
        "whitespace_variant: unrelated body should NOT match",
    )

    return ScenarioResult(
        name="whitespace_variant_match",
        fetched=0,
        inserted=0,
        updated=0,
        skipped=0,
        filtered=0,
        body_failed=0,
        body_saved=0,
        grouped=0,
        body_quota_saved=0,
        body_attempts=0,
        trimmed_rows=0,
        trimmed_content=0,
        final_rows=0,
        final_content_rows=0,
    )


def run_body_fallback_on_storage_cut_scenario(ticker: TestTicker) -> ScenarioResult:
    """그룹 대표 본문이 추출은 성공했지만 storage filter 컷일 때 같은 그룹 다음 후보로 fallback."""
    base = datetime.now(UTC).replace(microsecond=0)
    common_suffix = "storage filter fallback 검증 매우 유사한 제목 토큰 다수 같은 사건"
    urls = [f"https://news.example.com/{ticker.symbol}/storage-fallback/{idx}" for idx in range(4)]

    def make_neutral_item(url: str, published_at: datetime) -> NaverNewsItem:
        title = f"메모리 산업 동향 호황 분기 호실적 발표 갱신 실적 {common_suffix}"
        return NaverNewsItem(
            title=title,
            description=f"메모리 산업 description {common_suffix}",
            link=url,
            original_link=url,
            published_at=published_at,
            source_name="example.com",
        )

    def make_partial_scraped(url: str, published_at: datetime, match_count: int) -> ScrapedArticle:
        body_intro = " ".join([f"{ticker.name_kr} 관련 산업 동향"] * match_count)
        body_rest = "추가 산업 일반 내용 메모리 시장 호황 분기 실적 발표 종목 관련 동향 " * 10
        return ScrapedArticle(
            content=f"{body_intro} {body_rest}".strip(),
            summary="storage fallback summary",
            source_name="example.com",
            canonical_url=url,
            published_at=published_at,
        )

    items = [make_neutral_item(urls[idx], base - timedelta(minutes=10 * idx)) for idx in range(4)]
    scrapers = {
        urls[0]: make_partial_scraped(urls[0], base, match_count=1),
        urls[1]: make_partial_scraped(urls[1], base - timedelta(minutes=10), match_count=2),
        urls[2]: make_partial_scraped(urls[2], base - timedelta(minutes=20), match_count=2),
    }

    with SessionLocal() as session:
        service = NewsIngestionService(
            session,
            news_client=FakeNaverNewsClient(items),
            article_scraper=FakeArticleScraper(scrapers),
            redis_client=FakeRedis(),
            chroma_client=NullChromaClient(),
            embedding_client=DeterministicEmbeddingClient(),
        )
        try:
            raw = service.sync_news_for_ticker(ticker.symbol, mode="initial", force=True, limit=4)
            final_rows, final_content_rows = count_rows(session, ticker.symbol)
            result = to_result("body_fallback_on_storage_cut", raw, final_rows, final_content_rows)
            expect(result.grouped == 1, "storage_fallback: should form 1 group")
            expect(
                result.body_failed == 0,
                "storage_fallback: extraction succeeded, body_failed should remain 0",
            )
            expect(
                result.body_attempts == 2,
                "storage_fallback: 1st cut on matching -> 2nd attempt success break",
            )
            expect(result.body_saved == 1, "storage_fallback: 2nd attempt body should be saved")
            return result
        finally:
            session.rollback()


def run_body_fallback_within_group_scenario(ticker: TestTicker) -> ScenarioResult:
    """그룹 대표 본문 실패 시 같은 그룹의 다음 후보로 즉시 fallback되는지 확인."""
    base = datetime.now(UTC).replace(microsecond=0)
    urls = [f"https://news.example.com/{ticker.symbol}/fallback/{idx}" for idx in range(4)]
    common_suffix = "그룹 fallback 검증 매우 유사한 제목 토큰 다수 포함 동일 사건"
    items = [
        build_item(ticker, url, common_suffix, base - timedelta(minutes=10 * idx))
        for idx, url in enumerate(urls)
    ]
    scrapers = {
        urls[0]: ScrapedArticle(
            content="",
            summary="",
            source_name="example.com",
            canonical_url=urls[0],
            published_at=base,
        ),
        urls[1]: build_scraped(urls[1], items[1].title, base),
        urls[2]: build_scraped(urls[2], items[2].title, base),
    }

    with SessionLocal() as session:
        service = NewsIngestionService(
            session,
            news_client=FakeNaverNewsClient(items),
            article_scraper=FakeArticleScraper(scrapers),
            redis_client=FakeRedis(),
            chroma_client=NullChromaClient(),
            embedding_client=DeterministicEmbeddingClient(),
        )
        try:
            raw = service.sync_news_for_ticker(ticker.symbol, mode="initial", force=True, limit=4)
            final_rows, final_content_rows = count_rows(session, ticker.symbol)
            result = to_result("body_fallback_within_group", raw, final_rows, final_content_rows)
            expect(result.grouped == 1, "fallback: should form 1 group")
            expect(result.body_failed == 1, "fallback: 1st empty body should bump body_failed")
            expect(
                result.body_attempts == 2,
                "fallback: 2 attempts expected (1st failed, 2nd success break)",
            )
            expect(result.body_saved == 1, "fallback: 2nd attempt body should be saved")
            expect(
                result.body_quota_saved == 3,
                "fallback: grouped 1 from 4 candidates -> quota saved 3",
            )
            expect(result.final_content_rows == 0, "fallback: PG content should remain NULL")
            return result
        finally:
            session.rollback()


def run_body_failed_empty_content_scenario(ticker: TestTicker) -> ScenarioResult:
    """스크래퍼가 raise 없이 빈 본문을 반환한 케이스가 body_failed로 잡히는지 확인."""
    base = datetime.now(UTC).replace(microsecond=0)
    url = f"https://news.example.com/{ticker.symbol}/empty-body"
    item = build_item(ticker, url, "empty body extraction case", base)
    empty_scraped = ScrapedArticle(
        content="",
        summary="",
        source_name="example.com",
        canonical_url=url,
        published_at=base,
    )

    with SessionLocal() as session:
        service = NewsIngestionService(
            session,
            news_client=FakeNaverNewsClient([item]),
            article_scraper=FakeArticleScraper({url: empty_scraped}),
            redis_client=FakeRedis(),
            chroma_client=NullChromaClient(),
            embedding_client=DeterministicEmbeddingClient(),
        )
        try:
            raw = service.sync_news_for_ticker(ticker.symbol, mode="initial", force=True, limit=1)
            final_rows, final_content_rows = count_rows(session, ticker.symbol)
            result = to_result("body_failed_empty_content", raw, final_rows, final_content_rows)
            expect(
                result.body_failed == 1,
                "body_failed_empty_content: scraper returning empty content should bump body_failed",
            )
            expect(result.inserted == 0, "body_failed_empty_content: empty body should not insert row")
            return result
        finally:
            session.rollback()


def run_metadata_name_match_scenario() -> ScenarioResult:
    """본문 없는 metadata-only name match는 더 이상 허용하지 않는지 단위 검증."""

    class _MockTicker:
        name_kr = "SK하이닉스"
        symbol = "000660"

    ticker = _MockTicker()

    title = "AI 메모리 전쟁 마이크론 호실적"
    metadata_with_name = (
        "AI 메모리 전쟁 마이크론 호실적 SK하이닉스 등 한국 메모리 업체 동향"
    )
    expect(
        not NewsIngestionService._matches_ticker_reference(title, metadata_with_name, "", ticker),
        "metadata_name_match: metadata-only name_kr should NOT match after P1 removal",
    )

    metadata_with_space_name = (
        "AI 메모리 전쟁 마이크론 호실적 SK 하이닉스 등 한국 메모리 업체 동향"
    )
    expect(
        not NewsIngestionService._matches_ticker_reference(title, metadata_with_space_name, "", ticker),
        "metadata_name_match: metadata-only spaced name should NOT match after P1 removal",
    )

    metadata_unrelated = "AI 메모리 전쟁 마이크론 호실적 삼성전자 동향"
    expect(
        not NewsIngestionService._matches_ticker_reference(title, metadata_unrelated, "", ticker),
        "metadata_name_match: unrelated metadata should NOT match",
    )

    return ScenarioResult(
        name="metadata_name_match",
        fetched=0,
        inserted=0,
        updated=0,
        skipped=0,
        filtered=0,
        body_failed=0,
        body_saved=0,
        grouped=0,
        body_quota_saved=0,
        body_attempts=0,
        trimmed_rows=0,
        trimmed_content=0,
        final_rows=0,
        final_content_rows=0,
    )


def run_body_quota_saved_scenario(ticker: TestTicker) -> ScenarioResult:
    base = datetime.now(UTC).replace(microsecond=0)
    urls = [f"https://news.example.com/{ticker.symbol}/quota/{idx}" for idx in range(4)]
    common_suffix = "분기 영업이익 14조원 돌파 사상 최고 갱신 실적"
    items = [
        build_item(ticker, url, common_suffix, base - timedelta(minutes=10 * idx))
        for idx, url in enumerate(urls)
    ]
    scrapers = {urls[0]: build_scraped(urls[0], items[0].title, base)}

    with SessionLocal() as session:
        service = NewsIngestionService(
            session,
            news_client=FakeNaverNewsClient(items),
            article_scraper=FakeArticleScraper(scrapers),
            redis_client=FakeRedis(),
            chroma_client=NullChromaClient(),
            embedding_client=DeterministicEmbeddingClient(),
        )
        try:
            raw = service.sync_news_for_ticker(ticker.symbol, mode="initial", force=True, limit=4)
            final_rows, final_content_rows = count_rows(session, ticker.symbol)
            result = to_result("body_quota_saved", raw, final_rows, final_content_rows)
            expect(result.grouped == 1, "body_quota_saved: grouped should be 1")
            expect(result.body_quota_saved == 3, "body_quota_saved: quota saved should be 3")
            expect(result.body_saved == 1, "body_quota_saved: body_saved should be 1")
            return result
        finally:
            session.rollback()


def run_filtering_policy_scenario(ticker: TestTicker) -> ScenarioResult:
    base = datetime.now(UTC).replace(microsecond=0)
    urls = {
        "good": f"https://news.example.com/{ticker.symbol}/filter/good",
        "symbol": f"https://news.example.com/{ticker.symbol}/filter/symbol",
        "stale": f"https://news.example.com/{ticker.symbol}/filter/stale",
        "short_title": f"https://news.example.com/{ticker.symbol}/filter/short-title",
        "unrelated": f"https://news.example.com/{ticker.symbol}/filter/unrelated",
        "ad": f"https://news.example.com/{ticker.symbol}/filter/ad",
        "mirror": f"https://news.example.com/{ticker.symbol}/filter/mirror",
        "graphic": f"https://news.example.com/{ticker.symbol}/filter/graphic",
        "short_body": f"https://news.example.com/{ticker.symbol}/filter/short-body",
        "weak_body_ref": f"https://news.example.com/{ticker.symbol}/filter/weak-body-ref",
        "strong_body_ref": f"https://news.example.com/{ticker.symbol}/filter/strong-body-ref",
    }

    good_item = build_item(ticker, urls["good"], "growth outlook demand supply margins", base)
    symbol_item = build_symbol_item(ticker, urls["symbol"], "capital markets earnings guidance", base - timedelta(minutes=1))
    stale_item = build_item(ticker, urls["stale"], "stale old report headline data points", base - timedelta(days=8))
    short_title_item = NaverNewsItem(
        title=ticker.name_kr,
        description=f"{ticker.name_kr} short title description {ticker.symbol}",
        link=urls["short_title"],
        original_link=urls["short_title"],
        published_at=base - timedelta(minutes=2),
        source_name="example.com",
    )
    unrelated_item = NaverNewsItem(
        title="macro policy outlook unrelated article",
        description="unrelated article without target ticker",
        link=urls["unrelated"],
        original_link=urls["unrelated"],
        published_at=base - timedelta(minutes=3),
        source_name="example.com",
    )
    ad_item = build_item(ticker, urls["ad"], "[광고] investment summit partner benefits", base - timedelta(minutes=4))
    mirror_item = build_item(ticker, urls["mirror"], "distribution update and 무단전재 notice", base - timedelta(minutes=5))
    graphic_item = NaverNewsItem(
        title=f"[그래픽] {ticker.name_kr} labor issue overview",
        description=f"{ticker.name_kr} graphic summary",
        link=urls["graphic"],
        original_link=urls["graphic"],
        published_at=base - timedelta(minutes=5),
        source_name="example.com",
    )
    short_body_item = build_item(ticker, urls["short_body"], "factory expansion update cost demand", base - timedelta(minutes=6))
    weak_body_ref_item = NaverNewsItem(
        title="semiconductor labor update market impact",
        description="industry issue description only",
        link=urls["weak_body_ref"],
        original_link=urls["weak_body_ref"],
        published_at=base - timedelta(minutes=7),
        source_name="example.com",
    )
    strong_body_ref_item = NaverNewsItem(
        title="semiconductor cycle outlook market view",
        description="반도체 업황 관련 산업 동향 설명",
        link=urls["strong_body_ref"],
        original_link=urls["strong_body_ref"],
        published_at=base - timedelta(minutes=8),
        source_name="example.com",
    )

    items = [
        good_item,
        symbol_item,
        stale_item,
        short_title_item,
        unrelated_item,
        ad_item,
        mirror_item,
        graphic_item,
        short_body_item,
        weak_body_ref_item,
        strong_body_ref_item,
    ]
    articles = {
        urls["good"]: build_scraped(urls["good"], good_item.title, good_item.published_at or base),
        urls["symbol"]: build_scraped(urls["symbol"], symbol_item.title, symbol_item.published_at or base),
        urls["short_body"]: build_short_scraped(urls["short_body"], short_body_item.title, short_body_item.published_at or base),
        urls["weak_body_ref"]: ScrapedArticle(
            content=f"시장 전체에 대한 언급과 {ticker.name_kr} 한 번 언급",
            summary="weak ref",
            source_name="example.com",
            canonical_url=urls["weak_body_ref"],
            published_at=weak_body_ref_item.published_at,
        ),
        urls["strong_body_ref"]: ScrapedArticle(
            content=(
                f"시장 설명과 함께 {ticker.name_kr} 이슈를 길게 설명한다. "
                f"추가 문단에서도 {ticker.name_kr} 관련 수요와 공급을 반복적으로 다룬다. "
                + ("세부 내용 " * 80)
            ),
            summary="strong ref",
            source_name="example.com",
            canonical_url=urls["strong_body_ref"],
            published_at=strong_body_ref_item.published_at,
        ),
    }

    with SessionLocal() as session:
        service = NewsIngestionService(
            session,
            news_client=FakeNaverNewsClient(items),
            article_scraper=FakeArticleScraper(articles),
            redis_client=FakeRedis(),
            chroma_client=NullChromaClient(),
            embedding_client=DeterministicEmbeddingClient(),
        )
        service.BODY_CRAWL_LIMIT = 8
        try:
            raw = service.sync_news_for_ticker(ticker.symbol, mode="initial", force=True, limit=11)
            rows = list(session.scalars(select(NewsCache).where(NewsCache.symbol == ticker.symbol).order_by(NewsCache.source_url)))
            final_rows, final_content_rows = count_rows(session, ticker.symbol)
            result = to_result("filtering_policy", raw, final_rows, final_content_rows)
            saved_urls = {row.source_url for row in rows}
            expect(result.inserted == 3, "filtering_policy: inserted should be 3")
            expect(result.filtered == 8, "filtering_policy: filtered should be 8")
            expect(result.body_saved == 3, "filtering_policy: body_saved should be 3")
            expect(
                saved_urls == {
                    urls["good"],
                    urls["symbol"],
                    urls["strong_body_ref"],
                },
                "filtering_policy: good/symbol/strong_body_ref should remain",
            )
            return result
        finally:
            session.rollback()


def main() -> None:
    with SessionLocal() as session:
        ticker = get_test_ticker(session)

    scenarios = [
        run_initial_insert_scenario(ticker),
        run_duplicate_update_scenario(ticker),
        run_trim_scenario(ticker),
        run_drop_on_scrape_failure_scenario(ticker),
        run_title_gap_scenario(ticker),
        run_cooldown_scenario(ticker),
        run_lock_skip_scenario(ticker),
        run_ttl_accuracy_scenario(ticker),
        run_daily_api_counter_scenario(ticker),
        run_body_failed_empty_content_scenario(ticker),
        run_metadata_name_match_scenario(),
        run_body_quota_saved_scenario(ticker),
        run_body_fallback_within_group_scenario(ticker),
        run_body_fallback_on_storage_cut_scenario(ticker),
        run_whitespace_variant_scenario(),
        run_filtering_policy_scenario(ticker),
    ]

    for scenario in scenarios:
        print(f"[{scenario.name}]")
        print(
            " ".join(
                [
                    f"fetched={scenario.fetched}",
                    f"inserted={scenario.inserted}",
                    f"updated={scenario.updated}",
                    f"skipped={scenario.skipped}",
                    f"filtered={scenario.filtered}",
                ]
            )
        )
        print(
            " ".join(
                [
                    f"body_failed={scenario.body_failed}",
                    f"body_saved={scenario.body_saved}",
                    f"grouped={scenario.grouped}",
                    f"body_quota_saved={scenario.body_quota_saved}",
                    f"body_attempts={scenario.body_attempts}",
                    f"trimmed_rows={scenario.trimmed_rows}",
                    f"trimmed_content={scenario.trimmed_content}",
                ]
            )
        )
        print(f"final_rows={scenario.final_rows} final_content_rows={scenario.final_content_rows}")
        print()


if __name__ == "__main__":
    main()
