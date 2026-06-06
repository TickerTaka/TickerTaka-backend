from __future__ import annotations
import logging
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.prebuilt import create_react_agent

from app.agents.state import DebateState
from app.agents.prompts.prompts import BULL_SYSTEM, BULL_HUMAN
from app.agents.tools.evidence_tools import search_evidence
from app.core.llm_factory import get_llm

logger = logging.getLogger(__name__)
_TOOLS = [search_evidence]  # 가격/재무 데이터는 data_agent에서 이미 제공

# 4턴 구조: 1=bull주장, 2=bear반박, 3=bull재반박, 4=bear재반박
_TURN_LABEL = {1: "claim", 3: "counter_rebuttal"}
_ROUND_NAME = {1: "claim", 2: "rebuttal", 3: "counter_rebuttal", 4: "counter_rebuttal"}


def bull_agent_node(state: DebateState) -> dict:
    topic_idx     = state.get("current_topic_index", 0)
    turn          = state.get("current_turn", 1)
    agenda        = state.get("agenda", [])
    current_topic = agenda[topic_idx] if topic_idx < len(agenda) else f"주제 {topic_idx + 1}"
    turn_type     = _TURN_LABEL.get(turn, "claim")

    logger.info(f"[bull] 주제 {topic_idx + 1}/3 | 턴={turn_type}")

    bear_stmts = [
        s for s in state["statements"]
        if s["agent_role"] == "bear" and s.get("topic_index", 0) == topic_idx
    ]
    last_bear = bear_stmts[-1]["content"] if bear_stmts else "없음 (첫 번째 발언)"

    user_input = BULL_HUMAN.format(
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
        last_bear_statement=last_bear,
    )

    try:
        llm    = get_llm("bull", temperature=0.7, cached=False)
        agent  = create_react_agent(llm, _TOOLS)
        result = agent.invoke({
            "messages": [
                SystemMessage(content=BULL_SYSTEM),
                HumanMessage(content=user_input),
            ]
        })
        content   = result["messages"][-1].content
        evidences = _extract_evidences(result["messages"])
    except Exception as e:
        logger.error(f"[bull] 오류: {e}")
        content, evidences = f"(오류: {e})", []

    new_round_order = state["round_order"] + 1

    # 턴1(claim) → 다음은 bear(turn=2)
    # 턴3(counter_rebuttal) → 다음은 bear 재반박(turn=4)
    next_turn        = 2 if turn == 1 else 4
    next_topic_index = topic_idx

    return {
        "statements": [{
            "agent_role":  "bull",
            "round":       _ROUND_NAME.get(turn, "claim"),
            "round_order": new_round_order,
            "topic_index": topic_idx,
            "content":     content,
            "model_used":  "gpt-4o-mini",
            "evidences":   evidences,
        }],
        "round_order":         new_round_order,
        "current_turn":        next_turn,
        "current_topic_index": next_topic_index,
        "current_round":       _ROUND_NAME.get(next_turn, "rebuttal"),
    }


def _extract_evidences(messages) -> list[dict]:
    """LangChain ToolMessage에서 search_evidence 결과 추출 (OpenAI 포맷)."""
    import json
    evidences = []
    for msg in messages:
        # OpenAI: ToolMessage 객체, content는 JSON 문자열
        if hasattr(msg, "type") and msg.type == "tool" and hasattr(msg, "content"):
            try:
                parsed = json.loads(msg.content) if isinstance(msg.content, str) else msg.content
                if isinstance(parsed, list):
                    evidences.extend(parsed)
            except (json.JSONDecodeError, TypeError):
                pass
        # Anthropic 포맷 호환 (tool_result block)
        elif hasattr(msg, "content") and isinstance(msg.content, list):
            for block in msg.content:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    if isinstance(block.get("content"), list):
                        evidences.extend(block["content"])
    return evidences
