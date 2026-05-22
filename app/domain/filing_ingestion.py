from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import logging
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.external.dart import DartClient, DartFilingItem
from app.models import TickerMetadata
from app.repositories.filing_cache_repository import FilingCacheRepository

logger = logging.getLogger(__name__)
KST = ZoneInfo("Asia/Seoul")
UTC = timezone.utc


@dataclass
class SyncFilingResult:
    fetched_count: int = 0
    inserted_count: int = 0
    updated_count: int = 0
    skipped_count: int = 0
    elapsed_ms: int = 0


class FilingIngestionService:
    """Loads DART filing metadata into filing_cache for watchlist symbols."""

    LOOKBACK_DAYS = 30
    CACHE_TTL_DAYS = 7
    PAGE_COUNT = 100

    def __init__(
        self,
        session: Session,
        *,
        dart_client: DartClient | None = None,
    ) -> None:
        self.session = session
        self.repo = FilingCacheRepository(session)
        self.dart_client = dart_client or DartClient()

    def sync_filings_for_ticker(
        self,
        symbol: str,
        *,
        lookback_days: int | None = None,
        limit: int | None = None,
    ) -> SyncFilingResult:
        started = datetime.now(UTC)
        result = SyncFilingResult()

        ticker = self.session.get(TickerMetadata, symbol)
        if ticker is None:
            raise ValueError(f"ticker not found: {symbol}")

        normalized_symbol = symbol.strip()
        corp_code = self.dart_client.get_corp_code_by_stock_code(normalized_symbol)
        if corp_code is None:
            logger.info("filing sync skipped: DART corp_code not found for %s", normalized_symbol)
            result.skipped_count += 1
            result.elapsed_ms = self._elapsed_ms(started)
            return result

        today = datetime.now(KST).date()
        begin_date = today - timedelta(days=lookback_days or self.LOOKBACK_DAYS)
        items = self.dart_client.list_filings(
            corp_code.corp_code,
            begin_date=begin_date,
            end_date=today,
            page_count=limit or self.PAGE_COUNT,
        )
        result.fetched_count = len(items)

        valid_items = [item for item in items if self._is_valid_item(item)]
        result.skipped_count += len(items) - len(valid_items)
        receipt_nos = [item.receipt_no for item in valid_items]
        existing = self.repo.get_by_receipt_nos(receipt_nos)
        ttl_until = datetime.now(UTC) + timedelta(days=self.CACHE_TTL_DAYS)

        for item in valid_items:
            disclosed_at = self._parse_receipt_date(item.receipt_date)
            self.repo.upsert_filing(
                symbol=item.stock_code or normalized_symbol,
                filing_title=item.report_name,
                filing_type=item.filing_type,
                dart_receipt_no=item.receipt_no,
                source_url=self.dart_client.build_viewer_url(item.receipt_no),
                disclosed_at=disclosed_at,
                ttl_until=ttl_until,
            )
            if item.receipt_no in existing:
                result.updated_count += 1
            else:
                result.inserted_count += 1

        self.session.flush()
        result.elapsed_ms = self._elapsed_ms(started)
        logger.info(
            "filing sync finished",
            extra={
                "symbol": normalized_symbol,
                "corp_code": corp_code.corp_code,
                "fetched": result.fetched_count,
                "inserted": result.inserted_count,
                "updated": result.updated_count,
                "skipped": result.skipped_count,
                "elapsed_ms": result.elapsed_ms,
            },
        )
        return result

    @staticmethod
    def _is_valid_item(item: DartFilingItem) -> bool:
        return bool(item.receipt_no and item.report_name)

    @staticmethod
    def _parse_receipt_date(value: str) -> datetime | None:
        if not value:
            return None
        try:
            parsed = datetime.strptime(value, "%Y%m%d")
        except ValueError:
            logger.warning("invalid DART receipt date: %s", value)
            return None
        return parsed.replace(tzinfo=KST)

    @staticmethod
    def _elapsed_ms(started: datetime) -> int:
        return int((datetime.now(UTC) - started).total_seconds() * 1000)
