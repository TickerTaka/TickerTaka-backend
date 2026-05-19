from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, Enum as SQLEnum, Index, String, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.cache import (
        DataRefreshJob,
        EventTimeline,
        FilingCache,
        FinancialCache,
        NewsCache,
        PriceCache,
        TechnicalIndicatorCache,
    )
    from app.models.debate import DebateSession
    from app.models.watchlist import Watchlist


class MarketType(str, Enum):
    KOSPI = "KOSPI"
    KOSDAQ = "KOSDAQ"
    NASDAQ = "NASDAQ"
    NYSE = "NYSE"
    AMEX = "AMEX"
    OTHER = "OTHER"


class TickerMetadata(Base):
    """Security master metadata."""

    __tablename__ = "ticker_metadata"
    __table_args__ = (
        Index("idx_ticker_market_sector", "market", "sector"),
        Index("idx_ticker_name_en_trgm", "name_en", postgresql_using="gin"),
        Index("idx_ticker_name_kr", "name_kr"),
        Index("idx_ticker_name_kr_trgm", "name_kr", postgresql_using="gin"),
    )

    symbol: Mapped[str] = mapped_column(String(30), primary_key=True)
    name_kr: Mapped[str] = mapped_column(String(100), nullable=False)
    name_en: Mapped[str | None] = mapped_column(String(150))
    market: Mapped[MarketType] = mapped_column(
        SQLEnum(MarketType, name="market_type", create_type=False),
        nullable=False,
    )
    sector: Mapped[str | None] = mapped_column(String(100))
    industry: Mapped[str | None] = mapped_column(String(150))
    currency: Mapped[str | None] = mapped_column(String(10))
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("true"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )

    watchlists: Mapped[list["Watchlist"]] = relationship(back_populates="ticker")
    price_caches: Mapped[list["PriceCache"]] = relationship(back_populates="ticker")
    financial_caches: Mapped[list["FinancialCache"]] = relationship(back_populates="ticker")
    technical_indicator_caches: Mapped[list["TechnicalIndicatorCache"]] = relationship(back_populates="ticker")
    news_caches: Mapped[list["NewsCache"]] = relationship(back_populates="ticker")
    filing_caches: Mapped[list["FilingCache"]] = relationship(back_populates="ticker")
    event_timelines: Mapped[list["EventTimeline"]] = relationship(back_populates="ticker")
    data_refresh_jobs: Mapped[list["DataRefreshJob"]] = relationship(back_populates="ticker")
    debate_sessions: Mapped[list["DebateSession"]] = relationship(back_populates="ticker")
