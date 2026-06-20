# app/agents/nodes/moderator_node.py
from __future__ import annotations
import json
import logging
import asyncio
from langchain_core.messages import SystemMessage, HumanMessage

from app.agents.debate_checkpoint import clear_checkpoint
from app.agents.state import DebateState
from app.agents.prompts.prompts import (
    MODERATOR_PRE_SYSTEM, MODERATOR_PRE_HUMAN,
    MODERATOR_CHECK_SYSTEM, MODERATOR_CHECK_HUMAN,
    MODERATOR_SUMMARY_SYSTEM, MODERATOR_SUMMARY_HUMAN,
    JUDGE_SYSTEM, JUDGE_HUMAN,
)
from app.config import get_settings
from app.core.debate_runtime_guard import get_tracker
from app.core.llm_factory import get_llm, invoke_with_fallback
from app.repositories.debate_repo import (
    save_statement, save_evidence,
    save_moderator_summary, update_session_status,
)

logger = logging.getLogger(__name__)


def _call(system: str, human: str, temp: float = 0.3, role: str = "moderator") -> str:
    """retry + fallback 체인으로 LLM 호출 (최대 3회 재시도, 실패 시 fallback 모델)."""
    return invoke_with_fallback(
        [SystemMessage(content=system), HumanMessage(content=human)],
        role=role,
        temperature=temp,
    )


def _parse(text: str, fallback: dict) -> dict:
    try:
        s = text.find("{"); e = text.rfind("}") + 1
        return json.loads(text[s:e]) if s >= 0 else fallback
    except Exception:
        return fallback


# sLLM이 JSON verdict="hallucination"으로 마크하면서 note 텍스트에는
# "일치", "사실", "ok" 등 ok 신호를 쓰는 불일치를 방어하는 키워드 목록.
_OK_SIGNALS = (
    "일치함", "일치한다", "사실임", "근거 있", "근거가 있", "근거 존재",
    "뉴스에 근거", "계산 일치", "수치는 사실", "검증 불가로 ok",
    "모두 ok", "모두ok", "검증 불가", "실제 데이터와 일치",
    "검색 결과와 일치", "뉴스·공시", "뉴스/공시",
    "근거가 있으면 반드시 ok", "all ok", "해당 수치는 사실",
)


