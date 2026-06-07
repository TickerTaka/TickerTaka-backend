from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.domain.filing_ingestion import FilingIngestionService


def _service() -> FilingIngestionService:
    service = FilingIngestionService.__new__(FilingIngestionService)
    service.settings = SimpleNamespace(
        filing_initial_lookback_days=365,
        filing_refresh_lookback_days=30,
    )
    return service


def test_initial_sync_uses_long_backfill_window() -> None:
    service = _service()

    assert service._resolve_lookback_days(mode="initial", lookback_days=None) == 365


def test_refresh_sync_uses_short_window() -> None:
    service = _service()

    assert service._resolve_lookback_days(mode="refresh", lookback_days=None) == 30


def test_explicit_lookback_overrides_mode_default() -> None:
    service = _service()

    assert service._resolve_lookback_days(mode="refresh", lookback_days=90) == 90


def test_invalid_sync_mode_is_rejected() -> None:
    service = _service()

    with pytest.raises(ValueError, match="unsupported filing sync mode"):
        service._resolve_lookback_days(mode="unknown", lookback_days=None)


def test_non_positive_lookback_is_rejected() -> None:
    service = _service()

    with pytest.raises(ValueError, match="lookback_days must be at least 1"):
        service._resolve_lookback_days(mode="initial", lookback_days=0)
