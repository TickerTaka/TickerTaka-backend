from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sse_starlette import EventSourceResponse
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session, selectinload

from app.config import get_settings
from app.core.db import get_db
from app.domain.debate_service import DebateExecutionService, DebateStartRejectedError
from app.integrations import NotionMcpClient, NotionMcpError
from app.models import AgentStatement, DebateSession, DebateStatus, ModeratorSummary, TickerMetadata
from app.schemas.debate import (
    DebateCreateRequest,
    DebatePrepareResponse,
    DebateNotionPublishResponse,
    DebateSessionResponse,
    DebateStatementResponse,
)
from app.schemas.market_data import DebateListItem, DebateListResponse

router = APIRouter(prefix="/api/debates", tags=["debates"])


@router.post("", response_model=DebateSessionResponse, status_code=status.HTTP_201_CREATED)
async def create_debate(payload: DebateCreateRequest, db: Session = Depends(get_db)) -> DebateSessionResponse:
    settings = get_settings()
    ticker = _get_ticker_or_404(db, payload.symbol)
    session_row = _create_session_row(db, payload, status=DebateStatus.RUNNING)

    try:
        await DebateExecutionService().run_session(
            session_id=str(session_row.id),
            user_id=str(payload.user_id),
            symbol=payload.symbol,
            symbol_name=ticker.name_kr,
            category=payload.category.value,
            estimated_tokens=settings.estimated_tokens_for_category(payload.category.value),
        )
    except DebateStartRejectedError as exc:
        db.delete(session_row)
        db.commit()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except Exception as exc:
        db.refresh(session_row)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "message": str(exc),
                "session_id": str(session_row.id),
                "status": "failed",
            },
        ) from exc

    return _build_session_response(db, session_row.id)


@router.post("/sessions", response_model=DebatePrepareResponse, status_code=status.HTTP_201_CREATED)
def prepare_debate_session(payload: DebateCreateRequest, db: Session = Depends(get_db)) -> DebatePrepareResponse:
    _get_ticker_or_404(db, payload.symbol)
    session_row = _create_session_row(db, payload, status=DebateStatus.PENDING)
    return DebatePrepareResponse(
        session_id=session_row.id,
        user_id=session_row.user_id,
        symbol=session_row.symbol,
        category=str(
            session_row.category.value
            if hasattr(session_row.category, "value")
            else session_row.category
        ),
        status=str(session_row.status.value if hasattr(session_row.status, "value") else session_row.status),
        started_at=session_row.started_at,
    )


@router.get("", response_model=DebateListResponse)
def list_debates(
    user_id: UUID | None = None,
    symbol: str | None = None,
    limit: int = 20,
    db: Session = Depends(get_db),
) -> DebateListResponse:
    limit = max(1, min(limit, 100))
    stmt = (
        select(DebateSession, TickerMetadata.name_kr, ModeratorSummary.summary_content)
        .join(TickerMetadata, TickerMetadata.symbol == DebateSession.symbol)
        .outerjoin(ModeratorSummary, ModeratorSummary.session_id == DebateSession.id)
    )
    if user_id is not None:
        stmt = stmt.where(DebateSession.user_id == user_id)
    if symbol:
        stmt = stmt.where(DebateSession.symbol == symbol)
    rows = db.execute(stmt.order_by(DebateSession.started_at.desc()).limit(limit)).all()
    items: list[DebateListItem] = []
    for session_row, symbol_name, summary_content in rows:
        items.append(
            DebateListItem(
                session_id=session_row.id,
                user_id=session_row.user_id,
                symbol=session_row.symbol,
                symbol_name=symbol_name,
                category=str(
                    session_row.category.value
                    if hasattr(session_row.category, "value")
                    else session_row.category
                ),
                status=str(session_row.status.value if hasattr(session_row.status, "value") else session_row.status),
                started_at=session_row.started_at,
                completed_at=session_row.completed_at,
                summary_content=summary_content,
            )
        )
    return DebateListResponse(items=items)


@router.get("/{session_id}", response_model=DebateSessionResponse)
def get_debate(session_id: UUID, db: Session = Depends(get_db)) -> DebateSessionResponse:
    row = db.get(DebateSession, session_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"debate session not found: {session_id}")
    return _build_session_response(db, session_id)


