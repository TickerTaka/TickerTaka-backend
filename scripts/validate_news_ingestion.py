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
class ScenarioResult:
    name: str
    inserted: int
    updated: int
    skipped: int
    body_saved: int
    grouped: int
    trimmed_rows: int
    trimmed_content: int
    final_rows: int
    final_content_rows: int


class FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}

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
    def __init__(self, payloads: dict[str, ScrapedArticle]) -> None:
        self.payloads = payloads

    def scrape(self, url: str, timeout: tuple[float, float] = (3.0, 7.0)) -> ScrapedArticle:
        article = self.payloads.get(url)
        if article is None:
            raise RuntimeError(f"missing fake article for {url}")
        return article


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


def build_item(url: str, title: str, published_at: datetime, *, source_name: str = "example.com") -> NaverNewsItem:
    return NaverNewsItem(
        title=title,
        description=f"{title} description",
        link=url,
        original_link=url,
        published_at=published_at,
        source_name=source_name,
    )


def build_scraped(url: str, title: str, published_at: datetime) -> ScrapedArticle:
    return ScrapedArticle(
        content=f"{title} 본문 " + ("내용 " * 120),
        summary=f"{title} summary",
        source_name="example.com",
        canonical_url=url,
        published_at=published_at,
    )


def get_test_symbol(session) -> str:
    stmt = (
        select(TickerMetadata.symbol)
        .outerjoin(NewsCache, NewsCache.symbol == TickerMetadata.symbol)
        .group_by(TickerMetadata.symbol)
        .having(func.count(NewsCache.id) == 0)
        .order_by(TickerMetadata.symbol)
        .limit(1)
    )
    symbol = session.scalar(stmt)
    if symbol is None:
        symbol = session.scalar(select(TickerMetadata.symbol).order_by(TickerMetadata.symbol).limit(1))
    if symbol is None:
        raise RuntimeError("ticker_metadata is empty")
    return symbol


def count_rows(session, symbol: str) -> tuple[int, int]:
    total_rows = session.scalar(select(func.count()).select_from(NewsCache).where(NewsCache.symbol == symbol)) or 0
    content_rows = session.scalar(
        select(func.count()).select_from(NewsCache).where(
            NewsCache.symbol == symbol,
            NewsCache.content.is_not(None),
        )
    ) or 0
    return int(total_rows), int(content_rows)


def run_initial_insert_scenario(symbol: str) -> ScenarioResult:
    base = datetime.now(UTC).replace(microsecond=0)
    items: list[NaverNewsItem] = []
    articles: dict[str, ScrapedArticle] = {}

    for index in range(15):
        url = f"https://news.example.com/{symbol}/initial/{index}"
        title = f"{symbol} {TITLE_VARIANTS[index]}"
        published_at = base - timedelta(minutes=index)
        items.append(build_item(url, title, published_at))
        if index < 5:
            articles[url] = build_scraped(url, title, published_at)

    with SessionLocal() as session:
        symbol = get_test_symbol(session)
        service = NewsIngestionService(
            session,
            news_client=FakeNaverNewsClient(items),
            article_scraper=FakeArticleScraper(articles),
            redis_client=FakeRedis(),
        )
        try:
            result = service.sync_news_for_ticker(symbol, mode="initial", force=True)
            total_rows, content_rows = count_rows(session, symbol)
            return ScenarioResult(
                name="initial_insert",
                inserted=result.inserted_count,
                updated=result.updated_count,
                skipped=result.skipped_count,
                body_saved=result.body_saved_count,
                grouped=result.grouped_count,
                trimmed_rows=result.trimmed_rows_count,
                trimmed_content=result.trimmed_content_count,
                final_rows=total_rows,
                final_content_rows=content_rows,
            )
        finally:
            session.rollback()


