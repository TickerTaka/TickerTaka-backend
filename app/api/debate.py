from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.domain.debate_service import DebateExecutionService, DebateStartRejectedError
from app.models import AgentStatement, DebateSession, ModeratorSummary, TickerMetadata
from app.schemas.debate import DebateCreateRequest, DebateSessionResponse, DebateStatementResponse

router = APIRouter(prefix="/api/debates", tags=["debates"])


@router.post("", response_model=DebateSessionResponse, status_code=status.HTTP_201_CREATED)
async def create_debate(payload: DebateCreateRequest, db: Session = Depends(get_db)) -> DebateSessionResponse:
    ticker = db.get(TickerMetadata, payload.symbol)
    if ticker is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"ticker not found: {payload.symbol}")

    session_row = DebateSession(
        user_id=payload.user_id,
        symbol=payload.symbol,
        category=payload.category,
        status="running",
    )
    db.add(session_row)
    db.commit()
    db.refresh(session_row)

    try:
        await DebateExecutionService().run_session(
            session_id=str(session_row.id),
            user_id=str(payload.user_id),
            symbol=payload.symbol,
            symbol_name=ticker.name_kr,
            category=payload.category,
            user_portfolio={"avg_price": payload.avg_price} if payload.avg_price is not None else {},
        )
    except DebateStartRejectedError as exc:
        db.delete(session_row)
        db.commit()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    return _build_session_response(db, session_row.id)


@router.get("/{session_id}", response_model=DebateSessionResponse)
def get_debate(session_id: UUID, db: Session = Depends(get_db)) -> DebateSessionResponse:
    row = db.get(DebateSession, session_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"debate session not found: {session_id}")
    return _build_session_response(db, session_id)


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
