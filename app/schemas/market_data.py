from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


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
    """관심종목 뉴스+공시 통합 피드 항목. 감성분석 결과가 있으면 함께 내려준다."""

    id: UUID
    symbol: str
    symbol_name: str | None = None
    kind: str = Field(description='항목 종류. "news" 또는 "filing".', examples=["filing"])
    title: str
    summary: str | None = None
    source_name: str | None = None
    source_url: str
    published_at: datetime | None = None

    # ── 감성분석(evidence_analysis) 결과 — 아직 분석 전이면 모두 null ──
    sentiment: str | None = Field(
        default=None,
        description='감성 방향. "positive" | "negative" | "neutral" | "mixed". 분석 전이면 null.',
        examples=["negative"],
    )
    impact_score: int | None = Field(
        default=None,
        description="투자 영향도(강도). -2(강한 악재) ~ +2(강한 호재), 0=중립. 분석 전이면 null.",
        ge=-2,
        le=2,
        examples=[-1],
    )
    confidence: float | None = Field(
        default=None,
        description="판단 신뢰도 0.0~1.0.",
        examples=[0.78],
    )
    event_type: str | None = Field(
        default=None,
        description='공시 사건 유형. "잠정실적" | "손익구조변경" | "유상증자" | "자사주취득" | "단일판매공급계약" | "배당" | "소송" | "횡령배임" | "기타". 뉴스/미분석이면 null.',
        examples=["잠정실적"],
    )
    analysis_summary: str | None = Field(
        default=None,
        description="감성분석이 생성한 요약(원문 summary와 별개일 수 있음).",
    )
    key_points: list[str] = Field(
        default_factory=list,
        description="긍/부정 판단의 핵심 근거 문장/키워드.",
    )
    risks: list[str] = Field(
        default_factory=list,
        description="주의해야 할 리스크 문장/키워드.",
    )
    evidence: list[str] = Field(
        default_factory=list,
        description="판단 근거가 된 원문 인용(grounding 검증 통과 항목).",
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "id": "c2a3c70c-6d95-4f97-a31e-1cf28ffefe67",
                "symbol": "454910",
                "symbol_name": "두산로보틱스",
                "kind": "filing",
                "title": "연결재무제표기준영업(잠정)실적(공정공시)",
                "summary": "연결 기준 매출액 17.6% 증가, 영업이익 26.6% 감소.",
                "source_name": "DART",
                "source_url": "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260428800563",
                "published_at": "2026-04-28T16:00:00+09:00",
                "sentiment": "negative",
                "impact_score": -1,
                "confidence": 0.78,
                "event_type": "잠정실적",
                "analysis_summary": "영업이익이 전년 동기 대비 감소해 단기 실적 둔화.",
                "key_points": ["영업이익 26.6% 감소"],
                "risks": ["수익성 악화"],
                "evidence": ["영업이익 26.6% 감소"],
            }
        }
    )


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
