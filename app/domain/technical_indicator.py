from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd

from app.models import PriceCache


def build_indicator_rows(symbol: str, price_rows: list[PriceCache]) -> list[dict]:
    if not price_rows:
        return []

    ordered_rows = sorted(price_rows, key=lambda row: row.price_date)
    frame = pd.DataFrame(
        {
            "price_date": [row.price_date for row in ordered_rows],
            "close_price": [float(row.close_price) for row in ordered_rows],
            "volume": [float(row.volume or 0) for row in ordered_rows],
        }
    )
    frame["ma20"] = frame["close_price"].rolling(window=20, min_periods=20).mean()
    frame["ma60"] = frame["close_price"].rolling(window=60, min_periods=60).mean()
    frame["ma120"] = frame["close_price"].rolling(window=120, min_periods=120).mean()
    frame["volume_ma20"] = frame["volume"].rolling(window=20, min_periods=20).mean()

    delta = frame["close_price"].diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.rolling(window=14, min_periods=14).mean()
    avg_loss = loss.rolling(window=14, min_periods=14).mean()
    rs = avg_gain / avg_loss.replace({0: pd.NA})
    frame["rsi14"] = 100 - (100 / (1 + rs))

    ema12 = frame["close_price"].ewm(span=12, adjust=False, min_periods=12).mean()
    ema26 = frame["close_price"].ewm(span=26, adjust=False, min_periods=26).mean()
    frame["macd"] = ema12 - ema26
    frame["macd_signal"] = frame["macd"].ewm(span=9, adjust=False, min_periods=9).mean()
    frame["macd_hist"] = frame["macd"] - frame["macd_signal"]

    retrieved_at = datetime.now(UTC)
    records: list[dict] = []
    for record in frame.to_dict(orient="records"):
        records.append(
            {
                "symbol": symbol,
                "indicator_date": record["price_date"],
                "ma20": _to_optional_float(record["ma20"]),
                "ma60": _to_optional_float(record["ma60"]),
                "ma120": _to_optional_float(record["ma120"]),
                "rsi14": _to_optional_float(record["rsi14"]),
                "macd": _to_optional_float(record["macd"]),
                "macd_signal": _to_optional_float(record["macd_signal"]),
                "macd_hist": _to_optional_float(record["macd_hist"]),
                "volume_ma20": _to_optional_float(record["volume_ma20"]),
                "retrieved_at": retrieved_at,
            }
        )
    return records


def _to_optional_float(value) -> float | None:
    if pd.isna(value):
        return None
    return float(value)
