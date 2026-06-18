"""
토론 품질 사후 평가 — RAGAS 기반.

1. evaluate_summary_async  : 사회자 요약 ← 발언
   - Faithfulness      : 요약이 토론 발언 내용에 충실한가
   - Answer Relevancy  : 요약이 토론 의제에 관련성이 있는가
2. evaluate_evidence_async : 검색된 근거 ← 토론 주제 Context Precision

토론 완료 후 백그라운드에서 비동기 실행. 실시간 레이턴시 영향 없음.
LLM: OpenRouter 무료 모델 사용 (비용 절감).
Embeddings: sentence-transformers 다국어 모델 (answer_relevancy용).
평가 점수는 debate_eval_result 테이블에 저장됨.
"""
from __future__ import annotations
import asyncio
import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

_EVAL_MODEL      = "openai/gpt-oss-120b:free"
_EMBED_MODEL     = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"


# ── 결과 타입 ────────────────────────────────────────────────

@dataclass
class SummaryEvalResult:
    session_id:        str
    faithfulness:      float | None
    answer_relevancy:  float | None = None
    error:             str | None = None


@dataclass
class EvidenceEvalResult:
    session_id:    str
    avg_precision: float | None
    per_statement: list[dict] = field(default_factory=list)
    error:         str | None = None


# ── 공통: LLM / Embeddings 팩토리 ────────────────────────────

def _make_eval_llm():
    import warnings
    warnings.filterwarnings("ignore", message="Field .* has conflict with protected namespace")
    from ragas.llms import LangchainLLMWrapper
    from langchain_openai import ChatOpenAI
    from app.config import get_settings
    settings = get_settings()
    return LangchainLLMWrapper(ChatOpenAI(
        model=_EVAL_MODEL,
        api_key=settings.openrouter_api_key,
        base_url=settings.openrouter_base_url,
        temperature=0,
        max_retries=2,
        timeout=60,
        default_headers={
            "HTTP-Referer": "https://tickertaka.app",
            "X-Title": "TickerTaka Eval",
        },
    ))


def _make_eval_embeddings():
    """answer_relevancy 계산용 다국어 sentence-transformers 임베딩."""
    import warnings
    warnings.filterwarnings("ignore", message="Field .* has conflict with protected namespace")
    from ragas.embeddings import LangchainEmbeddingsWrapper
    from langchain_huggingface import HuggingFaceEmbeddings
    return LangchainEmbeddingsWrapper(
        HuggingFaceEmbeddings(model_name=_EMBED_MODEL)
    )


# ── DB 저장 ───────────────────────────────────────────────────

