from __future__ import annotations

_SUFFIX_MAP = {
    "KOSPI": ".KS",
    "KOSDAQ": ".KQ",
}


def to_yfinance_symbol(symbol: str, market: object | None) -> str:
    if not symbol or not symbol.isdigit() or len(symbol) != 6:
        return symbol

    market_key = _coerce_market_value(market)
    suffix = _SUFFIX_MAP.get(market_key)
    return f"{symbol}{suffix}" if suffix else symbol


def resolve_yfinance_symbol(symbol: str) -> str:
    from app.core.db import SessionLocal
    from app.models import TickerMetadata

    session = SessionLocal()
    try:
        ticker = session.get(TickerMetadata, symbol)
        market = ticker.market if ticker is not None else None
        return to_yfinance_symbol(symbol, market)
    finally:
        session.close()


def _coerce_market_value(value: object | None) -> str | None:
    if value is None:
        return None
    if hasattr(value, "value"):
        value = getattr(value, "value")
    market = str(value).upper()
    return market if market in _SUFFIX_MAP else None
