from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import (
    BIGINT,
    JSON,
    Boolean,
    Date,
    DateTime,
    Enum as SQLEnum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.debate import Evidence
    from app.models.ticker import TickerMetadata
    from app.models.user import AppUser


class SourceType(str, Enum):
    DART = "DART"
    NEWS = "NEWS"
    PRICE = "PRICE"
    FINANCIAL = "FINANCIAL"
    TECHNICAL = "TECHNICAL"
    INDUSTRY = "INDUSTRY"
    MACRO = "MACRO"
    USER_INPUT = "USER_INPUT"
    OTHER = "OTHER"


class RefreshJobType(str, Enum):
    PRICE = "price"
    NEWS = "news"
    FILING = "filing"
    FINANCIAL = "financial"
    TECHNICAL = "technical"
    EVENT = "event"
    ALL = "all"


class RefreshJobStatus(str, Enum):
    QUEUED = "queued"
    PUBLISHED = "published"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class PriceCache(Base):
    """Cached daily price history."""

    __tablename__ = "price_cache"
    __table_args__ = (
        UniqueConstraint("symbol", "price_date", name="uq_price_cache"),
        Index("idx_price_cache_symbol_date", "symbol", text("price_date DESC")),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    symbol: Mapped[str] = mapped_column(String(30), ForeignKey("ticker_metadata.symbol", ondelete="CASCADE"), nullable=False)
    price_date: Mapped[date] = mapped_column(Date, nullable=False)
    open_price: Mapped[float | None] = mapped_column(Numeric(18, 4))
    high_price: Mapped[float | None] = mapped_column(Numeric(18, 4))
    low_price: Mapped[float | None] = mapped_column(Numeric(18, 4))
    close_price: Mapped[float] = mapped_column(Numeric(18, 4), nullable=False)
    adjusted_close: Mapped[float | None] = mapped_column(Numeric(18, 4))
    volume: Mapped[int | None] = mapped_column(BIGINT)
    change_rate: Mapped[float | None] = mapped_column(Numeric(8, 4))
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))

    ticker: Mapped["TickerMetadata"] = relationship(back_populates="price_caches")
    evidences: Mapped[list["Evidence"]] = relationship(back_populates="price_cache")


class FinancialCache(Base):
    """Cached financial statement snapshot."""

    __tablename__ = "financial_cache"
    __table_args__ = (
        UniqueConstraint("symbol", "fiscal_year", "fiscal_quarter", name="uq_financial_cache"),
        Index("idx_financial_cache_lookup", "symbol", text("fiscal_year DESC"), "fiscal_quarter"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    symbol: Mapped[str] = mapped_column(String(30), ForeignKey("ticker_metadata.symbol", ondelete="CASCADE"), nullable=False)
    fiscal_year: Mapped[int] = mapped_column(Integer, nullable=False)
    fiscal_quarter: Mapped[int | None] = mapped_column(Integer)
    revenue: Mapped[float | None] = mapped_column(Numeric(20, 2))
    operating_profit: Mapped[float | None] = mapped_column(Numeric(20, 2))
    net_income: Mapped[float | None] = mapped_column(Numeric(20, 2))
    total_assets: Mapped[float | None] = mapped_column(Numeric(20, 2))
    total_liabilities: Mapped[float | None] = mapped_column(Numeric(20, 2))
    total_equity: Mapped[float | None] = mapped_column(Numeric(20, 2))
    per: Mapped[float | None] = mapped_column(Numeric(10, 4))
    pbr: Mapped[float | None] = mapped_column(Numeric(10, 4))
    roe: Mapped[float | None] = mapped_column(Numeric(10, 4))
    debt_ratio: Mapped[float | None] = mapped_column(Numeric(10, 4))
    source_url: Mapped[str | None] = mapped_column(Text)
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))

    ticker: Mapped["TickerMetadata"] = relationship(back_populates="financial_caches")
    evidences: Mapped[list["Evidence"]] = relationship(back_populates="financial_cache")


class TechnicalIndicatorCache(Base):
    """Cached technical indicator snapshot."""

    __tablename__ = "technical_indicator_cache"
    __table_args__ = (
        UniqueConstraint("symbol", "indicator_date", name="uq_technical_indicator"),
        Index("idx_technical_indicator_lookup", "symbol", text("indicator_date DESC")),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    symbol: Mapped[str] = mapped_column(String(30), ForeignKey("ticker_metadata.symbol", ondelete="CASCADE"), nullable=False)
    indicator_date: Mapped[date] = mapped_column(Date, nullable=False)
    ma20: Mapped[float | None] = mapped_column(Numeric(18, 4))
    ma60: Mapped[float | None] = mapped_column(Numeric(18, 4))
    ma120: Mapped[float | None] = mapped_column(Numeric(18, 4))
    rsi14: Mapped[float | None] = mapped_column(Numeric(6, 4))
    macd: Mapped[float | None] = mapped_column(Numeric(18, 4))
    macd_signal: Mapped[float | None] = mapped_column(Numeric(18, 4))
    macd_hist: Mapped[float | None] = mapped_column(Numeric(18, 4))
    volume_ma20: Mapped[float | None] = mapped_column(Numeric(20, 2))
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))

    ticker: Mapped["TickerMetadata"] = relationship(back_populates="technical_indicator_caches")
    evidences: Mapped[list["Evidence"]] = relationship(back_populates="technical_indicator_cache")


