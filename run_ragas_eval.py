import warnings
warnings.filterwarnings("ignore", message="Field .* has conflict with protected namespace")

"""
run_ragas_eval.py — RAGAS 배치 평가 스크립트

사용법:
  python run_ragas_eval.py                          # golden set 전체 실행
  python run_ragas_eval.py --session <session_id>  # 특정 세션만 평가
  python run_ragas_eval.py --dry-run               # DB 저장 없이 출력만

출력:
  ragas-<git_sha>.json  (결과 파일)
"""
import argparse
import uuid
import asyncio
import json
import os
import subprocess
from datetime import datetime, timezone

os.environ["ANONYMIZED_TELEMETRY"] = "false"
os.environ["TOKENIZERS_PARALLELISM"] = "false"
from dotenv import load_dotenv
load_dotenv(".env.local")

import logging
logging.basicConfig(level=logging.WARNING, format="%(asctime)s [%(levelname)s] %(message)s")

from app.domain.debate_evaluation import evaluate_summary_async, evaluate_evidence_async


# ── Golden Q&A 세트 ────────────────────────────────────────────
# 실제 데이터 기반으로 확장 가능
GOLDEN_CASES = [
    {
        "case_id": "golden-001",
        "description": "삼성전자 financial — 영업이익 증가/매출 감소 요약 충실도",
        "statements": [
            {"agent_role": "bull", "round": "claim",            "topic_index": 0, "content": "삼성전자 2026Q1 영업이익 492,360억(▲108.6% QoQ). 원가 절감과 반도체 회복세로 수익성 개선."},
            {"agent_role": "bear", "round": "rebuttal",         "topic_index": 0, "content": "매출 1,092,779억(▼54.1% QoQ). 매출 급감은 구조적 수요 약화 신호."},
            {"agent_role": "bull", "round": "counter_rebuttal", "topic_index": 0, "content": "ROE 14.04%로 자본 효율성 개선. AI 수요 증가로 하반기 매출 반등 전망."},
            {"agent_role": "bear", "round": "counter_rebuttal", "topic_index": 0, "content": "PER/PBR 미제공. 일회성 비용 절감 효과가 지속 가능할지 불확실."},
        ],
        "summary": (
            "삼성전자는 매출 감소(▼54.1%)에도 영업이익이 108.6% 증가했다. "
            "Bull은 원가 절감과 반도체 회복을 긍정적으로 보고, "
            "Bear는 매출 감소를 구조적 문제로 지적한다."
        ),
        "evidences": [
            {"excerpt": "삼성전자 2026Q1 영업이익 492,360억, 전분기 대비 108.6% 증가.", "source_title": "분기보고서"},
            {"excerpt": "글로벌 반도체 수요 AI 서버 중심으로 2026년 하반기 회복 전망.", "source_title": "반도체 시장 리포트"},
        ],
        "evidence_query": "삼성전자 실적 재무 분기보고서 매출 영업이익 순이익",
        "agenda": [
            "쟁점1: 매출 감소와 영업이익 증가의 원인 및 향후 전망",
            "쟁점2: 기술적 지표와 주가 흐름",
            "쟁점3: 재무 지표(ROE, PER, PBR) 평가",
        ],
        "expected_summary_faithfulness_min": 0.6,
        "expected_evidence_precision_min":   0.0,   # ChromaDB 없는 환경에서 0도 허용
    },
]


def _git_sha() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"]).decode().strip()
    except Exception:
        return "unknown"


