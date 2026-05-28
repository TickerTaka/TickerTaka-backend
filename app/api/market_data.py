from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.models import (
    DebateSession,
    DebateStatus,
    FinancialCache,
    MarketType,
    NewsCache,
    PriceCache,
    TechnicalIndicatorCache,
    TickerMetadata,
)
from app.schemas.market_data import (
    DashboardStatsResponse,
    FinancialSnapshot,
    MarketIndexItem,
    MarketIndexesResponse,
    NewsItem,
    NewsListResponse,
    PricePoint,
    StockDetailResponse,
    StockPricesResponse,
    TechnicalSnapshot,
    TickerSearchItem,
    TickerSearchResponse,
)

router = APIRouter(tags=["market-data"])


def _float(value) -> float | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return float(value)
    return float(value)


def _market_value(value: MarketType | str) -> str:
    return value.value if hasattr(value, "value") else str(value)


def _price_point(row: PriceCache) -> PricePoint:
    return PricePoint(
        date=row.price_date,
        open=_float(row.open_price),
        high=_float(row.high_price),
        low=_float(row.low_price),
        close=float(row.close_price),
        adjusted_close=_float(row.adjusted_close),
        volume=row.volume,
        change_rate=_float(row.change_rate),
    )


def _financial_snapshot(row: FinancialCache) -> FinancialSnapshot:
    return FinancialSnapshot(
        fiscal_year=row.fiscal_year,
        fiscal_quarter=row.fiscal_quarter,
        revenue=_float(row.revenue),
        operating_profit=_float(row.operating_profit),
        net_income=_float(row.net_income),
        total_assets=_float(row.total_assets),
        total_liabilities=_float(row.total_liabilities),
        total_equity=_float(row.total_equity),
        per=_float(row.per),
        pbr=_float(row.pbr),
        roe=_float(row.roe),
        debt_ratio=_float(row.debt_ratio),
    )


def _technical_snapshot(row: TechnicalIndicatorCache) -> TechnicalSnapshot:
    return TechnicalSnapshot(
        date=row.indicator_date,
        ma20=_float(row.ma20),
        ma60=_float(row.ma60),
        ma120=_float(row.ma120),
        rsi14=_float(row.rsi14),
        macd=_float(row.macd),
        macd_signal=_float(row.macd_signal),
        macd_hist=_float(row.macd_hist),
        volume_ma20=_float(row.volume_ma20),
    )


def _news_item(row: NewsCache) -> NewsItem:
    return NewsItem(
        id=row.id,
        symbol=row.symbol,
        title=row.title,
        summary=row.summary,
        source_name=row.source_name,
        source_url=row.source_url,
        published_at=row.published_at,
        retrieved_at=row.retrieved_at,
    )


@router.get("/api/tickers", response_model=TickerSearchResponse)
def search_tickers(
    q: str = Query("", max_length=100),
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
) -> TickerSearchResponse:
    query = q.strip()
    stmt = select(TickerMetadata).where(TickerMetadata.is_active.is_(True))
    if query:
        pattern = f"%{query}%"
        stmt = stmt.where(
            or_(
                TickerMetadata.symbol.ilike(pattern),
                TickerMetadata.name_kr.ilike(pattern),
                TickerMetadata.name_en.ilike(pattern),
            )
        )
    stmt = stmt.order_by(TickerMetadata.symbol).limit(limit)
    rows = list(db.scalars(stmt))
    return TickerSearchResponse(
        items=[
            TickerSearchItem(
                symbol=row.symbol,
                name_kr=row.name_kr,
                name_en=row.name_en,
                market=_market_value(row.market),
                sector=row.sector,
                industry=row.industry,
            )
            for row in rows
        ]
    )


@router.get("/api/stocks/{symbol}", response_model=StockDetailResponse)
def get_stock_detail(symbol: str, db: Session = Depends(get_db)) -> StockDetailResponse:
    ticker = db.get(TickerMetadata, symbol)
    if ticker is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"ticker not found: {symbol}")

    price = db.scalar(
        select(PriceCache).where(PriceCache.symbol == symbol).order_by(PriceCache.price_date.desc()).limit(1)
    )
    financial = db.scalar(
        select(FinancialCache)
        .where(FinancialCache.symbol == symbol)
        .order_by(FinancialCache.fiscal_year.desc(), FinancialCache.fiscal_quarter.desc().nullslast())
        .limit(1)
    )
    technical = db.scalar(
        select(TechnicalIndicatorCache)
        .where(TechnicalIndicatorCache.symbol == symbol)
        .order_by(TechnicalIndicatorCache.indicator_date.desc())
        .limit(1)
    )

    return StockDetailResponse(
        symbol=ticker.symbol,
        name_kr=ticker.name_kr,
        name_en=ticker.name_en,
        market=_market_value(ticker.market),
        sector=ticker.sector,
        industry=ticker.industry,
        currency=ticker.currency,
        latest_price=_price_point(price) if price else None,
        latest_financial=_financial_snapshot(financial) if financial else None,
        latest_technical=_technical_snapshot(technical) if technical else None,
    )


