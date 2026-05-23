from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import logging

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class QuoteSnapshot:
    symbol: str
    price: float
    prev_close: float | None
    change: float | None
    change_rate: float | None
    volume: int | None
    source: str
    is_delayed: bool
    ts: datetime


class QuoteClient:
    def fetch_quote(self, symbol: str) -> QuoteSnapshot:  # pragma: no cover - protocol-like base
        raise NotImplementedError


class YFinanceQuoteClient(QuoteClient):
    def fetch_quote(self, symbol: str) -> QuoteSnapshot:
        import yfinance as yf

        ticker = yf.Ticker(symbol)
        hist = ticker.history(period="5d")
        if hist.empty:
            raise RuntimeError(f"{symbol} quote data unavailable")

        latest = hist.iloc[-1]
        prev = hist.iloc[-2] if len(hist) > 1 else latest
        close_price = float(latest["Close"])
        prev_close = float(prev["Close"]) if prev is not None else None
        change = close_price - prev_close if prev_close is not None else None
        change_rate = ((change / prev_close) * 100.0) if prev_close not in (None, 0) else None

        return QuoteSnapshot(
            symbol=symbol,
            price=close_price,
            prev_close=prev_close,
            change=round(change, 2) if change is not None else None,
            change_rate=round(change_rate, 2) if change_rate is not None else None,
            volume=int(latest["Volume"]) if latest.get("Volume") is not None else None,
            source="yfinance",
            is_delayed=True,
            ts=datetime.now(UTC),
        )