async def run_case(case: dict, dry_run: bool) -> dict:
    # golden case는 case_id 기반 결정론적 UUID 생성 (재현 가능)
    session_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"ragas-{case['case_id']}"))
    print(f"\n▶ {case['case_id']}: {case['description']}")

    summary_result = await evaluate_summary_async(
        session_id,
        case["statements"],
        case["summary"],
        save=not dry_run,
    )
    evidence_result = await evaluate_evidence_async(
        session_id,
        case["evidences"],
        case["evidence_query"],
        case["agenda"],
        save=not dry_run,
    )

    faith = summary_result.faithfulness
    prec  = evidence_result.avg_precision
    faith_pass = faith is not None and faith >= case.get("expected_summary_faithfulness_min", 0)
    prec_pass  = prec  is None or prec >= case.get("expected_evidence_precision_min", 0)

    status = "PASS" if (faith_pass and prec_pass) else "FAIL"
    print(f"  summary_faithfulness : {faith:.3f} {'✅' if faith_pass else '❌'}" if faith is not None else "  summary_faithfulness : None ⚠️")
    print(f"  evidence_precision   : {prec:.3f} {'✅' if prec_pass else '❌'}"  if prec  is not None else "  evidence_precision   : None ⚠️")
    print(f"  → {status}")

    return {
        "case_id":                  case["case_id"],
        "description":              case["description"],
        "summary_faithfulness":     faith,
        "evidence_precision":       prec,
        "faith_pass":               faith_pass,
        "prec_pass":                prec_pass,
        "status":                   status,
    }


async def run_session(session_id: str, dry_run: bool) -> dict:
    """DB에서 세션 발언/요약을 읽어 평가."""
    from app.core.database import get_pool

    pool = await get_pool()
    async with pool.acquire() as conn:
        stmts = await conn.fetch("""
            SELECT agent_role, round, content,
                   ROW_NUMBER() OVER (ORDER BY round_order) - 1 AS topic_index
            FROM agent_statement
            WHERE session_id = $1::uuid
            ORDER BY round_order
        """, session_id)
        summary_row = await conn.fetchrow("""
            SELECT summary_content FROM moderator_summary WHERE session_id = $1::uuid
        """, session_id)
        session_row = await conn.fetchrow("""
            SELECT ds.symbol, ds.category,
                   COALESCE(tm.name, ds.symbol) AS symbol_name
            FROM debate_session ds
            LEFT JOIN ticker_metadata tm ON tm.symbol = ds.symbol
            WHERE ds.id = $1::uuid
        """, session_id)

    if not stmts or not summary_row or not session_row:
        print(f"  세션 {session_id} 데이터 없음")
        return {"case_id": session_id, "status": "SKIP"}

    statements  = [dict(s) for s in stmts]
    summary     = summary_row["summary_content"]
    symbol      = session_row["symbol"]
    symbol_name = session_row["symbol_name"]
    category    = session_row["category"]
    # build_category_query 직접 호출 대신 간단한 쿼리 조합 (임포트 의존성 최소화)
    eq = f"{symbol_name}({symbol}) {category} 실적 재무 뉴스 공시"

    print(f"\n▶ session={session_id} ({symbol} / {category})")

    sr = await evaluate_summary_async(session_id, statements, summary, save=not dry_run)
    er = await evaluate_evidence_async(session_id, [], eq, [], save=not dry_run)

    print(f"  summary_faithfulness : {sr.faithfulness}")
    print(f"  evidence_precision   : {er.avg_precision}")

    return {
        "case_id":              session_id,
        "summary_faithfulness": sr.faithfulness,
        "evidence_precision":   er.avg_precision,
        "status":               "DONE",
    }


async def main(args):
    sha = _git_sha()
    out_path = f"ragas-{sha}.json"
    results = []

    if args.session:
        results.append(await run_session(args.session, args.dry_run))
    else:
        for case in GOLDEN_CASES:
            results.append(await run_case(case, args.dry_run))

    passed = sum(1 for r in results if r.get("status") == "PASS")
    total  = len(results)
    print(f"\n{'='*50}")
    print(f"결과: {passed}/{total} PASS")
    print(f"{'='*50}")

    report = {
        "git_sha":    sha,
        "eval_at":    datetime.now(timezone.utc).isoformat(),
        "dry_run":    args.dry_run,
        "summary":    {"passed": passed, "total": total},
        "results":    results,
    }

    if not args.dry_run:
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        print(f"리포트 저장: {out_path}")
    else:
        print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--session",  help="특정 세션 ID 평가")
    parser.add_argument("--dry-run",  action="store_true", help="DB 저장 없이 출력만")
    asyncio.run(main(parser.parse_args()))
