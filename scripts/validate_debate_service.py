from __future__ import annotations

from app.domain.debate_service import DebateExecutionService


class FakeTracker:
    def __init__(self) -> None:
        self.started = []
        self.ended = []
        self.bound = []

    def try_start_session(self, *, user_id: str, symbol: str, session_id: str, estimated_tokens: int = 0):
        self.started.append((user_id, symbol, session_id, estimated_tokens))

        class Result:
            allowed = True
            reason = "ok"

        return Result()

    def end_session(self, *, user_id: str, symbol: str, session_id: str) -> None:
        self.ended.append((user_id, symbol, session_id))

    def bind_context(self, *, user_id: str, symbol: str, session_id: str):
        self.bound.append((user_id, symbol, session_id))

        class _Ctx:
            def __enter__(self_inner):
                return None

            def __exit__(self_inner, exc_type, exc, tb):
                return False

        return _Ctx()


class FakeGraph:
    async def astream(self, state):
        yield {"data_agent": {"price_context": "p", "financial_context": "f", "evidence_context": "e", "news_chunks": ["n"]}}
        yield {"moderator_pre": {"agenda": ["쟁점1"], "moderator_flag": "ok", "intervention_note": "", "hallucination_count": 0}}
        yield {"bull_agent": {"statements": [{"agent_role": "bull", "round": "opening", "round_order": 1, "content": "상승", "model_used": "fake", "evidences": []}], "round_order": 1}}
        yield {"moderator_summary": {"summary_content": "요약", "key_points": ["k1"], "current_round": "summary"}}


async def main() -> None:
    service = DebateExecutionService(graph_runner=FakeGraph(), tracker=FakeTracker())
    state = await service.run_session(
        session_id="session-1",
        user_id="user-1",
        symbol="000660",
        symbol_name="SK하이닉스",
        category="financial",
        user_portfolio={},
    )
    assert state["summary_content"] == "요약"
    assert state["agenda"] == ["쟁점1"]
    assert len(state["statements"]) == 1
    print({"session_id": state["session_id"], "summary": state["summary_content"], "statement_count": len(state["statements"])})


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
