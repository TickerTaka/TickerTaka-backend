# test_ragas.py
"""RAGAS 평가 함수 단독 테스트 (ChromaDB 데이터 없이도 동작 확인)"""
import warnings
warnings.filterwarnings("ignore", message="Field .* has conflict with protected namespace")
import asyncio
import os
os.environ["ANONYMIZED_TELEMETRY"] = "false"
os.environ["TOKENIZERS_PARALLELISM"] = "false"
from dotenv import load_dotenv
load_dotenv(".env.local")
import logging
logging.basicConfig(level=logging.WARNING)

from app.domain.debate_evaluation import evaluate_summary_async, evaluate_evidence_async


# ── 더미 데이터 ────────────────────────────────────────────────
DUMMY_STATEMENTS = [
    {"agent_role": "bull", "round": "claim",            "topic_index": 0, "content": "삼성전자의 영업이익이 108.6% 증가했습니다. 반도체 업황 회복이 기대됩니다."},
    {"agent_role": "bear", "round": "rebuttal",         "topic_index": 0, "content": "매출이 54.1% 감소한 것은 심각한 경고 신호입니다."},
    {"agent_role": "bull", "round": "counter_rebuttal", "topic_index": 0, "content": "원가 절감 효과로 영업이익은 견조하게 유지될 것입니다."},
    {"agent_role": "bear", "round": "counter_rebuttal", "topic_index": 0, "content": "일시적 비용 절감은 지속 가능한 성장이 아닙니다."},
]

DUMMY_SUMMARY = (
    "삼성전자는 매출 감소에도 영업이익이 108.6% 증가했다. "
    "Bull은 원가 절감과 반도체 회복을 긍정적으로 보고, "
    "Bear는 매출 54.1% 감소를 구조적 문제로 지적한다."
)

DUMMY_EVIDENCES = [
    {"excerpt": "삼성전자 2026년 1분기 영업이익 492,360억 원으로 전분기 대비 108.6% 증가. 반도체 부문 회복세.", "source_title": "삼성전자 분기보고서"},
    {"excerpt": "글로벌 반도체 시장 2026년 하반기 회복 전망. AI 수요 급증이 주된 동인.", "source_title": "반도체 시장 리포트"},
    {"excerpt": "삼성전자 HBM3E 공급 계약 확대. AI 서버용 메모리 수요 증가 수혜.", "source_title": "IT 뉴스"},
]

DUMMY_AGENDA = [
    "쟁점1: 매출 감소와 영업이익 증가의 원인 및 향후 전망",
    "쟁점2: 기술적 지표와 주가 흐름",
    "쟁점3: 재무 지표(ROE, PER, PBR) 평가",
]

DUMMY_QUERY = "삼성전자 실적 재무 분기보고서 매출 영업이익 순이익 부채"


async def main():
    session_id = "test-ragas-000"

    print("\n[1] 요약 품질 평가 (Faithfulness)")
    print("  context = 토론 발언, answer = 사회자 요약")
    result1 = await evaluate_summary_async(session_id, DUMMY_STATEMENTS, DUMMY_SUMMARY)
    print(f"  → faithfulness={result1.faithfulness}")

    print("\n[2] 검색 근거 품질 평가 (Context Precision)")
    print("  question = 카테고리 쿼리, contexts = 더미 뉴스/공시 청크")
    result2 = await evaluate_evidence_async(session_id, DUMMY_EVIDENCES, DUMMY_QUERY, DUMMY_AGENDA)
    print(f"  → avg_precision={result2.avg_precision}")


if __name__ == "__main__":
    asyncio.run(main())
