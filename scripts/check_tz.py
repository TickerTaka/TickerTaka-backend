from datetime import datetime, timezone, timedelta

from sqlalchemy import select

from app.core.db import SessionLocal
from app.models import TickerMetadata


def main() -> None:
    print("Current UTC now:", datetime.now(timezone.utc))
    print("Current KST now:", datetime.now(timezone(timedelta(hours=9))))
    print("Current local now:", datetime.now())
    print()
    with SessionLocal() as session:
        rows = session.scalars(select(TickerMetadata).limit(5)).all()
        print(f"Found {len(rows)} ticker_metadata rows")
        for row in rows:
            ca = row.created_at
            print(
                f"  symbol={row.symbol!r:>15} created_at={ca} "
                f"tzinfo={ca.tzinfo} utcoffset={ca.utcoffset() if ca.tzinfo else 'naive'}"
            )


if __name__ == "__main__":
    main()