@router.get("/api/stocks/{symbol}/prices", response_model=StockPricesResponse)
def get_stock_prices(
    symbol: str,
    limit: int = Query(260, ge=1, le=1000),
    db: Session = Depends(get_db),
) -> StockPricesResponse:
    if db.get(TickerMetadata, symbol) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"ticker not found: {symbol}")

    rows = list(
        db.scalars(
            select(PriceCache).where(PriceCache.symbol == symbol).order_by(PriceCache.price_date.desc()).limit(limit)
        )
    )
    rows.reverse()
    return StockPricesResponse(symbol=symbol, prices=[_price_point(row) for row in rows])


@router.get("/api/stocks/{symbol}/news", response_model=NewsListResponse)
def get_stock_news(
    symbol: str,
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
) -> NewsListResponse:
    if db.get(TickerMetadata, symbol) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"ticker not found: {symbol}")

    rows = list(
        db.scalars(
            select(NewsCache)
            .where(NewsCache.symbol == symbol)
            .order_by(NewsCache.published_at.desc().nullslast(), NewsCache.retrieved_at.desc())
            .limit(limit)
        )
    )
    return NewsListResponse(items=[_news_item(row) for row in rows])


@router.get("/api/news/recent", response_model=NewsListResponse)
def get_recent_news(
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
) -> NewsListResponse:
    rows = list(
        db.scalars(
            select(NewsCache)
            .order_by(NewsCache.published_at.desc().nullslast(), NewsCache.retrieved_at.desc())
            .limit(limit)
        )
    )
    return NewsListResponse(items=[_news_item(row) for row in rows])


@router.get("/api/market/indexes", response_model=MarketIndexesResponse)
def get_market_indexes(db: Session = Depends(get_db)) -> MarketIndexesResponse:
    latest_price_dates = (
        select(PriceCache.symbol, func.max(PriceCache.price_date).label("latest_date"))
        .group_by(PriceCache.symbol)
        .subquery()
    )
    stmt = (
        select(
            TickerMetadata.market,
            func.avg(PriceCache.change_rate).label("average_change_rate"),
            func.sum(case((PriceCache.change_rate > 0, 1), else_=0)).label("advancers"),
            func.sum(case((PriceCache.change_rate < 0, 1), else_=0)).label("decliners"),
            func.sum(case((PriceCache.change_rate == 0, 1), else_=0)).label("unchanged"),
            func.count().label("constituents"),
        )
        .join(PriceCache, PriceCache.symbol == TickerMetadata.symbol)
        .join(
            latest_price_dates,
            and_(
                latest_price_dates.c.symbol == PriceCache.symbol,
                latest_price_dates.c.latest_date == PriceCache.price_date,
            ),
        )
        .group_by(TickerMetadata.market)
        .order_by(TickerMetadata.market)
    )
    rows = db.execute(stmt).all()
    return MarketIndexesResponse(
        items=[
            MarketIndexItem(
                market=_market_value(row.market),
                name=_market_value(row.market),
                average_change_rate=_float(row.average_change_rate),
                advancers=int(row.advancers or 0),
                decliners=int(row.decliners or 0),
                unchanged=int(row.unchanged or 0),
                constituents=int(row.constituents or 0),
            )
            for row in rows
        ]
    )


@router.get("/api/dashboard/stats", response_model=DashboardStatsResponse)
def get_dashboard_stats(db: Session = Depends(get_db)) -> DashboardStatsResponse:
    return DashboardStatsResponse(
        ticker_count=db.scalar(select(func.count()).select_from(TickerMetadata)) or 0,
        active_ticker_count=db.scalar(
            select(func.count()).select_from(TickerMetadata).where(TickerMetadata.is_active.is_(True))
        )
        or 0,
        news_count=db.scalar(select(func.count()).select_from(NewsCache)) or 0,
        debate_session_count=db.scalar(select(func.count()).select_from(DebateSession)) or 0,
        completed_debate_count=db.scalar(
            select(func.count()).select_from(DebateSession).where(DebateSession.status == DebateStatus.COMPLETED)
        )
        or 0,
        latest_news_at=db.scalar(select(func.max(NewsCache.published_at))),
        latest_price_date=db.scalar(select(func.max(PriceCache.price_date))),
    )
