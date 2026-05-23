from __future__ import annotations

import logging
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.domain.watchlist_service import (
    TickerNotFoundError,
    UserNotFoundError,
    WatchlistAlreadyExistsError,
    WatchlistService,
    sync_watchlist_financials,
    sync_watchlist_news,
    sync_watchlist_prices,
)
from app.schemas.watchlist import (
    WatchlistCreateRequest,
    WatchlistCreateResponse,
    WatchlistItemResponse,
    WatchlistListResponse,
)

router = APIRouter(prefix="/api/watchlists", tags=["watchlists"])
logger = logging.getLogger(__name__)


def _to_item_response(item) -> WatchlistItemResponse:
    return WatchlistItemResponse(
        id=item.id,
        user_id=item.user_id,
        symbol=item.symbol,
        memo=item.memo,
        created_at=item.created_at,
        ticker_name_kr=item.ticker.name_kr if getattr(item, "ticker", None) else None,
    )


@router.post("", response_model=WatchlistCreateResponse, status_code=status.HTTP_201_CREATED)
def create_watchlist(
    payload: WatchlistCreateRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
) -> WatchlistCreateResponse:
    service = WatchlistService(db)
    sync_enqueued = False
    try:
        watchlist = service.create_watchlist(payload.user_id, payload.symbol, payload.memo)
        db.commit()
    except UserNotFoundError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except TickerNotFoundError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except WatchlistAlreadyExistsError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"watchlist already exists: {payload.user_id}/{payload.symbol}",
        ) from exc

    try:
        background_tasks.add_task(sync_watchlist_news, watchlist.symbol)
        background_tasks.add_task(sync_watchlist_prices, watchlist.symbol)
        background_tasks.add_task(sync_watchlist_financials, watchlist.symbol)
        sync_enqueued = True
    except Exception:
        logger.exception("failed to enqueue watchlist sync for %s", watchlist.symbol)
        sync_enqueued = False

    return WatchlistCreateResponse(watchlist=_to_item_response(watchlist), sync_enqueued=sync_enqueued)


@router.get("/{user_id}", response_model=WatchlistListResponse)
def list_watchlists(user_id: UUID, db: Session = Depends(get_db)) -> WatchlistListResponse:
    service = WatchlistService(db)
    try:
        items = service.list_watchlists(user_id)
    except UserNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return WatchlistListResponse(items=[_to_item_response(item) for item in items])
