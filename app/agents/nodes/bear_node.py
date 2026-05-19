from __future__ import annotations
import logging
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.prebuilt import create_react_agent

from app.agents.state import DebateState
from app.agents.prompts.prompts import BEAR_SYSTEM, BEAR_HUMAN
from app.agents.tools.market_tools import get_stock_price, get_financial_metrics, get_technical_indicators
from app.agents.tools.evidence_tools import search_evidence
from app.core.llm_factory import get_llm

logger = logging.getLogger(__name__)
_TOOLS = [get_stock_price, get_financial_metrics, get_technical_indicators, search_evidence]
_ROUNDS = ["opening", "rebuttal", "closing"]


def _next_round(current: str) -> str:
    idx = _ROUNDS.index(current) if current in _ROUNDS else 0
    return _ROUNDS[idx + 1] if idx + 1 < len(_ROUNDS) else "summary"


def bear_agent_node(state: DebateState) -> dict:
    logger.info(f"[bear] {state['current_round']}")

    bull_stmts = [s for s in state["statements"] if s["agent_role"] == "bull"]
    last_bull  = bull_stmts[-1]["content"] if bull_stmts else "없음"

    user_input = BEAR_HUMAN.format(
        symbol=state["symbol"], symbol_name=state["symbol_name"],
        category=state["category"],
        agenda="\n".join(f"- {a}" for a in state.get("agenda", [])),
        price_context=state["price_context"],
        financial_context=state["financial_context"],
        last_bull_statement=last_bull,
        current_round=state["current_round"],
    )

    try:
        llm   = get_llm("bear", temperature=0.7)
        agent = create_react_agent(llm, _TOOLS)
        result = agent.invoke({
            "messages": [
                SystemMessage(content=BEAR_SYSTEM),
                HumanMessage(content=user_input),
            ]
        })
        content   = result["messages"][-1].content
        evidences = _extract_evidences(result["messages"])
    except Exception as e:
        logger.error(f"[bear] 오류: {e}")
        content, evidences = f"(오류: {e})", []

    return {
        "statements": [{
            "agent_role":  "bear",
            "round":       state["current_round"],
            "round_order": state["round_order"] + 1,
            "content":     content,
            "model_used":  "meta-llama/llama-3.3-70b-instruct:free",
            "evidences":   evidences,
        }],
        "round_order":   state["round_order"] + 1,
        "current_round": _next_round(state["current_round"]),
    }


def _extract_evidences(messages) -> list[dict]:
    evidences = []
    for msg in messages:
        if hasattr(msg, "content") and isinstance(msg.content, list):
            for block in msg.content:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    if isinstance(block.get("content"), list):
                        evidences.extend(block["content"])
    return evidences
