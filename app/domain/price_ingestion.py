from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.core.redis import build_redis_client, make_key
from app.domain.financial_ratios import compute_pbr, compute_per, normalize_positive_ratio
from app.domain.technical_indicator import build_indicator_rows
from app.external.krx_client import DailyPriceRecord, PyKrxClient
from app.models import PriceCache, TickerMetadata
from app.repositories.financial_cache_repository import FinancialCacheRepository
from app.repositories.price_cache_repository import PriceCacheRepository
from app.repositories.technical_indicator_cache_repository import TechnicalIndicatorCacheRepository

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class SyncPriceResult:
    fetched_count: int = 0
    inserted_count: int = 0
    updated_count: int = 0
    indicators_count: int = 0
    skipped_count: int = 0
    trimmed_price_rows: int = 0
    trimmed_indicator_rows: int = 0
    elapsed_ms: int = 0


class PriceIngestionService:
    INITIAL_BACKFILL_DAYS = 365
    MAX_CACHE_ROWS = 1260
    COOL_DOWN_MINUTES = 60
    LOCK_TTL_SECONDS = 600
    RECENT_OVERWRITE_DAYS = 5

    def __init__(
        self,
        session: Session,
        *,
        price_client: PyKrxClient | None = None,
        redis_client=None,
    ) -> None:
        self.session = session
        self.price_repo = PriceCacheRepository(session)
        self.indicator_repo = TechnicalIndicatorCacheRepository(session)
        self.financial_repo = FinancialCacheRepository(session)
        self.price_client = price_client or PyKrxClient()
        self.redis_client = redis_client or build_redis_client(get_settings().redis_url)

    def sync_prices_for_ticker(
        self,
        symbol: str,
        mode: str = "initial",
        force: bool = False,
        backfill_days: int | None = None,
    ) -> SyncPriceResult:
        started = datetime.now(UTC)
        result = SyncPriceResult()
        ticker = self.session.get(TickerMetadata, symbol)
        if ticker is None:
            raise ValueError(f"ticker not found: {symbol}")

        lock_token = self._acquire_lock(symbol)
        if lock_token is None:
            result.skipped_count += 1
            return result

        try:
            if not force and self._is_within_cooldown(symbol):
                result.skipped_count += 1
                return result

            latest = self.price_repo.get_latest_price_date(symbol)
            end_date = date.today()
            if latest is None or mode == "initial":
                start_date = end_date - timedelta(days=backfill_days or self.INITIAL_BACKFILL_DAYS)
            else:
                start_date = min(latest - timedelta(days=self.RECENT_OVERWRITE_DAYS), end_date)

            records = self.price_client.fetch_daily_prices(
                symbol,
                start_date=start_date,
                end_date=end_date,
                market=ticker.market,
            )
            result.fetched_count = len(records)
            rows = self._build_price_rows(symbol, records)
            inserted, updated = self.price_repo.upsert_many(rows)
            result.inserted_count = inserted
            result.updated_count = updated
            self.session.commit()

            indicator_count = self.sync_technical_indicators_for_ticker(symbol)
            result.indicators_count = indicator_count
            self._sync_latest_valuation_metrics(symbol, market=ticker.market, end_date=end_date)

            result.trimmed_price_rows = self.price_repo.trim_rows_for_symbol(symbol, self.MAX_CACHE_ROWS)
            result.trimmed_indicator_rows = self.indicator_repo.trim_rows_for_symbol(symbol, self.MAX_CACHE_ROWS)
            self.session.commit()
            self._set_last_sync(symbol, started)
        finally:
            self._release_lock(symbol, lock_token)
            result.elapsed_ms = int((datetime.now(UTC) - started).total_seconds() * 1000)
        return result

    def sync_technical_indicators_for_ticker(self, symbol: str) -> int:
        price_rows = list(
            self.session.scalars(
                select(PriceCache).where(PriceCache.symbol == symbol).order_by(PriceCache.price_date.desc()).limit(self.MAX_CACHE_ROWS)
            )
        )
        indicator_rows = build_indicator_rows(symbol, price_rows)
        return self.indicator_repo.upsert_many(indicator_rows)

    def _build_price_rows(self, symbol: str, records: list[DailyPriceRecord]) -> list[dict]:
        rows: list[dict] = []
        previous_close: float | None = None
        for record in sorted(records, key=lambda item: item.price_date):
            change_rate = None
            if previous_close not in (None, 0):
                change_rate = ((record.close_price - previous_close) / previous_close) * 100
            rows.append(
                {
                    "symbol": symbol,
                    "price_date": record.price_date,
                    "open_price": record.open_price,
                    "high_price": record.high_price,
                    "low_price": record.low_price,
                    "close_price": record.close_price,
                    "adjusted_close": None,
                    "volume": record.volume,
                    "change_rate": change_rate,
                    "retrieved_at": datetime.now(UTC),
                }
            )
            previous_close = record.close_price
        return rows

    def _sync_latest_valuation_metrics(self, symbol: str, *, market, end_date: date) -> None:
        try:
            fundamental = self.price_client.fetch_latest_market_fundamental(
                symbol,
                end_date=end_date,
                market=market,
            )
            if fundamental is None:
                return

            latest_price_row = self.price_repo.list_recent(symbol, limit=1)
            if not latest_price_row:
                return

            close_price = float(latest_price_row[0].close_price)
            per = normalize_positive_ratio(fundamental.per)
            if per is None:
                per = compute_per(close_price, fundamental.eps)

            pbr = normalize_positive_ratio(fundamental.pbr)
            if pbr is None:
                pbr = compute_pbr(close_price, fundamental.bps)

            self.financial_repo.update_latest_valuation(symbol, per=per, pbr=pbr)
        except Exception:
            logger.exception("price valuation sync failed for %s", symbol)

    def _lock_key(self, symbol: str) -> str:
        return make_key("price-sync", "lock", symbol)

    def _last_sync_key(self, symbol: str) -> str:
        return make_key("price-sync", "last-sync", symbol)

    def _acquire_lock(self, symbol: str) -> str | None:
        if self.redis_client is None:
            return None
        token = f"{symbol}:{datetime.now(UTC).timestamp()}"
        try:
            acquired = self.redis_client.set(self._lock_key(symbol), token, nx=True, ex=self.LOCK_TTL_SECONDS)
            return token if acquired else None
        except Exception:
            logger.exception("price lock error for %s", symbol)
            return None

    def _release_lock(self, symbol: str, token: str | None) -> None:
        if self.redis_client is None or token is None:
            return
        try:
            if self.redis_client.get(self._lock_key(symbol)) == token:
                self.redis_client.delete(self._lock_key(symbol))
        except Exception:
            logger.exception("price unlock error for %s", symbol)

    def _is_within_cooldown(self, symbol: str) -> bool:
        if self.redis_client is None:
            return False
        try:
            raw = self.redis_client.get(self._last_sync_key(symbol))
        except Exception:
            return False
        if not raw:
            return False
        try:
            last_sync = datetime.fromtimestamp(float(raw), UTC)
        except (TypeError, ValueError):
            return False
        return last_sync >= datetime.now(UTC) - timedelta(minutes=self.COOL_DOWN_MINUTES)

    def _set_last_sync(self, symbol: str, value: datetime) -> None:
        if self.redis_client is None:
            return
        self.redis_client.set(self._last_sync_key(symbol), value.timestamp(), ex=86400)
