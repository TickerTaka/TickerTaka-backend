from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime
import json
import logging
from zoneinfo import ZoneInfo

from app.config import get_settings
from app.core.redis import get_redis, make_key
from app.external.quote_client import QuoteClient, QuoteSnapshot, YFinanceQuoteClient

logger = logging.getLogger(__name__)

KST = ZoneInfo("Asia/Seoul")


class IntradayQuoteService:
    def __init__(
        self,
        *,
        redis_client=None,
        quote_client: QuoteClient | None = None,
    ) -> None:
        self.redis_client = redis_client if redis_client is not None else get_redis()
        self.quote_client = quote_client or YFinanceQuoteClient()

    def get_latest_quote(self, symbol: str, *, max_age_seconds: int = 300) -> QuoteSnapshot:
        cached = self._load_cached_quote(symbol)
        if cached and self._is_fresh(cached, max_age_seconds=max_age_seconds):
            return cached

        fresh = self.quote_client.fetch_quote(symbol)
        self._store_cached_quote(symbol, fresh)
        return fresh

    def _key(self, symbol: str) -> str:
        return make_key("quote", "latest", symbol)

    def _load_cached_quote(self, symbol: str) -> QuoteSnapshot | None:
        if self.redis_client is None:
            return None
        try:
            raw = self.redis_client.get(self._key(symbol))
        except Exception:
            logger.exception("quote cache read failed for %s", symbol)
            return None
        if not raw:
            return None
        try:
            payload = json.loads(raw)
            return QuoteSnapshot(
                symbol=payload["symbol"],
                price=float(payload["price"]),
                prev_close=float(payload["prev_close"]) if payload.get("prev_close") is not None else None,
                change=float(payload["change"]) if payload.get("change") is not None else None,
                change_rate=float(payload["change_rate"]) if payload.get("change_rate") is not None else None,
                volume=int(payload["volume"]) if payload.get("volume") is not None else None,
                source=payload.get("source", "unknown"),
                is_delayed=bool(payload.get("is_delayed", True)),
                ts=datetime.fromisoformat(payload["ts"]),
            )
        except Exception:
            logger.exception("quote cache decode failed for %s", symbol)
            return None

    def _store_cached_quote(self, symbol: str, quote: QuoteSnapshot) -> None:
        if self.redis_client is None:
            return
        ttl = self._ttl_seconds(datetime.now(KST))
        payload = asdict(quote)
        payload["ts"] = quote.ts.isoformat()
        try:
            self.redis_client.setex(self._key(symbol), ttl, json.dumps(payload, ensure_ascii=False))
        except Exception:
            logger.exception("quote cache write failed for %s", symbol)

    @staticmethod
    def _is_fresh(quote: QuoteSnapshot, *, max_age_seconds: int) -> bool:
        now = datetime.now(UTC)
        return (now - quote.ts).total_seconds() <= max_age_seconds

    @staticmethod
    def _ttl_seconds(now_kst: datetime) -> int:
        if now_kst.weekday() >= 5:
            return 24 * 60 * 60
        market_open = now_kst.replace(hour=9, minute=0, second=0, microsecond=0)
        market_close = now_kst.replace(hour=15, minute=30, second=0, microsecond=0)
        if market_open <= now_kst <= market_close:
            return 5 * 60
        return 30 * 60