@router.get("/{session_id}/stream")
async def stream_debate(
    session_id: UUID,
    db: Session = Depends(get_db),
) -> EventSourceResponse:
    session_row = db.get(DebateSession, session_id)
    if session_row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"debate session not found: {session_id}")

    ticker = db.get(TickerMetadata, session_row.symbol)
    if ticker is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"ticker not found: {session_row.symbol}")

    settings = get_settings()
    session_id_str = str(session_row.id)
    user_id_str = str(session_row.user_id)
    symbol = session_row.symbol
    symbol_name = ticker.name_kr
    category = _enum_value(session_row.category)

    if session_row.status == DebateStatus.RUNNING:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="debate session is already running",
        )

    if session_row.status == DebateStatus.COMPLETED:
        summary_row = db.scalar(select(ModeratorSummary).where(ModeratorSummary.session_id == session_id))
        statement_rows = list(
            db.scalars(
                select(AgentStatement)
                .where(AgentStatement.session_id == session_id)
                .options(selectinload(AgentStatement.evidences))
                .order_by(AgentStatement.round_order)
            )
        )
        statement_payloads = [
            {
                "session_id": session_id_str,
                "agent_role": _enum_value(stmt.agent_role),
                "round": _enum_value(stmt.round),
                "round_order": stmt.round_order,
                "content": stmt.content,
                "model_used": stmt.model_name or "",
                "evidence_count": len(stmt.evidences or []),
            }
            for stmt in statement_rows
        ]
        summary_payload = (
            {
                "session_id": session_id_str,
                "summary_content": summary_row.summary_content,
                "key_points": summary_row.key_points or [],
            }
            if summary_row is not None
            else None
        )

        async def replay_events():
            yield _sse_event("session_started", {
                "session_id": session_id_str,
                "symbol": symbol,
                "symbol_name": symbol_name,
                "category": category,
                "status": "completed",
                "replay": True,
            })
            for stmt_payload in statement_payloads:
                yield _sse_event("statement", stmt_payload)
            if summary_payload is not None:
                yield _sse_event("summary", summary_payload)
            yield _sse_event("done", {
                "session_id": session_id_str,
                "status": "completed",
                "replay": True,
            })

        return EventSourceResponse(replay_events())

    if session_row.status == DebateStatus.FAILED:
        error_message = session_row.error_message or "debate session failed"

        async def failed_events():
            yield _sse_event("error", {
                "session_id": session_id_str,
                "status": "failed",
                "message": error_message,
            })
        return EventSourceResponse(failed_events())

    db.add(session_row)
    session_row.status = DebateStatus.RUNNING
    db.commit()
    db.refresh(session_row)

    service = DebateExecutionService()

    async def event_generator():
        stream_completed = False
        failure_reason = "stream terminated before completion"
        try:
            async for event in service.stream_session(
                session_id=session_id_str,
                user_id=user_id_str,
                symbol=symbol,
                symbol_name=symbol_name,
                category=category,
                estimated_tokens=settings.estimated_tokens_for_category(category),
            ):
                yield event
            stream_completed = True
        except asyncio.CancelledError:
            failure_reason = "client disconnected during stream"
            raise
        except DebateStartRejectedError as exc:
            failure_reason = str(exc)
            yield _sse_event("error", {
                "session_id": session_id_str,
                "status": "rejected",
                "message": failure_reason,
            })
        except Exception as exc:
            failure_reason = str(exc)
            yield _sse_event("error", {
                "session_id": session_id_str,
                "status": "failed",
                "message": failure_reason,
            })
        finally:
            if not stream_completed:
                from app.repositories.debate_repo import fail_session_if_running

                await asyncio.shield(fail_session_if_running(session_id_str, failure_reason))

    return EventSourceResponse(event_generator())


@router.post("/{session_id}/publish/notion", response_model=DebateNotionPublishResponse)
def publish_debate_to_notion(session_id: UUID, db: Session = Depends(get_db)) -> DebateNotionPublishResponse:
    session_row = db.scalar(
        select(DebateSession)
        .where(DebateSession.id == session_id)
        .with_for_update()
    )
    if session_row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"debate session not found: {session_id}")
    if session_row.status != DebateStatus.COMPLETED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="debate session is not completed yet",
        )
    if session_row.notion_page_id and session_row.notion_page_url and session_row.notion_published_at:
        return DebateNotionPublishResponse(
            session_id=session_row.id,
            notion_page_id=session_row.notion_page_id,
            notion_page_url=session_row.notion_page_url,
            notion_published_at=session_row.notion_published_at,
        )

    ticker = db.get(TickerMetadata, session_row.symbol)
    summary_row = db.scalar(select(ModeratorSummary).where(ModeratorSummary.session_id == session_id))
    statement_rows = list(
        db.scalars(
            select(AgentStatement)
            .where(AgentStatement.session_id == session_id)
            .options(selectinload(AgentStatement.evidences))
            .order_by(AgentStatement.round_order)
        )
    )
    if summary_row is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="debate summary is not available yet",
        )

    client = NotionMcpClient()
    try:
        result = client.publish_debate(
            _build_publish_payload(
                session_row=session_row,
                ticker=ticker,
                summary_row=summary_row,
                statement_rows=statement_rows,
            )
        )
    except NotionMcpError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"failed to publish debate to notion: {exc}",
        ) from exc

    session_row.notion_page_id = result.page_id
    session_row.notion_page_url = result.page_url
    session_row.notion_published_at = datetime.now(UTC)
    db.add(session_row)
    db.commit()
    db.refresh(session_row)
    return DebateNotionPublishResponse(
        session_id=session_row.id,
        notion_page_id=session_row.notion_page_id or "",
        notion_page_url=session_row.notion_page_url or "",
        notion_published_at=session_row.notion_published_at or datetime.now(UTC),
    )


