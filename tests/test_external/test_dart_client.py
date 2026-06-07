from __future__ import annotations

from datetime import date
from types import SimpleNamespace

from app.external.dart.client import DartClient


class FakeResponse:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def json(self) -> dict:
        return self.payload


def _filing(receipt_no: str) -> dict[str, str]:
    return {
        "corp_code": "00126380",
        "corp_name": "테스트기업",
        "stock_code": "000000",
        "report_nm": f"공시 {receipt_no}",
        "rcept_no": receipt_no,
        "rcept_dt": "20260607",
        "pblntf_ty": "B",
    }


def _client(pages: dict[int, dict], calls: list[int]) -> DartClient:
    client = DartClient.__new__(DartClient)
    client.settings = SimpleNamespace(dart_api_key="test-key")

    def fake_get(url: str, *, params: dict, timeout: int = 30) -> FakeResponse:
        calls.append(params["page_no"])
        return FakeResponse(pages[params["page_no"]])

    client._get = fake_get
    return client


def test_list_filings_reads_all_pages() -> None:
    calls: list[int] = []
    client = _client(
        {
            1: {"status": "000", "total_page": 2, "list": [_filing("1"), _filing("2")]},
            2: {"status": "000", "total_page": 2, "list": [_filing("3")]},
        },
        calls,
    )

    results = client.list_filings(
        "00126380",
        begin_date=date(2025, 6, 7),
        end_date=date(2026, 6, 7),
        page_count=2,
    )

    assert [item.receipt_no for item in results] == ["1", "2", "3"]
    assert calls == [1, 2]


def test_list_filings_stops_at_max_items() -> None:
    calls: list[int] = []
    client = _client(
        {
            1: {"status": "000", "total_page": 3, "list": [_filing("1"), _filing("2")]},
            2: {"status": "000", "total_page": 3, "list": [_filing("3"), _filing("4")]},
            3: {"status": "000", "total_page": 3, "list": [_filing("5")]},
        },
        calls,
    )

    results = client.list_filings(
        "00126380",
        begin_date=date(2025, 6, 7),
        end_date=date(2026, 6, 7),
        page_count=2,
        max_items=3,
    )

    assert [item.receipt_no for item in results] == ["1", "2", "3"]
    assert calls == [1, 2]
