# app/agents/nodes/moderator_node.py
from __future__ import annotations
import json
import logging
import asyncio
from langchain_core.messages import SystemMessage, HumanMessage
from langgraph.prebuilt import create_react_agent
from app.agents.tools.moderator_tools import get_financial_data, get_price_data

from app.agents.debate_checkpoint import clear_checkpoint
from app.agents.state import DebateState
from app.agents.prompts.prompts import (
    MODERATOR_PRE_SYSTEM, MODERATOR_PRE_HUMAN,
    MODERATOR_CHECK_SYSTEM, MODERATOR_CHECK_HUMAN,
    MODERATOR_SUMMARY_SYSTEM, MODERATOR_SUMMARY_HUMAN,
)
from app.core.debate_runtime_guard import get_tracker
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


def _default_agenda() -> list[str]:
    return ["쟁점1", "쟁점2", "쟁점3"]


def _build_summary_fallback(state: DebateState) -> tuple[str, list[str]]:
    agenda = state.get("agenda") or _default_agenda()
    non_moderator = [
        stmt for stmt in state.get("statements", [])
        if stmt.get("agent_role") in ("bull", "bear") and stmt.get("content")
    ]
    key_points: list[str] = [
        f"{topic}에 대한 찬반 논거가 제시되었으며, 상세 근거는 개별 발언을 참고해야 합니다."
        for topic in agenda[:3]
    ]

    summary = (
        "사회자 요약 생성 중 LLM 호출이 실패하여 축약 요약으로 대체했습니다. "
        f"{state['symbol_name']}({state['symbol']})에 대해 {len(non_moderator)}건의 찬반 발언이 기록되었고, "
        "최종 판단 전 개별 발언과 근거를 다시 확인해야 합니다."
    )
    return summary, key_points


def _coerce_agenda(value) -> list[str]:
    if isinstance(value, list):
        items = [str(item).strip() for item in value if str(item).strip()]
        if items:
            return items[:3]
    return _default_agenda()


def _schedule_background_task(coro, *, label: str) -> None:
    task = asyncio.create_task(coro)

    def _log_task_result(done_task: asyncio.Task) -> None:
        try:
            done_task.result()
        except Exception as exc:
            logger.error("[%s] 백그라운드 태스크 실패: %s", label, exc)

    task.add_done_callback(_log_task_result)


# ── 1. 의제 설계 ───────────────────────────────────────────
def moderator_pre_node(state: DebateState) -> dict:
    logger.info("[moderator_pre] 의제 설계")
    try:
        raw = _call(MODERATOR_PRE_SYSTEM, MODERATOR_PRE_HUMAN.format(
            symbol=state["symbol"], symbol_name=state["symbol_name"],
            category=state["category"],
            price_context=state["price_context"],
            financial_context=state["financial_context"],
            evidence_context=state["evidence_context"],
        ))
    except Exception as e:
        logger.warning("[moderator_pre] LLM 호출 실패, 기본 의제로 진행: %s", e)
        raw = json.dumps({"agenda": _default_agenda()}, ensure_ascii=False)
    parsed = _parse(raw, {"agenda": _default_agenda()})
    return {
        "agenda":              _coerce_agenda(parsed.get("agenda")),
        "moderator_flag":      "ok",
        "intervention_note":   "",
        "hallucination_count": 0,
        "current_topic_index": 0,
        "current_turn":        1,
        "current_round":       "claim",
        "round_order":         0,
    }


# ── 2. 발언 검증 ───────────────────────────────────────────
def moderator_check_node(state: DebateState) -> dict:
    if not state["statements"]:
        return {"moderator_flag": "ok", "intervention_note": ""}

    last = state["statements"][-1]
    # moderator 자신의 발언은 검증 생략
    if last["agent_role"] == "moderator":
        return {"moderator_flag": "ok", "intervention_note": ""}

    logger.info(f"[moderator_check] {last['agent_role']} 검증")

    try:
        raw = _call(MODERATOR_CHECK_SYSTEM, MODERATOR_CHECK_HUMAN.format(
            agent_role=last["agent_role"],
            symbol=state["symbol"],
            content=last["content"],
            price_context=state["price_context"],
            financial_context=state["financial_context"],
        ), temp=0.0)
    except Exception as e:
        logger.error(f"[moderator_check] 오류: {e}")
        return {"moderator_flag": "ok", "intervention_note": ""}

    parsed  = _parse(raw, {"verdict": "ok", "note": "", "corrected_fact": ""})
    verdict = parsed.get("verdict", "ok")
    note    = parsed.get("note", "")

    hallucination_count = state.get("hallucination_count", 0)
    extra = []

    if verdict == "hallucination":
        hallucination_count += 1
        msg = f"[사회자 개입] {note}"
        if parsed.get("corrected_fact"):
            msg += f"\n정정: {parsed['corrected_fact']}"
        extra = [{
            "agent_role":  "moderator",
            "round":       last["round"],
            "round_order": state["round_order"] + 1,
            "topic_index": last.get("topic_index", state.get("current_topic_index", 0)),
            "content":     msg,
            "model_used":  "gpt-4o-mini",
            "evidences":   [],
        }]

    return {
        "moderator_flag":      "intervene" if verdict == "hallucination" else "ok",
        "intervention_note":   note,
        "hallucination_count": hallucination_count,
        "statements":          extra,
        "round_order":         state["round_order"] + (1 if extra else 0),
    }