@router.delete("/{session_id}")
def delete_debate(session_id: UUID, db: Session = Depends(get_db)) -> dict[str, str]:
    row = db.get(DebateSession, session_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"debate session not found: {session_id}")
    # Detach any session that cached from this one, then delete via Core so the
    # DB's ON DELETE CASCADE removes statements/evidence/summaries/notes.
    # (ORM-level delete would NULL child FKs and violate NOT NULL.)
    db.execute(
        update(DebateSession)
        .where(DebateSession.cached_from_session_id == session_id)
        .values(cached_from_session_id=None)
    )
    db.execute(delete(DebateSession).where(DebateSession.id == session_id))
    db.commit()
    return {"status": "deleted", "session_id": str(session_id)}


def _build_session_response(db: Session, session_id: UUID) -> DebateSessionResponse:
    row = db.get(DebateSession, session_id)
    assert row is not None
    summary_row = db.scalar(select(ModeratorSummary).where(ModeratorSummary.session_id == session_id))
    statement_rows = list(
        db.scalars(
            select(AgentStatement)
            .where(AgentStatement.session_id == session_id)
            .order_by(AgentStatement.round_order)
        )
    )
    return DebateSessionResponse(
        session_id=row.id,
        user_id=row.user_id,
        symbol=row.symbol,
        category=str(row.category.value if hasattr(row.category, "value") else row.category),
        status=str(row.status.value if hasattr(row.status, "value") else row.status),
        started_at=row.started_at,
        completed_at=row.completed_at,
        summary_content=summary_row.summary_content if summary_row else None,
        key_points=(summary_row.key_points or []) if summary_row else [],
        notion_page_id=row.notion_page_id,
        notion_page_url=row.notion_page_url,
        notion_published_at=row.notion_published_at,
        statements=[
            DebateStatementResponse(
                agent_role=str(stmt.agent_role.value if hasattr(stmt.agent_role, "value") else stmt.agent_role),
                round=str(stmt.round.value if hasattr(stmt.round, "value") else stmt.round),
                round_order=stmt.round_order,
                content=stmt.content,
                model_used=stmt.model_name or "",
                evidence_count=len(stmt.evidences or []),
            )
            for stmt in statement_rows
        ],
    )


def _get_ticker_or_404(db: Session, symbol: str) -> TickerMetadata:
    ticker = db.get(TickerMetadata, symbol)
    if ticker is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"ticker not found: {symbol}")
    return ticker


def _create_session_row(db: Session, payload: DebateCreateRequest, *, status: DebateStatus) -> DebateSession:
    session_row = DebateSession(
        user_id=payload.user_id,
        symbol=payload.symbol,
        category=payload.category,
        status=status,
    )
    db.add(session_row)
    db.commit()
    db.refresh(session_row)
    return session_row


def _enum_value(value) -> str:
    return str(value.value if hasattr(value, "value") else value)


def _sse_event(event: str, payload: dict) -> dict[str, str]:
    import json

    return {
        "event": event,
        "data": json.dumps(payload, ensure_ascii=False),
    }


def _build_publish_payload(
    *,
    session_row: DebateSession,
    ticker: TickerMetadata | None,
    summary_row: ModeratorSummary,
    statement_rows: list[AgentStatement],
):
    from app.integrations.notion_mcp import DebatePublishPayload

    key_points = [str(item) for item in (summary_row.key_points or []) if str(item).strip()]
    statements: list[dict[str, object]] = []
    for stmt in statement_rows:
        evidence_lines = []
        for evidence in stmt.evidences or []:
            label = evidence.source_label or evidence.source_title or evidence.source_url or "evidence"
            evidence_lines.append(f"{label}: {evidence.excerpt}")
        statements.append(
            {
                "agent_role": str(stmt.agent_role.value if hasattr(stmt.agent_role, "value") else stmt.agent_role),
                "round": str(stmt.round.value if hasattr(stmt.round, "value") else stmt.round),
                "content": stmt.content,
                "evidence_lines": evidence_lines,
            }
        )
    return DebatePublishPayload(
        session_id=str(session_row.id),
        symbol=session_row.symbol,
        symbol_name=ticker.name_kr if ticker else session_row.symbol,
        category=str(session_row.category.value if hasattr(session_row.category, "value") else session_row.category),
        started_at=session_row.started_at,
        summary_content=summary_row.summary_content,
        key_points=key_points,
        statements=statements,
    )
