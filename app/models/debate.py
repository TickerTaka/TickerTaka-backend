from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import Boolean, DateTime, Enum as SQLEnum, ForeignKey, Index, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base
from app.models.cache import SourceType

if TYPE_CHECKING:
    from app.models.cache import FilingCache, FinancialCache, NewsCache, PriceCache, TechnicalIndicatorCache
    from app.models.ticker import TickerMetadata
    from app.models.user import AppUser


class DebateCategory(str, Enum):
    TECHNICAL = "technical"
    FINANCIAL = "financial"
    MARKET = "market"
    MACRO = "macro"
    SYNTHESIS = "synthesis"


class DebateMode(str, Enum):
    MODERATOR = "moderator"


class DebateStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class DebateRound(str, Enum):
    OPENING = "opening"
    REBUTTAL = "rebuttal"
    CLOSING = "closing"
    SUMMARY = "summary"


class AgentRole(str, Enum):
    BULL = "bull"
    BEAR = "bear"
    MODERATOR = "moderator"
    SYSTEM = "system"


def _enum_values(enum_cls: type[Enum]) -> list[str]:
    return [member.value for member in enum_cls]


class DebateSession(Base):
    """Debate execution session."""

    __tablename__ = "debate_session"
    __table_args__ = (
        Index("idx_debate_cache_key", "cache_key", unique=True, postgresql_where=text("cache_key IS NOT NULL")),
        Index("idx_debate_session_symbol", "symbol", "category", text("started_at DESC")),
        Index("idx_debate_session_user", "user_id", text("started_at DESC")),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    user_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("app_user.id", ondelete="CASCADE"), nullable=False)
    symbol: Mapped[str] = mapped_column(String(30), ForeignKey("ticker_metadata.symbol"), nullable=False)
    category: Mapped[DebateCategory] = mapped_column(
        SQLEnum(
            DebateCategory,
            name="debate_category",
            create_type=False,
            values_callable=_enum_values,
        ),
        nullable=False,
    )
    mode: Mapped[DebateMode] = mapped_column(
        SQLEnum(
            DebateMode,
            name="debate_mode",
            create_type=False,
            values_callable=_enum_values,
        ),
        nullable=False,
        server_default=text("'moderator'::debate_mode"),
    )
    status: Mapped[DebateStatus] = mapped_column(
        SQLEnum(
            DebateStatus,
            name="debate_status",
            create_type=False,
            values_callable=_enum_values,
        ),
        nullable=False,
        server_default=text("'pending'::debate_status"),
    )
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cached_from_session_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), ForeignKey("debate_session.id"))
    error_message: Mapped[str | None] = mapped_column(Text)
    cache_key: Mapped[str | None] = mapped_column(String(255))

    user: Mapped["AppUser"] = relationship(back_populates="debate_sessions")
    ticker: Mapped["TickerMetadata"] = relationship(back_populates="debate_sessions")
    cached_from_session: Mapped["DebateSession | None"] = relationship(remote_side=[id], back_populates="cached_sessions")
    cached_sessions: Mapped[list["DebateSession"]] = relationship(back_populates="cached_from_session")
    statements: Mapped[list["AgentStatement"]] = relationship(back_populates="session")
    moderator_summaries: Mapped[list["ModeratorSummary"]] = relationship(back_populates="session")
    debate_notes: Mapped[list["DebateNote"]] = relationship(back_populates="session")


class AgentStatement(Base):
    """Individual agent output within a debate session."""

    __tablename__ = "agent_statement"
    __table_args__ = (
        UniqueConstraint("session_id", "round_order", name="uq_statement_order"),
        Index("idx_statement_role", "agent_role"),
        Index("idx_statement_session_order", "session_id", "round_order"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    session_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("debate_session.id", ondelete="CASCADE"), nullable=False)
    round: Mapped[DebateRound] = mapped_column(
        SQLEnum(
            DebateRound,
            name="debate_round",
            create_type=False,
            values_callable=_enum_values,
        ),
        nullable=False,
    )
    round_order: Mapped[int] = mapped_column(Integer, nullable=False)
    agent_role: Mapped[AgentRole] = mapped_column(
        SQLEnum(
            AgentRole,
            name="agent_role",
            create_type=False,
            values_callable=_enum_values,
        ),
        nullable=False,
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    model_name: Mapped[str | None] = mapped_column(String(100))
    prompt_tokens: Mapped[int | None] = mapped_column(Integer)
    completion_tokens: Mapped[int | None] = mapped_column(Integer)
    latency_ms: Mapped[int | None] = mapped_column(Integer)

    session: Mapped["DebateSession"] = relationship(back_populates="statements")
    evidences: Mapped[list["Evidence"]] = relationship(back_populates="statement")


class Evidence(Base):
    """Evidence attached to an agent statement."""

    __tablename__ = "evidence"
    __table_args__ = (
        Index("idx_evidence_source", "source_type"),
        Index("idx_evidence_statement", "statement_id"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    statement_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("agent_statement.id", ondelete="CASCADE"), nullable=False)
    source_type: Mapped[SourceType] = mapped_column(SQLEnum(SourceType, name="source_type", create_type=False), nullable=False)
    source_url: Mapped[str | None] = mapped_column(String(2048))
    source_label: Mapped[str | None] = mapped_column(String(200))
    source_title: Mapped[str | None] = mapped_column(Text)
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    excerpt: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    news_cache_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), ForeignKey("news_cache.id", ondelete="SET NULL"))
    filing_cache_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), ForeignKey("filing_cache.id", ondelete="SET NULL"))
    price_cache_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), ForeignKey("price_cache.id", ondelete="SET NULL"))
    financial_cache_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), ForeignKey("financial_cache.id", ondelete="SET NULL"))
    technical_indicator_cache_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), ForeignKey("technical_indicator_cache.id", ondelete="SET NULL"))

    statement: Mapped["AgentStatement"] = relationship(back_populates="evidences")
    news_cache: Mapped["NewsCache | None"] = relationship(back_populates="evidences")
    filing_cache: Mapped["FilingCache | None"] = relationship(back_populates="evidences")
    price_cache: Mapped["PriceCache | None"] = relationship(back_populates="evidences")
    financial_cache: Mapped["FinancialCache | None"] = relationship(back_populates="evidences")
    technical_indicator_cache: Mapped["TechnicalIndicatorCache | None"] = relationship(back_populates="evidences")


class ModeratorSummary(Base):
    """Moderator summary generated for a session."""

    __tablename__ = "moderator_summary"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    session_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("debate_session.id", ondelete="CASCADE"), nullable=False, unique=True)
    summary_content: Mapped[str] = mapped_column(Text, nullable=False)
    key_points: Mapped[dict | list | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))

    session: Mapped["DebateSession"] = relationship(back_populates="moderator_summaries")


class DebateNote(Base):
    """User-specific note for a debate session."""

    __tablename__ = "debate_note"
    __table_args__ = (UniqueConstraint("user_id", "session_id", name="uq_debate_note"),)

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    user_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("app_user.id", ondelete="CASCADE"), nullable=False)
    session_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("debate_session.id", ondelete="CASCADE"), nullable=False)
    is_favorite: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    user_memo: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))

    user: Mapped["AppUser"] = relationship(back_populates="debate_notes")
    session: Mapped["DebateSession"] = relationship(back_populates="debate_notes")
