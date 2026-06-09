"""
RAGAS 회귀 테스트 — golden Q&A 기반 재현성 검증.

실행:
  pytest tests/test_agents/test_ragas_regression.py -v          # 전체
  pytest tests/test_agents/test_ragas_regression.py -v -k 001   # 특정 케이스

주의: LLM 호출 포함으로 케이스당 약 1분 소요.
      CI 기본 스위트에서는 @pytest.mark.slow 로 분리 가능.
"""
import warnings
warnings.filterwarnings("ignore", message="Field .* has conflict with protected namespace")

import os
import uuid
import asyncio
import pytest

os.environ.setdefault("ANONYMIZED_TELEMETRY", "false")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

# golden set을 배치 스크립트와 공유 (단일 진실 공급원)
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "../../.env.local"))

from run_ragas_eval import GOLDEN_CASES
from app.domain.debate_evaluation import evaluate_summary_async, evaluate_evidence_async


def _session_id(case_id: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, f"ragas-{case_id}"))


# ── 1. 요약 Faithfulness ─────────────────────────────────────

@pytest.mark.parametrize("case", GOLDEN_CASES, ids=[c["case_id"] for c in GOLDEN_CASES])
def test_summary_faithfulness(case):
    """요약 Faithfulness가 기준값 이상이어야 한다."""
    sid = _session_id(case["case_id"])
    result = asyncio.run(
        evaluate_summary_async(
            sid, case["statements"], case["summary"],
            agenda=case.get("agenda", []), save=False,
        )
    )
    min_score = case.get("expected_summary_faithfulness_min", 0)
    assert result.faithfulness is not None, \
        f"[{case['case_id']}] faithfulness 평가 실패 (None) — error: {result.error}"
    assert result.faithfulness >= min_score, (
        f"[{case['case_id']}] faithfulness {result.faithfulness:.3f} < 기준 {min_score}"
    )


# ── 2. 요약 Answer Relevancy ──────────────────────────────────

@pytest.mark.parametrize("case", GOLDEN_CASES, ids=[c["case_id"] for c in GOLDEN_CASES])
def test_summary_answer_relevancy(case):
    """요약 Answer Relevancy가 기준값 이상이어야 한다."""
    sid = _session_id(case["case_id"])
    result = asyncio.run(
        evaluate_summary_async(
            sid, case["statements"], case["summary"],
            agenda=case.get("agenda", []), save=False,
        )
    )
    min_score = case.get("expected_summary_answer_relevancy_min", 0)
    if result.answer_relevancy is None:
        pytest.skip(
            f"[{case['case_id']}] answer_relevancy 평가 실패 (None) — error: {result.error}"
        )
    assert result.answer_relevancy >= min_score, (
        f"[{case['case_id']}] answer_relevancy {result.answer_relevancy:.3f} < 기준 {min_score}"
    )


# ── 3. 근거 Context Precision ────────────────────────────────

@pytest.mark.parametrize("case", GOLDEN_CASES, ids=[c["case_id"] for c in GOLDEN_CASES])
def test_evidence_precision(case):
    """근거 Context Precision이 기준값 이상이어야 한다."""
    sid = _session_id(case["case_id"])
    result = asyncio.run(
        evaluate_evidence_async(
            sid,
            case["evidences"],
            case["evidence_query"],
            case["agenda"],
            save=False,
        )
    )
    min_score = case.get("expected_evidence_precision_min", 0)
    if result.avg_precision is None:
        pytest.skip(
            f"[{case['case_id']}] context_precision 평가 실패 (None) "
            "— ChromaDB 없는 환경이거나 evidence가 비어있음"
        )
    assert result.avg_precision >= min_score, (
        f"[{case['case_id']}] precision {result.avg_precision:.3f} < 기준 {min_score}"
    )
