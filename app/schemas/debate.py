from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class DebateCreateRequest(BaseModel):
    user_id: UUID
    symbol: str = Field(min_length=1, max_length=30)
    category: str = Field(min_length=1, max_length=30)
    avg_price: float | None = None


class DebateStatementResponse(BaseModel):
    agent_role: str
    round: str
    round_order: int
    content: str
    model_used: str
    evidence_count: int = 0


class DebateSessionResponse(BaseModel):
    session_id: UUID
    user_id: UUID
    symbol: str
    category: str
    status: str
    started_at: datetime | None = None
    completed_at: datetime | None = None
    summary_content: str | None = None
    key_points: list[str] = []
    statements: list[DebateStatementResponse] = []

