from sqlalchemy import select

from app.core.db import SessionLocal
from app.models import (
    AgentStatement,
    AppUser,
    DataRefreshJob,
    DebateNote,
    DebateSession,
    EventTimeline,
    Evidence,
    FilingCache,
    FinancialCache,
    ModeratorSummary,
    NewsCache,
    PriceCache,
    TechnicalIndicatorCache,
    TickerMetadata,
    Watchlist,
)


MODELS = [
    AppUser,
    TickerMetadata,
    PriceCache,
    FinancialCache,
    TechnicalIndicatorCache,
    NewsCache,
    FilingCache,
    EventTimeline,
    DataRefreshJob,
    DebateSession,
    AgentStatement,
    Evidence,
    ModeratorSummary,
    DebateNote,
    Watchlist,
]


def main() -> None:
    with SessionLocal() as session:
        for model in MODELS:
            row = session.scalar(select(model).limit(1))
            print(f"{model.__name__}: OK ({'row' if row is not None else 'empty'})")


if __name__ == "__main__":
    main()
