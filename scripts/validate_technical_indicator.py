from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from app.domain.technical_indicator import build_indicator_rows
from app.models import PriceCache


def main() -> None:
    base = date(2026, 1, 1)
    rows = [
        PriceCache(
            symbol="000020",
            price_date=base + timedelta(days=index),
            open_price=100 + (index % 7) - 3,
            high_price=102 + (index % 7),
            low_price=98 + (index % 5),
            close_price=100 + ((index % 9) - 4),
            adjusted_close=None,
            volume=1000 + index,
            change_rate=None,
            retrieved_at=datetime.now(UTC),
        )
        for index in range(140)
    ]
    indicators = build_indicator_rows("000020", rows)
    assert len(indicators) == 140
    assert indicators[-1]["ma20"] is not None
    assert indicators[-1]["ma120"] is not None
    assert indicators[-1]["rsi14"] is not None
    assert indicators[-1]["macd"] is not None
    print(
        {
            "rows": len(indicators),
            "last_date": indicators[-1]["indicator_date"].isoformat(),
            "ma20_ready": indicators[-1]["ma20"] is not None,
            "ma120_ready": indicators[-1]["ma120"] is not None,
        }
    )


if __name__ == "__main__":
    main()
