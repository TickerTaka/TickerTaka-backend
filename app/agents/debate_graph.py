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
    judge_agent_node,
    moderator_summary_node,
)

logger = logging.getLogger(__name__)

_NUM_TOPICS = 3  # 모더레이터가 생성하는 주제 수


def _final_node(state: DebateState) -> Literal["judge_agent", "moderator_summary"]:
    return "judge_agent" if state.get("decision_agent") == "judge" else "moderator_summary"


def _router(state: DebateState) -> Literal["bull_agent", "bear_agent", "judge_agent", "moderator_summary"]:
    # 환각 5회 이상 → 강제 종료 (4턴×3주제=12 발언에서 모더레이터가 과민 판정해도
    # 토론이 1주제만에 끊기지 않도록 임계값을 상향.)
    if state.get("hallucination_count", 0) >= 5:
        logger.warning("[graph] 환각 5회 초과 → final 이동")
        return _final_node(state)

    if state["moderator_flag"] == "end":
        return _final_node(state)

    topic_idx = state.get("current_topic_index", 0)
    turn      = state.get("current_turn", 1)

    # 모든 주제 완료 → summary
    if topic_idx >= _NUM_TOPICS:
        logger.info("[graph] 모든 주제 완료 → final")
        return _final_node(state)

    # 사회자 개입 → 직전 에이전트 재발언
    if state["moderator_flag"] == "intervene":
        return "bear_agent" if turn in (2, 4) else "bull_agent"

    # 4턴 구조: 1=bull주장, 2=bear반박, 3=bull재반박, 4=bear재반박
    # turn=2,4 → bear 차례 / turn=1,3 → bull 차례
    if turn in (2, 4):
        return "bear_agent"
    return "bull_agent"


def build_graph() -> StateGraph:
    b = StateGraph(DebateState)

    b.add_node("data_agent",        data_agent_node)
    b.add_node("moderator_pre",     moderator_pre_node)
    b.add_node("bull_agent",        bull_agent_node)
    b.add_node("bear_agent",        bear_agent_node)
    b.add_node("moderator_check",   moderator_check_node)
    b.add_node("judge_agent",       judge_agent_node)
    b.add_node("moderator_summary", moderator_summary_node)

    b.add_edge(START,             "data_agent")
    b.add_edge("data_agent",      "moderator_pre")
    b.add_edge("moderator_pre",   "bull_agent")
    b.add_edge("bull_agent",      "moderator_check")
    b.add_edge("bear_agent",      "moderator_check")
    b.add_conditional_edges("moderator_check", _router)
    b.add_edge("judge_agent",     "moderator_summary")
    b.add_edge("moderator_summary", END)

    return b.compile()


debate_graph = build_graph()
