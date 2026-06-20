"""TickerTaka MCP 서버 — 도메인 기능을 MCP tool 로 노출 (평가 항목6 서버측).

설계 원칙: **기존 FastAPI route 함수를 `db=<session>` 으로 직접 호출**해 로직을 100%
재사용한다(중복 0, production 코드 무수정 → 회귀 risk 0). 우리는 Notion MCP 의 클라이언트
이자, 이 서버로 **도구 제공자**가 되어 양방향을 이룬다.

기동(stdio transport):
    python -m app.mcp_server

Claude Desktop 등 MCP 클라이언트가 위 명령을 spawn 해 tool 을 호출한다.

⚠️ 데이터 제약: 토론/조회는 **이미 수집·인덱싱된 종목**에서만 의미가 있다.
   먼저 `list_available_symbols` 로 가능한 종목을 확인할 것.
"""
from __future__ import annotations

from uuid import UUID

from mcp.server.fastmcp import FastMCP

from app.core.db import session_scope

mcp = FastMCP("tickertaka")


def _err(exc: Exception) -> dict:
    from fastapi import HTTPException

    if isinstance(exc, HTTPException):
        return {"error": str(exc.detail)}
    return {"error": f"{type(exc).__name__}: {exc}"}


@mcp.tool()
def list_available_symbols() -> dict:
    """토론·조회 가능한 종목 목록(=news/filing 데이터가 수집된 종목). 다른 tool 의 symbol 은 여기서 고른다."""
    from sqlalchemy import distinct, select

    from app.models import FilingCache, NewsCache, TickerMetadata

    with session_scope() as s:
        syms = set(s.scalars(select(distinct(NewsCache.symbol)))) | set(
            s.scalars(select(distinct(FilingCache.symbol)))
        )
        names = (
            dict(
                s.execute(
                    select(TickerMetadata.symbol, TickerMetadata.name_kr).where(
                        TickerMetadata.symbol.in_(syms)
                    )
                ).all()
            )
            if syms
            else {}
        )
    return {
        "count": len(syms),
        "symbols": [{"symbol": x, "name_kr": names.get(x)} for x in sorted(syms)],
    }


@mcp.tool()
def get_stock_detail(symbol: str) -> dict:
    """종목의 최신 가격·재무·기술지표 스냅샷."""
    from app.api.market_data import get_stock_detail as _impl

    with session_scope() as s:
        try:
            return _impl(symbol, db=s).model_dump(mode="json")
        except Exception as exc:  # noqa: BLE001
            return _err(exc)


@mcp.tool()
def get_watchlist_feed(user_id: str, limit: int = 20) -> dict:
    """관심종목 뉴스/공시 + 감성분석(sentiment/impact_score/key_points/risks) 통합 피드."""
    from app.api.watchlist import get_watchlist_feed as _impl

    with session_scope() as s:
        try:
            return _impl(user_id=UUID(user_id), limit=limit, db=s).model_dump(mode="json")
        except Exception as exc:  # noqa: BLE001
            return _err(exc)


@mcp.tool()
def list_debates(user_id: str = "", symbol: str = "", limit: int = 20) -> dict:
    """토론 세션 목록(요약 메타). user_id/symbol 로 필터(빈 값이면 전체)."""
    from app.api.debate import list_debates as _impl

    with session_scope() as s:
        try:
            uid = UUID(user_id) if user_id else None
            return _impl(user_id=uid, symbol=symbol or None, limit=limit, db=s).model_dump(mode="json")
        except Exception as exc:  # noqa: BLE001
            return _err(exc)


@mcp.tool()
def get_debate(session_id: str) -> dict:
    """개별 토론 결과(발언 statements + moderator 요약)."""
    from app.api.debate import get_debate as _impl

    with session_scope() as s:
        try:
            return _impl(UUID(session_id), db=s).model_dump(mode="json")
        except Exception as exc:  # noqa: BLE001
            return _err(exc)


@mcp.tool()
async def start_debate(user_id: str, symbol: str, category: str = "financial") -> dict:
    """새 토론을 실행하고 결과를 반환한다.

    ⚠️ 데이터가 수집된 종목(list_available_symbols)만 의미 있음. LLM 다회 호출로 수십 초 소요.
    """
    from app.api.debate import create_debate
    from app.schemas.debate import DebateCreateRequest

    with session_scope() as s:
        try:
            payload = DebateCreateRequest(user_id=UUID(user_id), symbol=symbol, category=category)
            resp = await create_debate(payload, db=s)
            return resp.model_dump(mode="json")
        except Exception as exc:  # noqa: BLE001
            return _err(exc)


@mcp.tool()
def add_watchlist(user_id: str, symbol: str, memo: str = "") -> dict:
    """관심종목 추가 + 데이터 수집(뉴스/재무/가격/공시 인덱싱).

    ⚠️ 외부 API fetch + 벡터 인덱싱이라 수십 초~분 소요. 추가·수집 후에야 그 종목으로
    토론(start_debate)·피드(get_watchlist_feed)가 의미 있다. 이미 담긴 종목이면 데이터만 갱신.
    """
    from app.domain.watchlist_service import (
        TickerNotFoundError,
        UserNotFoundError,
        WatchlistAlreadyExistsError,
        WatchlistService,
        sync_watchlist_filings,
        sync_watchlist_financials,
        sync_watchlist_news,
        sync_watchlist_prices,
        sync_watchlist_valuation,
    )

    already = False
    with session_scope() as s:
        try:
            WatchlistService(s).create_watchlist(UUID(user_id), symbol, memo or None)
            s.commit()
        except WatchlistAlreadyExistsError:
            already = True
        except (UserNotFoundError, TickerNotFoundError) as exc:
            return _err(exc)
        except Exception as exc:  # noqa: BLE001
            return _err(exc)

    # 데이터 수집(인라인, 느림). 각 sync 함수는 자체 세션을 연다. 실패는 항목별로 격리.
    synced: list[str] = []
    for fn in (
        sync_watchlist_news,
        sync_watchlist_financials,
        sync_watchlist_prices,
        sync_watchlist_filings,
        sync_watchlist_valuation,
    ):
        try:
            fn(symbol)
            synced.append(fn.__name__)
        except Exception as exc:  # noqa: BLE001
            synced.append(f"{fn.__name__}: FAIL({type(exc).__name__})")
    return {
        "symbol": symbol,
        "already_in_watchlist": already,
        "synced": synced,
        "note": "수집 완료 — 이제 get_watchlist_feed / start_debate 사용 가능",
    }


if __name__ == "__main__":
    mcp.run()