def run_duplicate_update_scenario(symbol: str) -> ScenarioResult:
    base = datetime.now(UTC).replace(microsecond=0)
    existing_null_url = f"https://news.example.com/{symbol}/refresh/null"
    existing_content_url = f"https://news.example.com/{symbol}/refresh/filled"
    new_url = f"https://news.example.com/{symbol}/refresh/new"

    with SessionLocal() as session:
        symbol = get_test_symbol(session)
        try:
            session.add_all(
                [
                    NewsCache(
                        symbol=symbol,
                        title=f"{symbol} null content article distinct topic",
                        content=None,
                        summary="seed summary",
                        source_name="example.com",
                        source_url=existing_null_url,
                        published_at=base - timedelta(minutes=20),
                        ttl_until=base + timedelta(days=30),
                    ),
                    NewsCache(
                        symbol=symbol,
                        title=f"{symbol} already filled article separate topic",
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
                build_item(existing_null_url, f"{symbol} null content article distinct topic", base - timedelta(minutes=20)),
                build_item(existing_content_url, f"{symbol} already filled article separate topic", base - timedelta(minutes=10)),
                build_item(new_url, f"{symbol} brand new refresh article another topic", base),
            ]
            articles = {
                existing_null_url: build_scraped(existing_null_url, "null content refill", base - timedelta(minutes=20)),
                new_url: build_scraped(new_url, "brand new refresh", base),
            }

            service = NewsIngestionService(
                session,
                news_client=FakeNaverNewsClient(items),
                article_scraper=FakeArticleScraper(articles),
                redis_client=FakeRedis(),
            )
            result = service.sync_news_for_ticker(symbol, mode="refresh", force=True, limit=3)
            total_rows, content_rows = count_rows(session, symbol)
            return ScenarioResult(
                name="duplicate_update",
                inserted=result.inserted_count,
                updated=result.updated_count,
                skipped=result.skipped_count,
                body_saved=result.body_saved_count,
                grouped=result.grouped_count,
                trimmed_rows=result.trimmed_rows_count,
                trimmed_content=result.trimmed_content_count,
                final_rows=total_rows,
                final_content_rows=content_rows,
            )
        finally:
            session.rollback()


def run_trim_scenario(symbol: str) -> ScenarioResult:
    base = datetime.now(UTC).replace(microsecond=0)
    items: list[NaverNewsItem] = []
    articles: dict[str, ScrapedArticle] = {}

    for index in range(6):
        url = f"https://news.example.com/{symbol}/trim/{index}"
        title = f"{symbol} {TITLE_VARIANTS[index]}"
        published_at = base - timedelta(minutes=index)
        items.append(build_item(url, title, published_at))
        articles[url] = build_scraped(url, title, published_at)

    with SessionLocal() as session:
        symbol = get_test_symbol(session)
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
            result = service.sync_news_for_ticker(symbol, mode="initial", force=True, limit=6)
            total_rows, content_rows = count_rows(session, symbol)
            return ScenarioResult(
                name="trim_rows_and_content",
                inserted=result.inserted_count,
                updated=result.updated_count,
                skipped=result.skipped_count,
                body_saved=result.body_saved_count,
                grouped=result.grouped_count,
                trimmed_rows=result.trimmed_rows_count,
                trimmed_content=result.trimmed_content_count,
                final_rows=total_rows,
                final_content_rows=content_rows,
            )
        finally:
            session.rollback()


def main() -> None:
    with SessionLocal() as session:
        symbol = get_test_symbol(session)

    scenarios = [
        run_initial_insert_scenario(symbol),
        run_duplicate_update_scenario(symbol),
        run_trim_scenario(symbol),
    ]

    for scenario in scenarios:
        print(f"[{scenario.name}]")
        print(f"inserted={scenario.inserted} updated={scenario.updated} skipped={scenario.skipped}")
        print(f"body_saved={scenario.body_saved} grouped={scenario.grouped}")
        print(f"trimmed_rows={scenario.trimmed_rows} trimmed_content={scenario.trimmed_content}")
        print(f"final_rows={scenario.final_rows} final_content_rows={scenario.final_content_rows}")
        print()


if __name__ == "__main__":
    main()
