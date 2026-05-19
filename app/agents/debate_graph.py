# app/agents/debate_graph.py
from __future__ import annotations
import logging
from typing import Literal
from langgraph.graph import StateGraph, START, END

from app.agents.state import DebateState
from app.agents.nodes.data_node       import data_agent_node
from app.agents.nodes.bull_node       import bull_agent_node
from app.agents.nodes.bear_node       import bear_agent_node
from app.agents.nodes.moderator_node  import (
    moderator_pre_node,
    moderator_check_node,
    moderator_summary_node,
)

logger = logging.getLogger(__name__)

_ROUNDS = ["opening", "rebuttal", "closing"]


def _router(state: DebateState) -> Literal["bull_agent", "bear_agent", "moderator_summary"]:
    # 환각 2회 이상 → 강제 종료
    if state.get("hallucination_count", 0) >= 2:
        logger.warning("[graph] 환각 2회 초과 → summary 강제 이동")
        return "moderator_summary"

    # 사회자 개입 → 같은 에이전트 재발언
    if state["moderator_flag"] == "intervene":
        return "bull_agent" if state["round_order"] % 2 == 1 else "bear_agent"

    # summary 라운드 진입
    if state["current_round"] == "summary" or state["moderator_flag"] == "end":
        return "moderator_summary"

    # bull/bear 교대 (bull 홀수, bear 짝수 round_order)
    if state["round_order"] % 2 == 1:
        return "bear_agent"

    # 다음 라운드로 넘어갈 시점인지 확인
    idx = _ROUNDS.index(state["current_round"]) if state["current_round"] in _ROUNDS else -1
    if idx >= len(_ROUNDS) - 1:
        return "moderator_summary"

    return "bull_agent"


def build_graph() -> StateGraph:
    b = StateGraph(DebateState)

    b.add_node("data_agent",        data_agent_node)
    b.add_node("moderator_pre",     moderator_pre_node)
    b.add_node("bull_agent",        bull_agent_node)
    b.add_node("bear_agent",        bear_agent_node)
    b.add_node("moderator_check",   moderator_check_node)
    b.add_node("moderator_summary", moderator_summary_node)

    b.add_edge(START,             "data_agent")
    b.add_edge("data_agent",      "moderator_pre")
    b.add_edge("moderator_pre",   "bull_agent")
    b.add_edge("bull_agent",      "moderator_check")
    b.add_edge("bear_agent",      "moderator_check")
    b.add_conditional_edges("moderator_check", _router)
    b.add_edge("moderator_summary", END)

    return b.compile()


debate_graph = build_graph()
