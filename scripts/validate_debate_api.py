"""Endpoint smoke test for debate API via FastAPI TestClient.

검증 범위:
- /health 부트 확인
- POST /api/debates happy path
- GET /api/debates/{session_id} happy path
- 없는 symbol 404
- 잘못된 payload 422

주의:
- debate graph 전체를 돌리면 외부 LLM/Chroma 경로와 얽히므로
  smoke 단계에서는 DebateExecutionService.run_session만 mock으로 대체한다.
- 실제 DB에는 debate_session row가 생성되므로 종료 시 삭제한다.
"""
from __future__ import annotations

from unittest.mock import patch
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.core.db import session_scope
from app.main import app
from app.models import AppUser, DebateSession, TickerMetadata

SEED_EMAIL = "phase2-test-user@example.com"


def expect(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def get_seed_user_id() -> UUID:
    with session_scope() as session:
        user = session.scalar(select(AppUser).where(AppUser.email == SEED_EMAIL))
        if user is None:
            raise RuntimeError(f"seed user not found: {SEED_EMAIL}. run `python -m scripts.seed` first.")
        return user.id


def get_test_symbol_and_name() -> tuple[str, str]:
    with session_scope() as session:
        row = session.execute(
            select(TickerMetadata.symbol, TickerMetadata.name_kr).order_by(TickerMetadata.symbol).limit(1)
        ).first()
        if row is None:
            raise RuntimeError("ticker_metadata is empty")
        return row[0], row[1]


def cleanup_debate_sessions(user_id: UUID, symbol: str) -> None:
    with session_scope() as session:
        session.execute(
            delete(DebateSession).where(DebateSession.user_id == user_id, DebateSession.symbol == symbol)
        )


async def fake_run_session(self, *, session_id: str, user_id: str, symbol: str, symbol_name: str, category: str, user_portfolio=None, estimated_tokens: int = 0):
    return {
        "session_id": session_id,
        "user_id": user_id,
        "symbol": symbol,
        "symbol_name": symbol_name,
        "category": category,
        "user_portfolio": user_portfolio or {},
        "current_round": "summary",
        "round_order": 0,
        "max_rounds": 3,
        "agenda": [],
        "price_context": "",
        "financial_context": "",
        "evidence_context": "",
        "news_chunks": [],
        "statements": [],
        "moderator_flag": "ok",
        "intervention_note": "",
        "hallucination_count": 0,
        "summary_content": "",
        "key_points": [],
    }


def main() -> None:
    user_id = get_seed_user_id()
    symbol, _ = get_test_symbol_and_name()
    cleanup_debate_sessions(user_id, symbol)
    client = TestClient(app)

    try:
        r = client.get("/health")
        expect(r.status_code == 200, f"health expected 200, got {r.status_code}")
        print(f"[PASS] health: {r.status_code} {r.json()}")

        with patch("app.api.debate.DebateExecutionService.run_session", new=fake_run_session):
            r = client.post(
                "/api/debates",
                json={
                    "user_id": str(user_id),
                    "symbol": symbol,
                    "category": "financial",
                    "avg_price": 100000,
                },
            )
            expect(r.status_code == 201, f"create expected 201, got {r.status_code}: {r.text}")
            body = r.json()
            session_id = body["session_id"]
            expect(body["symbol"] == symbol, "symbol mismatch")
            expect(body["user_id"] == str(user_id), "user_id mismatch")
            expect(body["category"] == "financial", "category mismatch")
            print(f"[PASS] POST create: 201, session_id={session_id}")

            r = client.get(f"/api/debates/{session_id}")
            expect(r.status_code == 200, f"get expected 200, got {r.status_code}: {r.text}")
            body = r.json()
            expect(body["session_id"] == session_id, "session_id mismatch")
            expect(body["symbol"] == symbol, "GET symbol mismatch")
            print(f"[PASS] GET debate: 200, status={body['status']}")

        r = client.post(
            "/api/debates",
            json={
                "user_id": str(user_id),
                "symbol": "ZZZ999999",
                "category": "financial",
            },
        )
        expect(r.status_code == 404, f"unknown symbol expected 404, got {r.status_code}: {r.text}")
        print("[PASS] POST unknown symbol: 404")

        r = client.post(
            "/api/debates",
            json={
                "user_id": str(user_id),
                "symbol": symbol,
            },
        )
        expect(r.status_code == 422, f"missing category expected 422, got {r.status_code}: {r.text}")
        print("[PASS] POST missing field: 422")

        print("\nALL DEBATE API SMOKE TESTS PASSED")
    finally:
        cleanup_debate_sessions(user_id, symbol)


if __name__ == "__main__":
    main()