async def _save_eval_result(
    session_id: str,
    eval_type: str,
    score: float | None,
    error: str | None = None,
) -> None:
    """평가 점수를 debate_eval_result 테이블에 저장."""
    try:
        from app.core.database import get_pool
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO debate_eval_result (session_id, eval_type, score, model_used, error)
                VALUES ($1::uuid, $2, $3, $4, $5)
            """, session_id, eval_type, score, _EVAL_MODEL, error)
        logger.info(f"[eval] DB 저장 완료 — {eval_type} score={score} session={session_id}")
    except Exception as e:
        logger.error(f"[eval] DB 저장 실패 (무시): {e}")


# ── 1. 사회자 요약 품질 ───────────────────────────────────────

async def evaluate_summary_async(
    session_id: str,
    statements: list[dict],
    summary: str,
    agenda: list[str] | None = None,
    save: bool = True,
) -> SummaryEvalResult:
    """
    사회자 요약을 RAGAS Faithfulness + Answer Relevancy로 평가.
    - faithfulness    : context=토론 발언, answer=요약 → 요약이 발언에 충실한가
    - answer_relevancy: question=의제, answer=요약, contexts=발언 → 의제에 관련성 있는가
    save=False 이면 DB 저장 생략 (dry-run 용).
    """
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(
        None, _run_summary_ragas, session_id, statements, summary, agenda or []
    )
    if save:
        await _save_eval_result(
            session_id, "summary_faithfulness", result.faithfulness,
            result.error if result.faithfulness is None else None,
        )
        await _save_eval_result(
            session_id, "summary_answer_relevancy", result.answer_relevancy,
            result.error if result.answer_relevancy is None else None,
        )
    return result


def _run_summary_ragas(session_id, statements, summary, agenda) -> SummaryEvalResult:
    try:
        from ragas import evaluate
        from ragas.metrics import faithfulness, answer_relevancy
        from datasets import Dataset

        debate_text = "\n\n".join(
            f"[{s['agent_role'].upper()} / 주제{s.get('topic_index', 0)+1} / {s.get('round', '')}]\n{s['content']}"
            for s in statements
            if s["agent_role"] in ("bull", "bear") and s.get("content")
        )
        if not debate_text or not summary:
            return SummaryEvalResult(
                session_id=session_id, faithfulness=None,
                answer_relevancy=None, error="missing data",
            )

        # answer_relevancy의 question: 자연스러운 단일 질문으로 표현
        # RAGAS는 요약에서 질문을 역생성하여 이 question과 임베딩 유사도를 계산
        if agenda:
            agenda_str = ", ".join(agenda)
            question = f"다음 의제들에 대해 Bull과 Bear의 주요 논점과 핵심 쟁점을 요약하라: {agenda_str}"
        else:
            question = "토론에서 Bull과 Bear의 핵심 논점과 쟁점은 무엇인가?"

        logger.info(f"[eval-summary] 평가 시작 (faithfulness + answer_relevancy) — session={session_id}")

        dataset = Dataset.from_dict({
            "question": [question],
            "answer":   [summary],
            "contexts": [[debate_text]],
        })
        result = evaluate(
            dataset=dataset,
            metrics=[faithfulness, answer_relevancy],
            llm=_make_eval_llm(),
            embeddings=_make_eval_embeddings(),
            raise_exceptions=False,
        )
        df = result.to_pandas()
        faith_score = df["faithfulness"].tolist()[0]
        relev_score = df["answer_relevancy"].tolist()[0]

        faith_f = float(faith_score) if faith_score is not None else None
        relev_f = float(relev_score) if relev_score is not None else None

        def _level(s):
            if s is None: return "⚠️ None"
            return "✅ 양호" if s >= 0.7 else ("⚠️ 주의" if s >= 0.4 else "🚨 경고")

        logger.warning(
            f"[eval-summary] faithfulness={faith_f:.3f} {_level(faith_f)} | "
            f"answer_relevancy={relev_f:.3f} {_level(relev_f)} "
            f"(session={session_id})"
            if faith_f is not None and relev_f is not None else
            f"[eval-summary] 부분 실패 faith={faith_f} relev={relev_f} (session={session_id})"
        )
        if faith_f is not None and faith_f < 0.4:
            logger.warning("[eval-summary] 요약이 토론 발언과 크게 다를 수 있습니다.")
        if relev_f is not None and relev_f < 0.4:
            logger.warning("[eval-summary] 요약이 토론 의제와 관련성이 낮습니다.")

        return SummaryEvalResult(
            session_id=session_id,
            faithfulness=faith_f,
            answer_relevancy=relev_f,
        )

    except Exception as e:
        logger.error(f"[eval-summary] 실패: {e}", exc_info=True)
        return SummaryEvalResult(
            session_id=session_id, faithfulness=None,
            answer_relevancy=None, error=str(e),
        )


# ── 2. 검색 근거 품질 ─────────────────────────────────────────

async def evaluate_evidence_async(
    session_id: str,
    initial_evidences: list[dict],
    evidence_query: str,
    agenda: list[str],
    save: bool = True,
) -> EvidenceEvalResult:
    """
    data_agent가 가져온 초기 evidence를 RAGAS Context Precision으로 평가.
    save=False 이면 DB 저장 생략 (dry-run 용).
    """
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(
        None, _run_evidence_ragas, session_id, initial_evidences, evidence_query, agenda
    )
    if save:
        await _save_eval_result(
            session_id, "evidence_precision", result.avg_precision, result.error
        )
    return result


def _run_evidence_ragas(session_id, initial_evidences, evidence_query, agenda) -> EvidenceEvalResult:
    try:
        from ragas import evaluate
        from ragas.metrics import context_precision
        from datasets import Dataset

        excerpts = [ev["excerpt"] for ev in initial_evidences if ev.get("excerpt")]
        if not excerpts:
            logger.warning("[eval-evidence] 평가 대상 없음 — news/filings 데이터도 없음")
            return EvidenceEvalResult(session_id=session_id, avg_precision=None, error="no evidence")

        logger.info(f"[eval-evidence] 평가 시작 — {len(excerpts)}개 청크 (session={session_id})")

        ground_truth = " | ".join(agenda) if agenda else evidence_query

        dataset = Dataset.from_dict({
            "question":     [evidence_query],
            "contexts":     [excerpts],
            "ground_truth": [ground_truth],
        })
        result = evaluate(
            dataset=dataset,
            metrics=[context_precision],
            llm=_make_eval_llm(),
            raise_exceptions=False,
        )
        score = result.to_pandas()["context_precision"].tolist()[0]
        score_f = float(score) if score is not None else None

        level = "✅ 양호" if (score_f or 0) >= 0.7 else ("⚠️ 주의" if (score_f or 0) >= 0.4 else "🚨 경고")
        logger.warning(
            f"[eval-evidence] context_precision={score_f:.3f} {level} "
            f"(청크 {len(excerpts)}개, session={session_id})" if score_f is not None else
            f"[eval-evidence] 평가 실패 (session={session_id})"
        )
        if score_f is not None and score_f < 0.4:
            logger.warning("[eval-evidence] 검색 근거가 주제와 관련성이 낮습니다. 임베딩/쿼리를 점검하세요.")

        return EvidenceEvalResult(session_id=session_id, avg_precision=score_f)

    except Exception as e:
        logger.error(f"[eval-evidence] 실패: {e}", exc_info=True)
        return EvidenceEvalResult(session_id=session_id, avg_precision=None, error=str(e))
