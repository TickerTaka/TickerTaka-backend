# app/agents/nodes/moderator_node.py
from __future__ import annotations
import json
import logging
from langchain_core.messages import SystemMessage, HumanMessage

from app.agents.state import DebateState
from app.agents.prompts.prompts import (
    MODERATOR_PRE_SYSTEM, MODERATOR_PRE_HUMAN,
    MODERATOR_CHECK_SYSTEM, MODERATOR_CHECK_HUMAN,
    MODERATOR_SUMMARY_SYSTEM, MODERATOR_SUMMARY_HUMAN,
)
from app.core.llm_factory import get_llm
from app.repositories.debate_repo import (
    save_statement, save_evidence,
    save_moderator_summary, update_session_status,
)

logger = logging.getLogger(__name__)


def _call(system: str, human: str, temp: float = 0.3) -> str:
    llm = get_llm("moderator", temperature=temp)
    return llm.invoke([SystemMessage(content=system), HumanMessage(content=human)]).content


def _parse(text: str, fallback: dict) -> dict:
    try:
        s = text.find("{"); e = text.rfind("}") + 1
        return json.loads(text[s:e]) if s >= 0 else fallback
    except Exception:
        return fallback


# ── 1. 의제 설계 ───────────────────────────────────────────
def moderator_pre_node(state: DebateState) -> dict:
    logger.info("[moderator_pre] 의제 설계")
    raw    = _call(MODERATOR_PRE_SYSTEM, MODERATOR_PRE_HUMAN.format(
        symbol=state["symbol"], symbol_name=state["symbol_name"],
        category=state["category"],
        price_context=state["price_context"],
        financial_context=state["financial_context"],
        evidence_context=state["evidence_context"],
    ))
    parsed = _parse(raw, {"agenda": ["쟁점1", "쟁점2", "쟁점3"]})
    return {
        "agenda": parsed.get("agenda", []),
        "moderator_flag": "ok",
        "intervention_note": "",
        "hallucination_count": 0,
    }


# ── 2. 발언 검증 ───────────────────────────────────────────
def moderator_check_node(state: DebateState) -> dict:
    if not state["statements"]:
        return {"moderator_flag": "ok", "intervention_note": ""}

    last = state["statements"][-1]
    logger.info(f"[moderator_check] {last['agent_role']} 검증")

    raw    = _call(MODERATOR_CHECK_SYSTEM, MODERATOR_CHECK_HUMAN.format(
        agent_role=last["agent_role"], content=last["content"],
        category=state["category"],
        price_context=state["price_context"],
        financial_context=state["financial_context"],
        evidence_context=state["evidence_context"],
    ), temp=0.1)
    parsed  = _parse(raw, {"verdict": "ok", "note": "", "corrected_fact": ""})
    verdict = parsed.get("verdict", "ok")
    note    = parsed.get("note", "")

    hallucination_count = state.get("hallucination_count", 0)
    extra = []

    if verdict in ("intervene", "hallucination"):
        if verdict == "hallucination":
            hallucination_count += 1
        msg = f"[사회자 개입] {note}"
        if parsed.get("corrected_fact"):
            msg += f"\n정정: {parsed['corrected_fact']}"
        extra = [{
            "agent_role": "moderator", "round": state["current_round"],
            "round_order": state["round_order"] + 1, "content": msg,
            "model_used": "deepseek/deepseek-r1:free", "evidences": [],
        }]

    return {
        "moderator_flag":      "intervene" if verdict != "ok" else "ok",
        "intervention_note":   note,
        "hallucination_count": hallucination_count,
        "statements":          extra,
        "round_order":         state["round_order"] + (1 if extra else 0),
    }


# ── 3. 최종 요약 + DB 저장 ────────────────────────────────
async def moderator_summary_node(state: DebateState) -> dict:
    logger.info("[moderator_summary] 요약 + DB 저장")

    all_stmts = "\n\n".join(
        f"[{s['agent_role'].upper()} / {s['round']}]\n{s['content']}"
        for s in state["statements"]
    )
    portfolio = state.get("user_portfolio", {})
    port_ctx  = f"\n[평균 매수가] {portfolio.get('avg_price',0):,}원\n" if portfolio else ""

    raw    = _call(MODERATOR_SUMMARY_SYSTEM, MODERATOR_SUMMARY_HUMAN.format(
        all_statements=all_stmts,
        symbol=state["symbol"], symbol_name=state["symbol_name"],
        category=state["category"],
        price_context=state["price_context"],
        evidence_context=state["evidence_context"],
        portfolio_context=port_ctx,
    ), temp=0.4)
    parsed  = _parse(raw, {"summary_content": raw, "key_points": []})
    summary = parsed.get("summary_content", raw)
    points  = parsed.get("key_points", [])

    # DB 저장
    for stmt in state["statements"]:
        stmt_id = await save_statement(
            session_id=state["session_id"], round_=stmt["round"],
            round_order=stmt["round_order"], agent_role=stmt["agent_role"],
            content=stmt["content"], model_name=stmt.get("model_used", ""),
        )
        for ev in stmt.get("evidences", []):
            await save_evidence(
                statement_id=stmt_id,
                source_type=ev.get("source_type", "OTHER"),
                excerpt=ev.get("excerpt", ""),
                source_url=ev.get("source_url"),
                source_label=ev.get("source_label"),
                source_title=ev.get("source_title"),
                news_cache_id=ev.get("news_cache_id"),
                filing_cache_id=ev.get("filing_cache_id"),
            )

    await save_statement(
        session_id=state["session_id"], round_="summary",
        round_order=state["round_order"] + 1, agent_role="moderator",
        content=summary, model_name="deepseek/deepseek-r1:free",
    )
    await save_moderator_summary(state["session_id"], summary, points)
    await update_session_status(state["session_id"], "completed")

    return {
        "statements": [{
            "agent_role": "moderator", "round": "summary",
            "round_order": state["round_order"] + 1,
            "content": summary, "model_used": "deepseek/deepseek-r1:free", "evidences": [],
        }],
        "summary_content": summary,
        "key_points":      points,
        "current_round":   "summary",
    }
