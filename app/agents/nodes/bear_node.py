from __future__ import annotations
import logging
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.prebuilt import create_react_agent
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from openai import APIError, RateLimitError, APITimeoutError

from app.agents.state import DebateState
from app.agents.prompts.prompts import BEAR_SYSTEM, BEAR_HUMAN
from app.agents.tools.evidence_tools import search_evidence
from app.agents.nodes.utils import extract_evidences
from app.core.llm_factory import get_llm
from app.config import get_settings

logger = logging.getLogger(__name__)
_TOOLS = [search_evidence]  # 가격/재무 데이터는 data_agent에서 이미 제공

# 4턴 구조: 1=bull주장, 2=bear반박, 3=bull재반박, 4=bear재반박
_TURN_LABEL = {2: "rebuttal", 4: "counter_rebuttal"}


def bear_agent_node(state: DebateState) -> dict:
    topic_idx     = state.get("current_topic_index", 0)
    turn          = state.get("current_turn", 2)
    agenda        = state.get("agenda", [])
    current_topic = agenda[topic_idx] if topic_idx < len(agenda) else f"주제 {topic_idx + 1}"
    turn_type     = _TURN_LABEL.get(turn, "rebuttal")

    logger.info(f"[bear] 주제 {topic_idx + 1}/3 | 턴={turn_type}")

    bull_stmts = [
        s for s in state["statements"]
        if s["agent_role"] == "bull" and s.get("topic_index", 0) == topic_idx
    ]
    last_bull = bull_stmts[-1]["content"] if bull_stmts else "없음"

    # 교정 이력 주입: retry 여부와 무관하게 세션 내 모든 발언에 상시 포함
    corrections = state.get("bear_corrections", [])
    if state.get("moderator_flag") == "intervene":
        note = state.get("intervention_note", "")
        fact = state.get("corrected_fact", "")
        current = note + (f" → 올바른 수치: {fact}" if fact else "")
        if current and current not in corrections:
            corrections = corrections + [current]

    correction_block = ""
    if corrections:
        lines = "\n".join(f"  - {c}" for c in corrections)
        prefix = "[사회자 교정 요청 — 즉시 수정]\n" if state.get("moderator_flag") == "intervene" else "[교정 이력 — 반드시 준수]\n"
        correction_block = (
            f"\n\n{prefix}"
            "아래 항목은 이전 발언에서 사회자가 지적한 오류입니다. 동일한 실수를 반복하지 마세요.\n"
            f"{lines}\n"
            "데이터에 명시된 수치만 인용하세요."
        )

    user_input = BEAR_HUMAN.format(
        symbol=state["symbol"],
        symbol_name=state["symbol_name"],
        category=state["category"],
        topic_index=topic_idx + 1,
        current_topic=current_topic,
        turn_type=turn_type,
        agenda="\n".join(f"- {a}" for a in agenda),
        price_context=state["price_context"],
        financial_context=state["financial_context"],
        evidence_context=state["evidence_context"],
        last_bull_statement=last_bull,
    ) + correction_block

    try:
        content, evidences = _invoke_bear(user_input)
    except Exception as e:
        logger.error(f"[bear] 최종 오류 (재시도 소진): {e}")
        content, evidences = f"(오류: {e})", []

    new_round_order = state["round_order"] + 1

    # 턴2(rebuttal) → 다음은 bull 재반박(turn=3)
    # 턴4(counter_rebuttal) → 주제 완료, 다음 주제로 이동(turn=1, topic_index+1)
    if turn == 2:
        next_turn        = 3
        next_topic_index = topic_idx
        next_round       = "counter_rebuttal"
    else:  # turn == 4
        next_turn        = 1
        next_topic_index = topic_idx + 1
        next_round       = "claim"

    return {
        "statements": [{
            "agent_role":  "bear",
            "round":       turn_type,
            "round_order": new_round_order,
            "topic_index": topic_idx,
            "content":     content,
            "model_used":  get_settings().bear_model,
            "evidences":   evidences,
        }],
        "round_order":         new_round_order,
        "current_turn":        next_turn,
        "current_topic_index": next_topic_index,
        "current_round":       next_round,
    }


@retry(
    retry=retry_if_exception_type((RateLimitError, APITimeoutError, APIError)),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    reraise=True,
)
def _invoke_bear(user_input: str) -> tuple[str, list]:
    llm    = get_llm("bear", temperature=0.7, cached=False)
    agent  = create_react_agent(llm, _TOOLS)
    result = agent.invoke({
        "messages": [
            SystemMessage(content=BEAR_SYSTEM),
            HumanMessage(content=user_input),
        ]
    })
    return result["messages"][-1].content, extract_evidences(result["messages"])
