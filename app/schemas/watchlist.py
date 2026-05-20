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
