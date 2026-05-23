from __future__ import annotations

from typing import Any

from app.agents.debate_checkpoint import load_checkpoint, merge_state, save_checkpoint
from app.agents.state import DebateState
from app.core.debate_runtime_guard import DebateRuntimeGuard, get_tracker


class DebateStartRejectedError(RuntimeError):
    pass


class DebateExecutionService:
    def __init__(
        self,
        *,
        graph_runner=None,
        tracker: DebateRuntimeGuard | None = None,
    ) -> None:
        self.graph_runner = graph_runner
        self.tracker = tracker or get_tracker()

    async def run_session(
        self,
        *,
        session_id: str,
        user_id: str,
        symbol: str,
        symbol_name: str,
        category: str,
        user_portfolio: dict[str, Any] | None = None,
        estimated_tokens: int = 0,
    ) -> DebateState:
        start_result = self.tracker.try_start_session(
            user_id=user_id,
            symbol=symbol,
            session_id=session_id,
            estimated_tokens=estimated_tokens,
        )
        if not start_result.allowed:
            raise DebateStartRejectedError(start_result.reason)

        graph_runner = self.graph_runner or _get_default_graph()

        state = load_checkpoint(session_id) or self._build_initial_state(
            session_id=session_id,
            user_id=user_id,
            symbol=symbol,
            symbol_name=symbol_name,
            category=category,
            user_portfolio=user_portfolio or {},
        )

        try:
            with self.tracker.bind_context(user_id=user_id, symbol=symbol, session_id=session_id):
                async for chunk in graph_runner.astream(state):
                    node = next(iter(chunk))
                    data = chunk[node]
                    state = merge_state(state, data)
                    save_checkpoint(state)
        except Exception as exc:
            from app.repositories.debate_repo import update_session_status

            await update_session_status(session_id, "failed", str(exc))
            raise
        finally:
            self.tracker.end_session(user_id=user_id, symbol=symbol, session_id=session_id)

        return state

    @staticmethod
    def _build_initial_state(
        *,
        session_id: str,
        user_id: str,
        symbol: str,
        symbol_name: str,
        category: str,
        user_portfolio: dict[str, Any],
    ) -> DebateState:
        return {
            "session_id": session_id,
            "user_id": user_id,
            "symbol": symbol,
            "symbol_name": symbol_name,
            "category": category,
            "user_portfolio": user_portfolio,
            "current_round": "opening",
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


def _get_default_graph():
    from app.agents.debate_graph import debate_graph

    return debate_graph
