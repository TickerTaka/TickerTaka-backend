from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import logging

from app.config import get_settings
from app.core.redis import build_redis_client, make_key
from app.domain.financial_ratios import compute_debt_ratio, compute_roe
from app.external.dart import CorpCodeProvider, DartClient
from app.models import FinancialCache, TickerMetadata
from app.repositories.financial_cache_repository import FinancialCacheRepository

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class SyncFinancialResult:
    fetched_periods: int = 0
    saved_rows: int = 0
    skipped_count: int = 0
    trimmed_rows: int = 0
    elapsed_ms: int = 0


class FinancialIngestionService:
    INITIAL_BACKFILL_YEARS = 5
    MAX_CACHE_ROWS = 60
    COOLDOWN_HOURS = 6
    LOCK_TTL_SECONDS = 300

    def __init__(
        self,
        session,
        *,
        dart_client: DartClient | None = None,
        corp_code_provider: CorpCodeProvider | None = None,
        redis_client=None,
    ) -> None:
        self.session = session
        self.repo = FinancialCacheRepository(session)
        self.dart_client = dart_client or DartClient()
        self.corp_code_provider = corp_code_provider or CorpCodeProvider()
        self.redis_client = redis_client or build_redis_client(get_settings().redis_url)

    def sync_financials_for_ticker(
        self,
        symbol: str,
        mode: str = "initial",
        force: bool = False,
        backfill_years: int | None = None,
    ) -> SyncFinancialResult:
        started = datetime.now(UTC)
        result = SyncFinancialResult()
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
            corp_code = self.corp_code_provider.get_corp_code(symbol)
            if not corp_code:
                raise ValueError(f"corp code not found for symbol: {symbol}")

            current_year = datetime.now(UTC).year
            years = list(range(current_year - (backfill_years or self.INITIAL_BACKFILL_YEARS) + 1, current_year + 1))
            records = self.dart_client.fetch_financials(corp_code=corp_code, years=years)
            result.fetched_periods = len(records)

            rows = []
            retrieved_at = datetime.now(UTC)
            for record in records:
                # PER/PBR are price-dependent valuation metrics. We keep them
                # out of the initial financial cache sync and leave them NULL
                # until a later price-joined calculation policy is finalized.
                rows.append(
                    {
                        "symbol": symbol,
                        "fiscal_year": record.fiscal_year,
                        "fiscal_quarter": record.fiscal_quarter,
                        "revenue": record.revenue,
                        "operating_profit": record.operating_profit,
                        "net_income": record.net_income,
                        "total_assets": record.total_assets,
                        "total_liabilities": record.total_liabilities,
                        "total_equity": record.total_equity,
                        "per": None,
                        "pbr": None,
                        "roe": compute_roe(record.net_income, record.total_equity),
                        "debt_ratio": compute_debt_ratio(record.total_liabilities, record.total_equity),
                        "source_url": record.source_url,
                        "retrieved_at": retrieved_at,
                    }
                )
            result.saved_rows = self.repo.upsert_many(rows)
            result.trimmed_rows = self.repo.trim_rows_for_symbol(symbol, self.MAX_CACHE_ROWS)
            self._set_last_sync(symbol, started)
            self.session.flush()
        finally:
            self._release_lock(symbol, lock_token)
            result.elapsed_ms = int((datetime.now(UTC) - started).total_seconds() * 1000)
        return result

    def _lock_key(self, symbol: str) -> str:
        return make_key("financial-sync", "lock", symbol)

    def _last_sync_key(self, symbol: str) -> str:
        return make_key("financial-sync", "last-sync", symbol)

    def _acquire_lock(self, symbol: str) -> str | None:
        if self.redis_client is None:
            return None
        token = f"{symbol}:{datetime.now(UTC).timestamp()}"
        try:
            acquired = self.redis_client.set(self._lock_key(symbol), token, nx=True, ex=self.LOCK_TTL_SECONDS)
            return token if acquired else None
        except Exception:
            logger.exception("financial lock error for %s", symbol)
            return None

    def _release_lock(self, symbol: str, token: str | None) -> None:
        if self.redis_client is None or token is None:
            return
        try:
            if self.redis_client.get(self._lock_key(symbol)) == token:
                self.redis_client.delete(self._lock_key(symbol))
        except Exception:
            logger.exception("financial unlock error for %s", symbol)

    def _is_within_cooldown(self, symbol: str) -> bool:
        if self.redis_client is None:
            return False
        raw = self.redis_client.get(self._last_sync_key(symbol))
        if not raw:
            return False
        try:
            last_sync = datetime.fromtimestamp(float(raw), UTC)
        except (TypeError, ValueError):
            return False
        return last_sync >= datetime.now(UTC) - timedelta(hours=self.COOLDOWN_HOURS)

    def _set_last_sync(self, symbol: str, value: datetime) -> None:
        if self.redis_client is None:
            return
        self.redis_client.set(self._last_sync_key(symbol), value.timestamp(), ex=86400 * 7)
