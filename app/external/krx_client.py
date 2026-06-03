from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from app.models import MarketType


@dataclass(slots=True)
class DailyPriceRecord:
    symbol: str
    price_date: date
    open_price: float | None
    high_price: float | None
    low_price: float | None
    close_price: float
    volume: int | None


@dataclass(slots=True)
class MarketFundamentalRecord:
    symbol: str
    price_date: date
    eps: float | None
    bps: float | None
    per: float | None
    pbr: float | None


@dataclass(slots=True)
class MarketCapRecord:
    symbol: str
    price_date: date
    close_price: float | None
    market_cap: float | None
    listed_shares: int | None


class PyKrxClient:
    """Thin wrapper around pykrx for daily OHLCV history."""

    SUPPORTED_MARKETS = {MarketType.KOSPI, MarketType.KOSDAQ}

    def fetch_daily_prices(
        self,
        symbol: str,
        *,
        start_date: date,
        end_date: date,
        market: MarketType,
    ) -> list[DailyPriceRecord]:
        if market not in self.SUPPORTED_MARKETS:
            raise ValueError(f"pykrx unsupported market: {market}")

        try:
            from pykrx import stock
        except ImportError as exc:  # pragma: no cover - depends on local env
            raise RuntimeError("pykrx is required for price cache ingestion") from exc

        frame = stock.get_market_ohlcv_by_date(
            fromdate=start_date.strftime("%Y%m%d"),
            todate=end_date.strftime("%Y%m%d"),
            ticker=symbol,
        )
        if frame.empty:
            return []

        records: list[DailyPriceRecord] = []
        for index, row in frame.iterrows():
            records.append(
                DailyPriceRecord(
                    symbol=symbol,
                    price_date=index.date(),
                    open_price=float(row["시가"]) if row.get("시가") is not None else None,
                    high_price=float(row["고가"]) if row.get("고가") is not None else None,
                    low_price=float(row["저가"]) if row.get("저가") is not None else None,
                    close_price=float(row["종가"]),
                    volume=int(row["거래량"]) if row.get("거래량") is not None else None,
                )
            )
        return records

    def fetch_latest_market_fundamental(
        self,
        symbol: str,
        *,
        end_date: date,
        market: MarketType,
        lookback_days: int = 30,
    ) -> MarketFundamentalRecord | None:
        if market not in self.SUPPORTED_MARKETS:
            raise ValueError(f"pykrx unsupported market: {market}")

        try:
            from pykrx import stock
        except ImportError as exc:  # pragma: no cover - depends on local env
            raise RuntimeError("pykrx is required for price cache ingestion") from exc

        start_date = max(date(2000, 1, 1), end_date.fromordinal(end_date.toordinal() - lookback_days))
        frame = stock.get_market_fundamental_by_date(
            fromdate=start_date.strftime("%Y%m%d"),
            todate=end_date.strftime("%Y%m%d"),
            ticker=symbol,
        )
        if not frame.empty:
            latest_index = frame.index[-1]
            row = frame.iloc[-1]
            return MarketFundamentalRecord(
                symbol=symbol,
                price_date=latest_index.date(),
                eps=_to_optional_float(row.get("EPS")),
                bps=_to_optional_float(row.get("BPS")),
                per=_to_optional_float(row.get("PER")),
                pbr=_to_optional_float(row.get("PBR")),
            )

        market_name = market.value if hasattr(market, "value") else str(market)
        market_candidates = [market_name]
        if "ALL" not in market_candidates:
            market_candidates.append("ALL")

        for offset in range(lookback_days + 1):
            probe_date = end_date.fromordinal(end_date.toordinal() - offset)
            for market_candidate in market_candidates:
                try:
                    frame = stock.get_market_fundamental_by_ticker(
                        date=probe_date.strftime("%Y%m%d"),
                        market=market_candidate,
                    )
                except KeyError:
                    continue
                if frame.empty or symbol not in frame.index:
                    continue
                row = frame.loc[symbol]
                return MarketFundamentalRecord(
                    symbol=symbol,
                    price_date=probe_date,
                    eps=_to_optional_float(row.get("EPS")),
                    bps=_to_optional_float(row.get("BPS")),
                    per=_to_optional_float(row.get("PER")),
                    pbr=_to_optional_float(row.get("PBR")),
                )
        return None

    def fetch_latest_market_cap(
        self,
        symbol: str,
        *,
        end_date: date,
        market: MarketType,
        lookback_days: int = 30,
    ) -> MarketCapRecord | None:
        if market not in self.SUPPORTED_MARKETS:
            raise ValueError(f"pykrx unsupported market: {market}")

        try:
            from pykrx import stock
        except ImportError as exc:  # pragma: no cover - depends on local env
            raise RuntimeError("pykrx is required for price cache ingestion") from exc

        start_date = max(date(2000, 1, 1), end_date.fromordinal(end_date.toordinal() - lookback_days))
        frame = stock.get_market_cap_by_date(
            fromdate=start_date.strftime("%Y%m%d"),
            todate=end_date.strftime("%Y%m%d"),
            ticker=symbol,
        )
        if not frame.empty:
            latest_index = frame.index[-1]
            row = frame.iloc[-1]
            return MarketCapRecord(
                symbol=symbol,
                price_date=latest_index.date(),
                close_price=_to_optional_float(row.get("종가")),
                market_cap=_to_optional_float(row.get("시가총액")),
                listed_shares=_to_optional_int(row.get("상장주식수")),
            )

        market_name = market.value if hasattr(market, "value") else str(market)
        market_candidates = [market_name]
        if "ALL" not in market_candidates:
            market_candidates.append("ALL")

        for offset in range(lookback_days + 1):
            probe_date = end_date.fromordinal(end_date.toordinal() - offset)
            for market_candidate in market_candidates:
                try:
                    frame = stock.get_market_cap_by_ticker(
                        date=probe_date.strftime("%Y%m%d"),
                        market=market_candidate,
                    )
                except KeyError:
                    continue
                if frame.empty or symbol not in frame.index:
                    continue
                row = frame.loc[symbol]
                return MarketCapRecord(
                    symbol=symbol,
                    price_date=probe_date,
                    close_price=_to_optional_float(row.get("종가")),
                    market_cap=_to_optional_float(row.get("시가총액")),
                    listed_shares=_to_optional_int(row.get("상장주식수")),
                )
        return None


def _to_optional_float(value) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except Exception:
        return None


def _to_optional_int(value) -> int | None:
    try:
        if value is None:
            return None
        return int(value)
    except Exception:
        return None
