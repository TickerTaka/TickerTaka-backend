from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from app.core.db import SessionLocal
from app.domain.news_ingestion import NewsIngestionService
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
    trimmed_rows: int
    trimmed_content: int
    final_rows: int
    final_content_rows: int


class FakeRedis:
    def __init__(self, initial: dict[str, str] | None = None) -> None:
        self.store: dict[str, str] = dict(initial or {})

    def set(self, key: str, value: str | float, nx: bool = False, ex: int | None = None) -> bool:
        if nx and key in self.store:
            return False
        self.store[key] = str(value)
        return True

    def get(self, key: str) -> str | None:
        return self.store.get(key)

    def delete(self, key: str) -> int:
        return int(self.store.pop(key, None) is not None)


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
        )
        try:
            raw = service.sync_news_for_ticker(ticker.symbol, mode="initial", force=True)
            final_rows, final_content_rows = count_rows(session, ticker.symbol)
            result = to_result("initial_insert", raw, final_rows, final_content_rows)
            expect(result.inserted == 15, "initial_insert: inserted should be 15")
            expect(result.body_saved == 5, "initial_insert: body_saved should be 5")
            expect(result.grouped == 5, "initial_insert: grouped should be 5")
            expect(result.final_rows == 15, "initial_insert: final_rows should be 15")
            expect(result.final_content_rows == 5, "initial_insert: final_content_rows should be 5")
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
            )
            raw = service.sync_news_for_ticker(ticker.symbol, mode="refresh", force=True, limit=3)
            final_rows, final_content_rows = count_rows(session, ticker.symbol)
            result = to_result("duplicate_update", raw, final_rows, final_content_rows)
            expect(result.inserted == 1, "duplicate_update: inserted should be 1")
            expect(result.updated == 1, "duplicate_update: updated should be 1")
            expect(result.skipped == 1, "duplicate_update: skipped should be 1")
            expect(result.final_rows == 3, "duplicate_update: final_rows should be 3")
            expect(result.final_content_rows == 3, "duplicate_update: final_content_rows should be 3")
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
        )
        service.MAX_CACHE_ROWS = 4
        service.MAX_CONTENT_ROWS = 2
        service.BODY_CRAWL_LIMIT = 6
        try:
            raw = service.sync_news_for_ticker(ticker.symbol, mode="initial", force=True, limit=6)
            final_rows, final_content_rows = count_rows(session, ticker.symbol)
            result = to_result("trim_rows_and_content", raw, final_rows, final_content_rows)
            expect(result.trimmed_rows == 2, "trim_rows_and_content: trimmed_rows should be 2")
            expect(result.trimmed_content == 2, "trim_rows_and_content: trimmed_content should be 2")
            expect(result.final_rows == 4, "trim_rows_and_content: final_rows should be 4")
            expect(result.final_content_rows == 2, "trim_rows_and_content: final_content_rows should be 2")
            return result
        finally:
            session.rollback()


def run_partial_insert_on_scrape_failure_scenario(ticker: TestTicker) -> ScenarioResult:
    base = datetime.now(UTC).replace(microsecond=0)
    url = f"https://news.example.com/{ticker.symbol}/partial/failure"
    item = build_item(ticker, url, "partial insert after scraper failure", base)

    with SessionLocal() as session:
        service = NewsIngestionService(
            session,
            news_client=FakeNaverNewsClient([item]),
            article_scraper=FakeArticleScraper({url: RuntimeError("scrape failed")}),
            redis_client=FakeRedis(),
        )
        try:
            raw = service.sync_news_for_ticker(ticker.symbol, mode="initial", force=True, limit=1)
            row = session.scalar(select(NewsCache).where(NewsCache.symbol == ticker.symbol, NewsCache.source_url == url))
            final_rows, final_content_rows = count_rows(session, ticker.symbol)
            result = to_result("partial_insert_on_scrape_failure", raw, final_rows, final_content_rows)
            expect(result.inserted == 1, "partial_insert_on_scrape_failure: inserted should be 1")
            expect(result.body_failed == 1, "partial_insert_on_scrape_failure: body_failed should be 1")
            expect(result.final_content_rows == 0, "partial_insert_on_scrape_failure: content rows should be 0")
            expect(row is not None and row.content is None, "partial_insert_on_scrape_failure: row should exist without content")
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
        "short_body": f"https://news.example.com/{ticker.symbol}/filter/short-body",
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
    short_body_item = build_item(ticker, urls["short_body"], "factory expansion update cost demand", base - timedelta(minutes=6))

    items = [
        good_item,
        symbol_item,
        stale_item,
        short_title_item,
        unrelated_item,
        ad_item,
        mirror_item,
        short_body_item,
    ]
    articles = {
        urls["good"]: build_scraped(urls["good"], good_item.title, good_item.published_at or base),
        urls["symbol"]: build_scraped(urls["symbol"], symbol_item.title, symbol_item.published_at or base),
        urls["short_body"]: build_short_scraped(urls["short_body"], short_body_item.title, short_body_item.published_at or base),
    }

    with SessionLocal() as session:
        service = NewsIngestionService(
            session,
            news_client=FakeNaverNewsClient(items),
            article_scraper=FakeArticleScraper(articles),
            redis_client=FakeRedis(),
        )
        service.BODY_CRAWL_LIMIT = 8
        try:
            raw = service.sync_news_for_ticker(ticker.symbol, mode="initial", force=True, limit=8)
            rows = list(session.scalars(select(NewsCache).where(NewsCache.symbol == ticker.symbol).order_by(NewsCache.source_url)))
            final_rows, final_content_rows = count_rows(session, ticker.symbol)
            result = to_result("filtering_policy", raw, final_rows, final_content_rows)
            saved_urls = {row.source_url for row in rows}
            expect(result.inserted == 2, "filtering_policy: inserted should be 2")
            expect(result.filtered == 6, "filtering_policy: filtered should be 6")
            expect(result.body_saved == 2, "filtering_policy: body_saved should be 2")
            expect(saved_urls == {urls["good"], urls["symbol"]}, "filtering_policy: only good and symbol urls should remain")
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
        run_partial_insert_on_scrape_failure_scenario(ticker),
        run_title_gap_scenario(ticker),
        run_cooldown_scenario(ticker),
        run_lock_skip_scenario(ticker),
        run_ttl_accuracy_scenario(ticker),
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
                    f"trimmed_rows={scenario.trimmed_rows}",
                    f"trimmed_content={scenario.trimmed_content}",
                ]
            )
        )
        print(f"final_rows={scenario.final_rows} final_content_rows={scenario.final_content_rows}")
        print()


if __name__ == "__main__":
    main()
