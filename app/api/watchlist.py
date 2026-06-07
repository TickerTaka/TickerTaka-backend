from __future__ import annotations

import logging
from datetime import datetime, timezone
from uuid import UUID

_MIN_DT = datetime.min.replace(tzinfo=timezone.utc)

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from sqlalchemy import select

from app.core.db import get_db
from app.domain.watchlist_service import (
    TickerNotFoundError,
    UserNotFoundError,
    WatchlistAlreadyExistsError,
    WatchlistNotFoundError,
    WatchlistService,
    sync_watchlist_filings,
    sync_watchlist_financials,
    sync_watchlist_news,
    sync_watchlist_prices,
    sync_watchlist_valuation,
)
from app.models import EvidenceAnalysis, FilingCache, NewsCache, TickerMetadata
from app.repositories.evidence_analysis_repository import EvidenceAnalysisRepository
from app.schemas.market_data import WatchlistFeedItem, WatchlistFeedResponse
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
        background_tasks.add_task(sync_watchlist_financials, watchlist.symbol)
        background_tasks.add_task(sync_watchlist_prices, watchlist.symbol)
        background_tasks.add_task(sync_watchlist_filings, watchlist.symbol)
        background_tasks.add_task(sync_watchlist_valuation, watchlist.symbol)
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


@router.get(
    "/{user_id}/feed",
    response_model=WatchlistFeedResponse,
    summary="관심종목 뉴스+공시 피드 (감성분석 포함)",
    description=(
        "사용자 관심종목들의 뉴스와 공시를 발행일 내림차순으로 합쳐 내려준다.\n\n"
        "각 항목에는 감성분석(`evidence_analysis`) 결과가 함께 실린다:\n"
        "- `sentiment`: positive / negative / neutral / mixed (방향)\n"
        "- `impact_score`: -2(강한 악재) ~ +2(강한 호재) (강도)\n"
        "- `confidence`, `analysis_summary`, `key_points`, `risks`\n\n"
        "아직 분석되지 않은 항목은 위 필드가 모두 null/빈 배열이다. "
        "프론트는 `sentiment`로 색, `impact_score`로 강도를 표시하면 된다."
    ),
)
def get_watchlist_feed(
    user_id: UUID,
    limit: int = 20,
    db: Session = Depends(get_db),
) -> WatchlistFeedResponse:
    """관심종목 뉴스+공시 통합 피드. 항목별 감성분석(sentiment/impact_score 등)을 join해 반환한다."""
    service = WatchlistService(db)
    try:
        items = service.list_watchlists(user_id)
    except UserNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    symbols = [w.symbol for w in items]
    if not symbols:
        return WatchlistFeedResponse(items=[])

    name_by_symbol = {
        row.symbol: row.name_kr
        for row in db.scalars(select(TickerMetadata).where(TickerMetadata.symbol.in_(symbols)))
    }
    capped = max(1, min(limit, 100))

    news_rows = list(
        db.scalars(
            select(NewsCache)
            .where(NewsCache.symbol.in_(symbols))
            .order_by(NewsCache.published_at.desc().nullslast(), NewsCache.retrieved_at.desc())
            .limit(capped)
        )
    )
    filing_rows = list(
        db.scalars(
            select(FilingCache)
            .where(FilingCache.symbol.in_(symbols))
            .order_by(FilingCache.disclosed_at.desc().nullslast(), FilingCache.retrieved_at.desc())
            .limit(capped)
        )
    )

    # 감성분석(evidence_analysis) 결과를 source_id 로 join
    analysis_repo = EvidenceAnalysisRepository(db)
    news_analysis = analysis_repo.get_by_sources("news", [n.id for n in news_rows])
    filing_analysis = analysis_repo.get_by_sources("filing", [f.id for f in filing_rows])

    def _analysis_fields(analysis: EvidenceAnalysis | None) -> dict:
        if analysis is None:
            return {}
        return {
            "sentiment": analysis.sentiment,
            "impact_score": analysis.impact_score,
            "confidence": float(analysis.confidence) if analysis.confidence is not None else None,
            "analysis_summary": analysis.summary,
            "key_points": list(analysis.key_points or []),
            "risks": list(analysis.risks or []),
        }

    feed: list[WatchlistFeedItem] = []
    for n in news_rows:
        feed.append(
            WatchlistFeedItem(
                id=n.id,
                symbol=n.symbol,
                symbol_name=name_by_symbol.get(n.symbol),
                kind="news",
                title=n.title,
                summary=n.summary,
                source_name=n.source_name,
                source_url=n.source_url,
                published_at=n.published_at or n.retrieved_at,
                **_analysis_fields(news_analysis.get(str(n.id))),
            )
        )
    for f in filing_rows:
        feed.append(
            WatchlistFeedItem(
                id=f.id,
                symbol=f.symbol,
                symbol_name=name_by_symbol.get(f.symbol),
                kind="filing",
                title=f.filing_title,
                summary=f.summary,
                source_name="DART",
                source_url=f.source_url,
                published_at=f.disclosed_at or f.retrieved_at,
                **_analysis_fields(filing_analysis.get(str(f.id))),
            )
        )
    feed.sort(key=lambda it: it.published_at or _MIN_DT, reverse=True)
    return WatchlistFeedResponse(items=feed[:capped])


@router.delete("/{user_id}/{symbol}")
def delete_watchlist(user_id: UUID, symbol: str, db: Session = Depends(get_db)) -> dict[str, str]:
    service = WatchlistService(db)
    try:
        service.delete_watchlist(user_id, symbol)
        db.commit()
    except UserNotFoundError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except WatchlistNotFoundError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return {"status": "deleted", "user_id": str(user_id), "symbol": symbol}
