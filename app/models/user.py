from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import DateTime, String, Text, text
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.cache import DataRefreshJob
    from app.models.debate import DebateNote, DebateSession
    from app.models.watchlist import Watchlist


class AppUser(Base):
    """Application user account."""

    __tablename__ = "app_user"

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str | None] = mapped_column(String(100))
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

    watchlists: Mapped[list["Watchlist"]] = relationship(back_populates="user")
    debate_sessions: Mapped[list["DebateSession"]] = relationship(back_populates="user")
    debate_notes: Mapped[list["DebateNote"]] = relationship(back_populates="user")
    data_refresh_jobs: Mapped[list["DataRefreshJob"]] = relationship(back_populates="user")