def _sanitize_verdict(verdict: str, note: str, raw: str) -> str:
    """
    JSON verdict='hallucination'이지만 note 또는 raw 텍스트에
    ok 신호가 포함된 경우 'ok'로 정정.
    OpenRouter sLLM 계열의 JSON/텍스트 불일치 오류를 방어한다.
    """
    if verdict != "hallucination":
        return verdict
    combined = (note + " " + raw).lower()
    if any(sig.lower() in combined for sig in _OK_SIGNALS):
        logger.warning(
            "[moderator_check] JSON verdict=hallucination이지만 ok 신호 감지 → ok로 정정. note=%s",
            note[:150],
        )
        return "ok"
    return verdict


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
    end_notice = _format_debate_end_notice(state)
    summary = (
        "사회자 요약 생성 중 LLM 호출이 실패하여 축약 요약으로 대체했습니다. "
        f"{state['symbol_name']}({state['symbol']})에 대해 {len(non_moderator)}건의 찬반 발언이 기록되었고, "
        "최종 판단 전 개별 발언과 근거를 다시 확인해야 합니다."
        f"{(' ' + end_notice) if end_notice else ''}"
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
        except asyncio.CancelledError:
            pass  # 이벤트 루프 종료 시 정상 취소
        except Exception as exc:
            logger.error("[%s] 백그라운드 태스크 실패: %s", label, exc)

    task.add_done_callback(_log_task_result)


def _format_debate_end_notice(state: DebateState) -> str:
    ended_by = state.get("debate_ended_by") or ""
    reason = state.get("debate_end_reason") or ""
    if not ended_by:
        return ""
    role_label = "Bull" if ended_by == "bull" else "Bear" if ended_by == "bear" else ended_by
    if reason:
        return f"토론은 {role_label} 발언 검증 문제로 중단되었습니다: {reason}"
    return f"토론은 {role_label} 발언 검증 문제로 중단되었습니다."


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
            evidence_context=state.get("evidence_context", ""),
        ), temp=0.0)
    except Exception as e:
        logger.error(f"[moderator_check] 오류: {e}")
        return {"moderator_flag": "ok", "intervention_note": ""}

    parsed  = _parse(raw, {"verdict": "ok", "note": "", "corrected_fact": ""})
    verdict       = _sanitize_verdict(
        parsed.get("verdict", "ok"),
        parsed.get("note", ""),
        raw,
    )
    note          = parsed.get("note", "")
    corrected_fact = parsed.get("corrected_fact", "")

    hallucination_count      = state.get("hallucination_count", 0)
    bull_hallucination_count = state.get("bull_hallucination_count", 0)
    bear_hallucination_count = state.get("bear_hallucination_count", 0)
    extra = []

    if verdict == "hallucination":
        hallucination_count += 1
        last_role = last["agent_role"]
        if last_role == "bull":
            bull_hallucination_count += 1
            per_agent_count = bull_hallucination_count
        else:
            bear_hallucination_count += 1
            per_agent_count = bear_hallucination_count

        msg = f"[사회자 개입] {note}"
        if corrected_fact:
            msg += f"\n정정: {corrected_fact}"
        if per_agent_count >= 3:
            msg += f"\n토론 중단: {last_role} 발언에서 반복적인 검증 문제가 감지되었습니다."
        else:
            msg += f"\n발언을 수정해 주세요. ({last_role.upper()} 누적 {per_agent_count}회)"
        extra = [{
            "agent_role":  "moderator",
            "round":       last["round"],
            "round_order": state["round_order"] + 1,
            "topic_index": last.get("topic_index", state.get("current_topic_index", 0)),
            "content":     msg,
            "model_used":  get_settings().moderator_model,
            "evidences":   [],
        }]

        # Langfuse: 사회자 개입 기록
        _log_hallucination(
            session_id=state["session_id"],
            agent_role=last_role,
            round_=last.get("round", ""),
            topic_index=last.get("topic_index", 0),
            note=note,
            count=hallucination_count,
        )

    result = {
        "moderator_flag":           "intervene" if verdict == "hallucination" else "ok",
        "intervention_note":        note          if verdict == "hallucination" else "",
        "corrected_fact":           corrected_fact if verdict == "hallucination" else "",
        "hallucination_count":      hallucination_count,
        "bull_hallucination_count": bull_hallucination_count,
        "bear_hallucination_count": bear_hallucination_count,
        "statements":               extra,
        "round_order":              state["round_order"] + (1 if extra else 0),
    }

    # 환각 탐지 시 해당 에이전트의 교정 이력에 추가 (이후 모든 발언에 주입됨)
    if verdict == "hallucination":
        correction_entry = note + (f" → {corrected_fact}" if corrected_fact else "")
        if last["agent_role"] == "bull":
            result["bull_corrections"] = [correction_entry]
        else:
            result["bear_corrections"] = [correction_entry]
    if verdict == "hallucination" and per_agent_count >= 3:
        result["moderator_flag"] = "end"
        result["debate_ended_by"] = last["agent_role"]
        result["debate_end_reason"] = note or "반복적인 발언 검증 실패"
    return result


def _log_hallucination(
    session_id: str,
    agent_role: str,
    round_: str,
    topic_index: int,
    note: str,
    count: int,
) -> None:
    """Langfuse에 사회자 개입(환각) 이벤트 기록. 비활성화 시 no-op."""
    try:
        from langfuse import get_client
        lf = get_client()
        lf.create_score(
            session_id=session_id,
            name="hallucination",
            value=1.0,
            comment=f"[{agent_role.upper()} / {round_} / 주제{topic_index+1}] {note} (누적 {count}회)",
        )
    except Exception as e:
        logger.debug("[langfuse] score 기록 실패 (무시): %s", e)