# ── 3. 최종 요약 + DB 저장 ────────────────────────────────
async def moderator_summary_node(state: DebateState) -> dict:
    logger.info("[moderator_summary] 요약 + DB 저장")
    used_fallback_summary = False

    all_stmts = "\n\n".join(
        f"[{s['agent_role'].upper()} / {s['round']}]\n{s['content']}"
        for s in state["statements"]
    )
    portfolio = state.get("user_portfolio", {})
    port_ctx  = f"\n[평균 매수가] {portfolio.get('avg_price',0):,}원\n" if portfolio else ""

    try:
        raw = _call(MODERATOR_SUMMARY_SYSTEM, MODERATOR_SUMMARY_HUMAN.format(
            all_statements=all_stmts,
            symbol=state["symbol"], symbol_name=state["symbol_name"],
            category=state["category"],
            price_context=state["price_context"],
            evidence_context=state["evidence_context"],
            portfolio_context=port_ctx,
        ), temp=0.4)
        parsed = _parse(raw, {"summary_content": raw, "key_points": []})
        summary = parsed.get("summary_content", raw)
        points = parsed.get("key_points", [])
    except Exception as e:
        logger.warning("[moderator_summary] LLM 호출 실패, fallback summary 사용: %s", e)
        summary, points = _build_summary_fallback(state)
        used_fallback_summary = True

    # 새 round 이름 → DB enum 호환 매핑
    _round_map = {
        "claim":            "opening",
        "counter_rebuttal": "closing",
    }

    # debate_session upsert (FK 충족)
    try:
        from app.core.database import get_pool  # noqa
        _pool = await get_pool()
        async with _pool.acquire() as conn:
            # app_user upsert
            await conn.execute("""
                INSERT INTO app_user (id, email, password_hash)
                VALUES ($1::uuid, $2, 'test')
                ON CONFLICT (id) DO NOTHING
            """, state["user_id"], f"{state['user_id']}@test.local")
            # debate_session upsert
            await conn.execute("""
                INSERT INTO debate_session (id, user_id, symbol, category, status)
                VALUES ($1::uuid, $2::uuid, $3, $4, 'running')
                ON CONFLICT (id) DO NOTHING
            """, state["session_id"], state["user_id"], state["symbol"], state["category"])
    except Exception as e:
        logger.warning(f"[moderator_summary] session upsert 실패 (무시): {e}")

    # DB 저장
    for stmt in state["statements"]:
        stmt_id = await save_statement(
            session_id=state["session_id"], round_=_round_map.get(stmt["round"], stmt["round"]),
            round_order=stmt["round_order"], agent_role=stmt["agent_role"],
            content=stmt["content"], model_name=stmt.get("model_used", ""),
        )
        for ev in stmt.get("evidences", []):
            try:
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
            except Exception as e:
                logger.warning("[moderator_summary] evidence 저장 실패, statement는 유지: %s", e)

    await save_statement(
        session_id=state["session_id"], round_="summary",
        round_order=state["round_order"] + 1, agent_role="moderator",
        content=summary, model_name="gpt-4o-mini",
    )
    await save_moderator_summary(state["session_id"], summary, points)
    await update_session_status(state["session_id"], "completed")
    clear_checkpoint(state["session_id"])
    get_tracker().end_session(
        user_id=state["user_id"],
        symbol=state["symbol"],
        session_id=state["session_id"],
    )

    # RAGAS 사후 평가 — 백그라운드 실행 (토론 흐름 차단 안 함)
    try:
        if not used_fallback_summary:
            _schedule_background_task(
                _run_summary_eval(state["session_id"], state["statements"], summary),
                label="eval-summary",
            )
        else:
            logger.info("[moderator_summary] fallback summary 사용으로 summary RAGAS 평가는 건너뜀")
        from app.domain.evidence_retrieval import build_category_query
        eq = build_category_query(
            symbol=state["symbol"],
            symbol_name=state["symbol_name"],
            category=state["category"],
        )
        _schedule_background_task(
            _run_evidence_eval(
                state["session_id"],
                state.get("initial_evidences", []),
                eq,
                state.get("agenda", []),
            ),
            label="eval-evidence",
        )
    except Exception as e:
        logger.warning(f"[moderator_summary] RAGAS 평가 태스크 등록 실패 (무시): {e}")

    return {
        "statements": [{
            "agent_role": "moderator", "round": "summary",
            "round_order": state["round_order"] + 1,
            "content": summary, "model_used": "gpt-4o-mini", "evidences": [],
        }],
        "summary_content": summary,
        "key_points":      points,
        "current_round":   "summary",
    }


async def _run_summary_eval(session_id: str, statements: list, summary: str) -> None:
    """사회자 요약 품질 RAGAS 평가 — 백그라운드 태스크"""
    try:
        from app.domain.debate_evaluation import evaluate_summary_async
        await evaluate_summary_async(session_id, statements, summary)
    except Exception as e:
        logger.error(f"[eval] 요약 평가 실패: {e}")


async def _run_evidence_eval(
    session_id: str,
    initial_evidences: list,
    evidence_query: str,
    agenda: list,
) -> None:
    """검색 근거 품질 RAGAS 평가 — 백그라운드 태스크"""
    try:
        from app.domain.debate_evaluation import evaluate_evidence_async
        await evaluate_evidence_async(session_id, initial_evidences, evidence_query, agenda)
    except Exception as e:
        logger.error(f"[eval] 근거 품질 평가 실패: {e}")
