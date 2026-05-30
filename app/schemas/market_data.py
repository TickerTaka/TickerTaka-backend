from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class TickerSearchItem(BaseModel):
    symbol: str
    name_kr: str
    name_en: str | None = None
    market: str
    sector: str | None = None
    industry: str | None = None


class TickerSearchResponse(BaseModel):
    items: list[TickerSearchItem]


class PricePoint(BaseModel):
    date: date
    open: float | None = None
    high: float | None = None
    low: float | None = None
    close: float
    adjusted_close: float | None = None
    volume: int | None = None
    change_rate: float | None = None


class FinancialSnapshot(BaseModel):
    fiscal_year: int
    fiscal_quarter: int | None = None
    revenue: float | None = None
    operating_profit: float | None = None
    net_income: float | None = None
    total_assets: float | None = None
    total_liabilities: float | None = None
    total_equity: float | None = None
    per: float | None = None
    pbr: float | None = None
    roe: float | None = None
    debt_ratio: float | None = None


class TechnicalSnapshot(BaseModel):
    date: date
    ma20: float | None = None
    ma60: float | None = None
    ma120: float | None = None
    rsi14: float | None = None
    macd: float | None = None
    macd_signal: float | None = None
    macd_hist: float | None = None
    volume_ma20: float | None = None


class StockDetailResponse(BaseModel):
    symbol: str
    name_kr: str
    name_en: str | None = None
    market: str
    sector: str | None = None
    industry: str | None = None
    currency: str | None = None
    latest_price: PricePoint | None = None
    latest_financial: FinancialSnapshot | None = None
    latest_technical: TechnicalSnapshot | None = None


class StockPricesResponse(BaseModel):
    symbol: str
    prices: list[PricePoint]


class NewsItem(BaseModel):
    id: UUID
    symbol: str
    title: str
    summary: str | None = None
    source_name: str | None = None
    source_url: str
    published_at: datetime | None = None
    retrieved_at: datetime


class NewsListResponse(BaseModel):
    items: list[NewsItem]


class FilingItem(BaseModel):
    id: UUID
    symbol: str
    filing_title: str
    filing_type: str | None = None
    summary: str | None = None
    source_url: str
    disclosed_at: datetime | None = None
    retrieved_at: datetime


class FilingListResponse(BaseModel):
    items: list[FilingItem]


class WatchlistFeedItem(BaseModel):
    """Unified news + filing feed item for a user's watchlist."""

    id: UUID
    symbol: str
    symbol_name: str | None = None
    kind: str  # "news" | "filing"
    title: str
    summary: str | None = None
    source_name: str | None = None
    source_url: str
    published_at: datetime | None = None


class WatchlistFeedResponse(BaseModel):
    items: list[WatchlistFeedItem]


class MarketIndexItem(BaseModel):
    market: str
    name: str
    average_change_rate: float | None = None
    advancers: int = 0
    decliners: int = 0
    unchanged: int = 0
    constituents: int = 0


class MarketIndexesResponse(BaseModel):
    items: list[MarketIndexItem]


class DashboardStatsResponse(BaseModel):
    ticker_count: int
    active_ticker_count: int
    news_count: int
    debate_session_count: int
    completed_debate_count: int
    latest_news_at: datetime | None = None
    latest_price_date: date | None = None


class DebateListItem(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    session_id: UUID
    user_id: UUID
    symbol: str
    symbol_name: str | None = None
    category: str
    status: str
    started_at: datetime | None = None
    completed_at: datetime | None = None
    summary_content: str | None = None


class DebateListResponse(BaseModel):
    items: list[DebateListItem]
