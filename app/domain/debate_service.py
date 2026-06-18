from __future__ import annotations

import json
from typing import Any

from app.agents.debate_checkpoint import load_checkpoint, merge_state, save_checkpoint
from app.agents.state import DebateState
from app.config import get_settings
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
        decision_agent: str = "moderator",
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
            decision_agent=decision_agent,
        )
        state["decision_agent"] = decision_agent if decision_agent == "judge" else "moderator"

        try:
            with self.tracker.bind_context(user_id=user_id, symbol=symbol, session_id=session_id):
                async for chunk in _astream_with_config(graph_runner, state):
                    node = next(iter(chunk))
                    data = chunk[node]
                    state = merge_state(state, data)
                    save_checkpoint(state)
        except Exception as exc:
            from app.repositories.debate_repo import fail_session_if_running

            await fail_session_if_running(session_id, str(exc))
            raise
        finally:
            self.tracker.end_session(user_id=user_id, symbol=symbol, session_id=session_id)

        return state

    async def stream_session(
        self,
        *,
        session_id: str,
        user_id: str,
        symbol: str,
        symbol_name: str,
        category: str,
        decision_agent: str = "moderator",
        estimated_tokens: int = 0,
    ):
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
            decision_agent=decision_agent,
        )
        state["decision_agent"] = decision_agent if decision_agent == "judge" else "moderator"

        try:
            yield _stream_event(
                "session_started",
                {
                    "session_id": session_id,
                    "symbol": symbol,
                    "symbol_name": symbol_name,
                    "category": category,
                    "decision_agent": decision_agent,
                    "status": "running",
                },
            )
            with self.tracker.bind_context(user_id=user_id, symbol=symbol, session_id=session_id):
                async for chunk in _astream_with_config(graph_runner, state):
                    node = next(iter(chunk))
                    data = chunk[node]
                    state = merge_state(state, data)
                    save_checkpoint(state)
                    for event in self._build_chunk_events(
                        session_id=session_id,
                        symbol=symbol,
                        symbol_name=symbol_name,
                        category=category,
                        node=node,
                        data=data,
                        state=state,
                    ):
                        yield event
        except Exception as exc:
            from app.repositories.debate_repo import fail_session_if_running

            await fail_session_if_running(session_id, str(exc))
            raise
        finally:
            self.tracker.end_session(user_id=user_id, symbol=symbol, session_id=session_id)

        yield _stream_event(
            "done",
            {
                "session_id": session_id,
                "status": "completed",
                "summary_content": state.get("summary_content", ""),
                "key_points": state.get("key_points", []),
                "decision_agent": state.get("decision_agent", "moderator"),
                "judge_result": state.get("judge_result", {}),
                "debate_ended_by": state.get("debate_ended_by", ""),
                "debate_end_reason": state.get("debate_end_reason", ""),
            },
        )

    @staticmethod
    def _build_initial_state(
        *,
        session_id: str,
        user_id: str,
        symbol: str,
        symbol_name: str,
        category: str,
        decision_agent: str = "moderator",
    ) -> DebateState:
        return {
            "session_id": session_id,
            "user_id": user_id,
            "symbol": symbol,
            "symbol_name": symbol_name,
            "category": category,
            "decision_agent": decision_agent if decision_agent == "judge" else "moderator",
            "current_round": "opening",
            "round_order": 0,
            "max_rounds": 3,
            "current_topic_index": 0,
            "current_turn": 1,
            "agenda": [],
            "price_context": "",
            "financial_context": "",
            "evidence_context": "",
            "news_chunks": [],
            "initial_evidences": [],
            "statements": [],
            "moderator_flag": "ok",
            "intervention_note": "",
            "hallucination_count": 0,
            "debate_ended_by": "",
            "debate_end_reason": "",
            "judge_result": {},
            "summary_content": "",
            "key_points": [],
        }

    @staticmethod
    def _build_chunk_events(
        *,
        session_id: str,
        symbol: str,
        symbol_name: str,
        category: str,
        node: str,
        data: dict[str, Any],
        state: DebateState,
    ) -> list[dict[str, str]]:
        events: list[dict[str, str]] = []
        if node == "data_agent":
            events.append(
                _stream_event(
                    "stage",
                    {
                        "session_id": session_id,
                        "node": node,
                        "stage": "context_collected",
                        "symbol": symbol,
                        "symbol_name": symbol_name,
                        "category": category,
                    },
                )
            )
        if node == "moderator_pre":
            events.append(
                _stream_event(
                    "agenda",
                    {
                        "session_id": session_id,
                        "agenda": state.get("agenda", []),
                    },
                )
            )
        if node == "moderator_check" and data.get("debate_ended_by"):
            events.append(
                _stream_event(
                    "debate_stopped",
                    {
                        "session_id": session_id,
                        "ended_by": data.get("debate_ended_by", ""),
                        "reason": data.get("debate_end_reason", ""),
                        "hallucination_count": data.get("hallucination_count", 0),
                    },
                )
            )
        if node == "judge_agent":
            events.append(
                _stream_event(
                    "judge",
                    {
                        "session_id": session_id,
                        "judge_result": data.get("judge_result", {}),
                    },
                )
            )
        for statement in data.get("statements", []):
            events.append(
                _stream_event(
                    "statement",
                    {
                        "session_id": session_id,
                        "node": node,
                        "agent_role": statement.get("agent_role", ""),
                        "round": statement.get("round", ""),
                        "round_order": statement.get("round_order", 0),
                        "topic_index": statement.get("topic_index"),
                        "content": statement.get("content", ""),
                        "model_used": statement.get("model_used", ""),
                        "evidence_count": len(statement.get("evidences", []) or []),
                    },
                )
            )
        if node == "moderator_summary":
            events.append(
                _stream_event(
                    "summary",
                    {
                        "session_id": session_id,
                        "summary_content": data.get("summary_content", ""),
                        "key_points": data.get("key_points", []),
                        "decision_agent": state.get("decision_agent", "moderator"),
                        "judge_result": state.get("judge_result", {}),
                        "debate_ended_by": state.get("debate_ended_by", ""),
                        "debate_end_reason": state.get("debate_end_reason", ""),
                    },
                )
            )
        return events


def _get_default_graph():
    from app.agents.debate_graph import debate_graph

    return debate_graph


def _astream_with_config(graph_runner, state: DebateState):
    settings = get_settings()
    config = {"recursion_limit": settings.debate_graph_recursion_limit}
    try:
        return graph_runner.astream(state, config)
    except TypeError:
        return graph_runner.astream(state)


def _stream_event(event: str, payload: dict[str, Any]) -> dict[str, str]:
    return {
        "event": event,
        "data": json.dumps(payload, ensure_ascii=False),
    }