class NewsCache(Base):
    """Cached article metadata and extracted content."""

    __tablename__ = "news_cache"
    __table_args__ = (
        Index("idx_news_source_url_unique", "source_url", unique=True),
        Index("idx_news_symbol_published", "symbol", text("published_at DESC")),
        Index("idx_news_ttl", "symbol", "ttl_until", postgresql_where=text("ttl_until IS NOT NULL")),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    symbol: Mapped[str] = mapped_column(String(30), ForeignKey("ticker_metadata.symbol", ondelete="CASCADE"), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[str | None] = mapped_column(Text)
    summary: Mapped[str | None] = mapped_column(Text)
    source_name: Mapped[str | None] = mapped_column(String(150))
    source_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    ttl_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    ticker: Mapped["TickerMetadata"] = relationship(back_populates="news_caches")
    evidences: Mapped[list["Evidence"]] = relationship(back_populates="news_cache")


class FilingCache(Base):
    """Cached disclosure filing document."""

    __tablename__ = "filing_cache"
    __table_args__ = (
        Index("idx_filing_symbol_disclosed", "symbol", text("disclosed_at DESC")),
        Index("idx_filing_ttl", "symbol", "ttl_until", postgresql_where=text("ttl_until IS NOT NULL")),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    symbol: Mapped[str] = mapped_column(String(30), ForeignKey("ticker_metadata.symbol", ondelete="CASCADE"), nullable=False)
    filing_title: Mapped[str] = mapped_column(Text, nullable=False)
    filing_type: Mapped[str | None] = mapped_column(String(100))
    content: Mapped[str | None] = mapped_column(Text)
    summary: Mapped[str | None] = mapped_column(Text)
    dart_receipt_no: Mapped[str | None] = mapped_column(String(20), unique=True)
    source_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    disclosed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    ttl_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    ticker: Mapped["TickerMetadata"] = relationship(back_populates="filing_caches")
    evidences: Mapped[list["Evidence"]] = relationship(back_populates="filing_cache")


class EventTimeline(Base):
    """Material event timeline for a symbol."""

    __tablename__ = "event_timeline"
    __table_args__ = (
        UniqueConstraint("symbol", "event_date", "event_title", name="uq_event_timeline"),
        Index("idx_event_timeline_symbol_date", "symbol", text("event_date DESC")),
        Index("idx_event_timeline_type_status", "event_type", "event_status"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    symbol: Mapped[str] = mapped_column(String(30), ForeignKey("ticker_metadata.symbol", ondelete="CASCADE"), nullable=False)
    event_date: Mapped[date] = mapped_column(Date, nullable=False)
    event_title: Mapped[str] = mapped_column(String(255), nullable=False)
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    event_status: Mapped[str] = mapped_column(String(30), nullable=False)
    source_type: Mapped[SourceType | None] = mapped_column(SQLEnum(SourceType, name="source_type", create_type=False))
    source_url: Mapped[str | None] = mapped_column(String(2048))
    description: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[float | None] = mapped_column(Numeric(5, 2))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))

    ticker: Mapped["TickerMetadata"] = relationship(back_populates="event_timelines")


class DataRefreshJob(Base):
    """Refresh orchestration job state."""

    __tablename__ = "data_refresh_job"
    __table_args__ = (
        Index("idx_data_refresh_job_kafka_message_id", "kafka_message_id"),
        Index("idx_data_refresh_job_status_schedule", "status", "scheduled_at"),
        Index("idx_data_refresh_job_symbol_type_status", "symbol", "job_type", "status"),
        Index("idx_data_refresh_job_user_created", "user_id", text("created_at DESC")),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    user_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), ForeignKey("app_user.id", ondelete="SET NULL"))
    symbol: Mapped[str] = mapped_column(String(30), ForeignKey("ticker_metadata.symbol", ondelete="CASCADE"), nullable=False)
    job_type: Mapped[RefreshJobType] = mapped_column(
        SQLEnum(RefreshJobType, name="refresh_job_type", create_type=False),
        nullable=False,
        server_default=text("'all'::refresh_job_type"),
    )
    status: Mapped[RefreshJobStatus] = mapped_column(
        SQLEnum(RefreshJobStatus, name="refresh_job_status", create_type=False),
        nullable=False,
        server_default=text("'queued'::refresh_job_status"),
    )
    kafka_topic: Mapped[str | None] = mapped_column(String(100))
    kafka_key: Mapped[str | None] = mapped_column(String(100))
    kafka_message_id: Mapped[str | None] = mapped_column(String(100))
    kafka_partition: Mapped[int | None] = mapped_column(Integer)
    kafka_offset: Mapped[int | None] = mapped_column(BIGINT)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("100"))
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    max_retries: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("3"))
    error_message: Mapped[str | None] = mapped_column(Text)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))

    user: Mapped["AppUser | None"] = relationship(back_populates="data_refresh_jobs")
    ticker: Mapped["TickerMetadata"] = relationship(back_populates="data_refresh_jobs")