# ── 3. Judge 판정 ────────────────────────────────────────────
def judge_agent_node(state: DebateState) -> dict:
    logger.info("[judge] 최종 판정")
    all_stmts = "\n\n".join(
        f"[{s['agent_role'].upper()} / {s['round']}]\n{s['content']}"
        for s in state["statements"]
        if s.get("agent_role") in ("bull", "bear", "moderator")
    )
    fallback = {
        "leaning": "mixed",
        "confidence": "low",
        "rationale": "Bull과 Bear 양쪽 모두 확인할 논점이 있어 한쪽으로 단정하기 어렵습니다.",
        "action_guidance": "매수 전에는 상승 근거가 실제 수치로 이어지는지, 보유 또는 매도 전에는 리스크가 단기 이슈인지 구조적 문제인지 확인하세요.",
    }

    try:
        raw = _call(JUDGE_SYSTEM, JUDGE_HUMAN.format(
            all_statements=all_stmts,
            symbol=state["symbol"],
            symbol_name=state["symbol_name"],
            category=state["category"],
            price_context=state["price_context"],
            financial_context=state["financial_context"],
            evidence_context=state["evidence_context"],
            debate_end_notice=_format_debate_end_notice(state) or "중단 없음",
        ), temp=0.2, role="judge")
        parsed = _parse(raw, fallback)
    except Exception as e:
        logger.warning("[judge] LLM 호출 실패, fallback 판정 사용: %s", e)
        parsed = fallback

    leaning = parsed.get("leaning", "mixed")
    if leaning not in ("bull", "bear", "mixed"):
        parsed["leaning"] = "mixed"
    confidence = parsed.get("confidence", "low")
    if confidence not in ("low", "medium", "high"):
        parsed["confidence"] = "low"
    return {"judge_result": parsed}


# ── 4. 최종 요약 + DB 저장 ────────────────────────────────
async def moderator_summary_node(state: DebateState) -> dict:
    logger.info("[moderator_summary] 요약 + DB 저장")
    used_fallback_summary = False

    all_stmts = "\n\n".join(
        f"[{s['agent_role'].upper()} / {s['round']}]\n{s['content']}"
        for s in state["statements"]
    )

    try:
        agenda_text = "\n".join(f"{i+1}. {a}" for i, a in enumerate(state.get("agenda") or []))
        raw = _call(MODERATOR_SUMMARY_SYSTEM, MODERATOR_SUMMARY_HUMAN.format(
            agenda=agenda_text or "의제 없음",
            all_statements=all_stmts,
            debate_end_notice=_format_debate_end_notice(state) or "중단 없음",
            symbol=state["symbol"], symbol_name=state["symbol_name"],
            category=state["category"],
            price_context=state["price_context"],
            financial_context=state["financial_context"],
            evidence_context=state["evidence_context"],
        ), temp=0.4)
        parsed = _parse(raw, {"summary_content": raw, "structured_summary": raw, "key_points": []})
        summary = parsed.get("summary_content", raw)           # 사용자 출력용 (투자 가이드 형식)
        structured_summary = parsed.get("structured_summary", summary)  # RAGAS 평가용 (의제별 구조)
        points = parsed.get("key_points", [])
    except Exception as e:
        logger.warning("[moderator_summary] LLM 호출 실패, fallback summary 사용: %s", e)
        summary, points = _build_summary_fallback(state)
        structured_summary = summary
        used_fallback_summary = True

    # 새 round 이름 → DB enum 호환 매핑
    _round_map = {
        "claim":            "opening",
        "counter_rebuttal": "closing",
    }

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
        content=summary, model_name=get_settings().moderator_model,
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
    debate_was_cut = bool(state.get("debate_ended_by"))
    try:
        if not used_fallback_summary and not debate_was_cut:
            _schedule_background_task(
                _run_summary_eval(
                    state["session_id"], state["statements"], structured_summary,
                    state.get("agenda", []),
                ),
                label="eval-summary",
            )
        elif debate_was_cut:
            logger.info("[moderator_summary] 토론 중단 케이스 — summary RAGAS 평가 건너뜀")
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
            "content": summary, "model_used": get_settings().moderator_model, "evidences": [],
        }],
        "summary_content": summary,
        "key_points":      points,
        "current_round":   "summary",
    }


async def _run_summary_eval(
    session_id: str, statements: list, summary: str, agenda: list
) -> None:
    """사회자 요약 품질 RAGAS 평가 — 백그라운드 태스크 (faithfulness + answer_relevancy)"""
    try:
        from app.domain.debate_evaluation import evaluate_summary_async
        await evaluate_summary_async(session_id, statements, summary, agenda)
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
