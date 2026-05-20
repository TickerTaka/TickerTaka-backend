from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.models import AppUser, TickerMetadata, Watchlist


class WatchlistRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get_user(self, user_id: UUID) -> AppUser | None:
        return self.session.get(AppUser, user_id)

    def get_ticker(self, symbol: str) -> TickerMetadata | None:
        return self.session.get(TickerMetadata, symbol)

    def get_by_user_and_symbol(self, user_id: UUID, symbol: str) -> Watchlist | None:
        stmt = select(Watchlist).where(Watchlist.user_id == user_id, Watchlist.symbol == symbol)
        return self.session.scalar(stmt)

    def list_by_user(self, user_id: UUID) -> list[Watchlist]:
        stmt = (
            select(Watchlist)
            .options(joinedload(Watchlist.ticker))
            .where(Watchlist.user_id == user_id)
            .order_by(Watchlist.created_at.desc())
        )
        return list(self.session.scalars(stmt))

    def list_distinct_symbols(self) -> list[str]:
        stmt = select(Watchlist.symbol).distinct().order_by(Watchlist.symbol)
        return list(self.session.scalars(stmt))

    def get_by_id(self, watchlist_id: UUID) -> Watchlist | None:
        stmt = select(Watchlist).options(joinedload(Watchlist.ticker)).where(Watchlist.id == watchlist_id)
        return self.session.scalar(stmt)

    def create(self, user_id: UUID, symbol: str, memo: str | None = None) -> Watchlist:
        watchlist = Watchlist(user_id=user_id, symbol=symbol, memo=memo)
        self.session.add(watchlist)
        self.session.flush()
        self.session.refresh(watchlist, attribute_names=["ticker"])
        return watchlist
