from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class WatchlistCreateRequest(BaseModel):
    user_id: UUID
    symbol: str = Field(min_length=1, max_length=30)
    memo: str | None = Field(default=None, max_length=1000)


class WatchlistItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    user_id: UUID
    symbol: str
    memo: str | None
    created_at: datetime
    ticker_name_kr: str | None = None


class WatchlistCreateResponse(BaseModel):
    watchlist: WatchlistItemResponse
    sync_enqueued: bool = True


class WatchlistListResponse(BaseModel):
    items: list[WatchlistItemResponse]


class WatchlistRefreshResponse(BaseModel):
    """새로고침 버튼 응답. 무거운 재수집(뉴스/공시/재무/가격/평가)을 백그라운드로 큐잉한 뒤
    즉시 반환한다(비차단). `symbols`는 이번에 재수집을 시작한 종목,
    `skipped`는 throttle 윈도우(최근 N분) 안이라 건너뛴 종목이다.
    """

    status: str = Field(default="refreshing", examples=["refreshing"])
    symbols: list[str] = Field(default_factory=list, description="재수집을 시작한 종목들")
    skipped: list[str] = Field(default_factory=list, description="throttle로 건너뛴 종목들")
