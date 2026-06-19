"""검색 IR 지표 평가 — reranker off vs on 을 nDCG/MRR/precision@k 로 비교 (항목9).

기존 eval_reranker_ab.py 가 순위변화·latency·pairwise judge 까지만 봤다면, 이 스크립트는
**쿼리별 골든 relevance 라벨**을 기준으로 표준 IR 지표를 계산해 "reranker 가 정답을
상위로 올리는가"를 정량 입증한다. context_precision(LLM·골든 부재 시 degenerate)의 한계를 보완.

흐름:
  1) 쿼리별로 후보 풀을 retrieval(off/on 합집합)로 모은다.
  2) 라벨 파일(reports/ir-golden-<symbol>.json)이 있으면 사용, 없으면 LLM 으로 초안 라벨 생성 후 저장
     → 사람이 그 파일을 열어 0/1 을 보정할 수 있다(golden = LLM 초안 + human review).
  3) off/on 각 랭킹에 대해 precision@k / MRR / nDCG@k 계산, 쿼리 평균을 비교.
  4) reports/ir-<symbol>-<sha>.json 저장.

사용:
  python -m scripts.eval_retrieval_ir 005380
  python -m scripts.eval_retrieval_ir 005380 --top-k 8 --candidates 60 --relabel
"""
from __future__ import annotations

import argparse
import json
import math
import os
import subprocess

from app.config import get_settings
from app.core.db import session_scope
from app.domain.evidence_retrieval import EvidenceRetrievalService

# 종목별 평가 쿼리(005380 현대차 기본). 다른 종목은 --queries 로 파일 지정 가능.
_DEFAULT_QUERIES: dict[str, list[str]] = {
    # 005380 실제 인덱스 내용(분기보고서·성과급논란·공급망화재·월드컵마케팅)에 맞춘 쿼리
    "005380": [
        "현대차 분기보고서 실적 영업이익 매출",
        "현대차 성과급 노조 이익 배분 논란",
        "현대차 부품 공장 화재 공급망 차질",
        "현대차 월드컵 마케팅 브랜드 응원 캠페인",
    ],
}


def _key(hit: dict) -> str:
    return hit.get("news_cache_id") or hit.get("filing_cache_id") or hit.get("source_url") or hit.get("source_title", "")


def _retrieve(symbol: str, query: str, top_k: int, reranker: bool) -> list[dict]:
    settings = get_settings()
    settings.rag_reranker_enabled = reranker
    with session_scope() as session:
        service = EvidenceRetrievalService(session)
        return service.search_symbol_evidence(query=query, symbol=symbol, top_k=top_k)


# ── IR 지표 (binary relevance) ──────────────────────────────────
def precision_at_k(ranked: list[str], rel: dict[str, int], k: int) -> float:
    if k <= 0:
        return 0.0
    return sum(rel.get(x, 0) for x in ranked[:k]) / k


def mrr(ranked: list[str], rel: dict[str, int]) -> float:
    for i, x in enumerate(ranked, start=1):
        if rel.get(x, 0) > 0:
            return 1.0 / i
    return 0.0


def _dcg(ranked: list[str], rel: dict[str, int], k: int) -> float:
    return sum(rel.get(x, 0) / math.log2(i + 1) for i, x in enumerate(ranked[:k], start=1))


def ndcg_at_k(ranked: list[str], rel: dict[str, int], k: int) -> float:
    ideal = sorted(rel.values(), reverse=True)
    idcg = sum(g / math.log2(i + 1) for i, g in enumerate(ideal[:k], start=1))
    return _dcg(ranked, rel, k) / idcg if idcg > 0 else 0.0


# ── LLM 초안 라벨링 ─────────────────────────────────────────────
def _llm_relevance(query: str, pool: list[dict]) -> dict[str, int]:
    """각 후보가 query 에 관련 있으면 1, 아니면 0 (LLM 초안)."""
    from langchain_core.messages import HumanMessage, SystemMessage
    from langchain_openai import ChatOpenAI

    settings = get_settings()
    llm = ChatOpenAI(
        model="openai/gpt-oss-120b:free",
        api_key=settings.openrouter_api_key,
        base_url=settings.openrouter_base_url,
        temperature=0,
        timeout=60,
        default_headers={"HTTP-Referer": "https://tickertaka.app", "X-Title": "TickerTaka IRLabel"},
    )
    system = (
        "너는 금융 검색결과 관련성 평가자다. 질의와 후보 문서가 주어지면, 그 문서가 질의에 "
        "직접 관련 있으면 1, 아니면 0 으로만 답하라. 숫자 1 또는 0 한 글자만 출력."
    )
    rel: dict[str, int] = {}
    for h in pool:
        excerpt = (h.get("excerpt") or h.get("source_title") or "")[:400]
        human = f"[질의]\n{query}\n\n[후보 문서]\n{excerpt}\n\n관련 있으면 1, 없으면 0:"
        try:
            ans = str(llm.invoke([SystemMessage(content=system), HumanMessage(content=human)]).content).strip()
            rel[_key(h)] = 1 if ans[:1] == "1" else 0
        except Exception:
            rel[_key(h)] = 0
    return rel


def _git_sha() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"]).decode().strip()
    except Exception:
        return "unknown"


def main() -> None:
    parser = argparse.ArgumentParser(description="검색 IR 지표(off vs on)")
    parser.add_argument("symbol", nargs="?", default="005380")
    parser.add_argument("--top-k", type=int, default=8)
    parser.add_argument("--candidates", type=int, default=60, help="lexical candidate limit 오버라이드")
    parser.add_argument("--relabel", action="store_true", help="기존 라벨 무시하고 LLM 재라벨")
    args = parser.parse_args()

    settings = get_settings()
    settings.rag_hybrid_enabled = True
    settings.rag_lexical_candidate_limit = args.candidates

    queries = _DEFAULT_QUERIES.get(args.symbol)
    if not queries:
        print(f"🚫 {args.symbol} 기본 쿼리 없음. _DEFAULT_QUERIES 에 추가하세요.")
        return

    os.makedirs("reports", exist_ok=True)
    label_path = f"reports/ir-golden-{args.symbol}.json"
    labels: dict[str, dict[str, int]] = {}
    if os.path.exists(label_path) and not args.relabel:
        labels = json.load(open(label_path, encoding="utf-8"))
        print(f"라벨 로드: {label_path} (보정하려면 이 파일 편집 후 재실행)")

    # warmup
    _retrieve(args.symbol, queries[0], args.top_k, reranker=False)

    per_query = []
    for q in queries:
        hits_off = _retrieve(args.symbol, q, args.top_k, reranker=False)
        hits_on = _retrieve(args.symbol, q, args.top_k, reranker=True)
        rank_off = [_key(h) for h in hits_off]
        rank_on = [_key(h) for h in hits_on]

        # 후보 풀 = off/on 합집합
        pool_map = {_key(h): h for h in hits_off + hits_on}
        if q not in labels:
            print(f"  LLM 라벨링: '{q[:30]}...' (후보 {len(pool_map)}개)")
            labels[q] = _llm_relevance(q, list(pool_map.values()))
        rel = {k: int(v) for k, v in labels[q].items()}

        k = args.top_k
        m_off = {"p@k": precision_at_k(rank_off, rel, k), "mrr": mrr(rank_off, rel), "ndcg": ndcg_at_k(rank_off, rel, k)}
        m_on = {"p@k": precision_at_k(rank_on, rel, k), "mrr": mrr(rank_on, rel), "ndcg": ndcg_at_k(rank_on, rel, k)}
        per_query.append({"query": q, "n_relevant": sum(rel.values()), "off": m_off, "on": m_on})
        print(f"\n[{q[:40]}]  관련 {sum(rel.values())}개")
        print(f"   off: p@{k}={m_off['p@k']:.3f} mrr={m_off['mrr']:.3f} ndcg={m_off['ndcg']:.3f}")
        print(f"   on : p@{k}={m_on['p@k']:.3f} mrr={m_on['mrr']:.3f} ndcg={m_on['ndcg']:.3f}")

    json.dump(labels, open(label_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    # 쿼리 평균
    def _avg(side: str, metric: str) -> float:
        return sum(r[side][metric] for r in per_query) / len(per_query)

    avg = {s: {m: _avg(s, m) for m in ("p@k", "mrr", "ndcg")} for s in ("off", "on")}
    print(f"\n{'='*52}\n평균(쿼리 {len(per_query)}개), k={args.top_k}")
    print(f"   reranker OFF : p@k={avg['off']['p@k']:.3f}  mrr={avg['off']['mrr']:.3f}  ndcg={avg['off']['ndcg']:.3f}")
    print(f"   reranker ON  : p@k={avg['on']['p@k']:.3f}  mrr={avg['on']['mrr']:.3f}  ndcg={avg['on']['ndcg']:.3f}")
    d_ndcg = avg["on"]["ndcg"] - avg["off"]["ndcg"]
    print(f"   Δ nDCG (on-off) = {d_ndcg:+.3f}  → {'reranker 개선 ✅' if d_ndcg > 0.01 else ('악화 🚨' if d_ndcg < -0.01 else '차이 미미 ➖')}")

    sha = _git_sha()
    out = {
        "symbol": args.symbol, "git_sha": sha, "top_k": args.top_k,
        "candidates": args.candidates, "per_query": per_query, "avg": avg,
        "label_source": "LLM 초안 + human review (reports/ir-golden-*.json)",
    }
    out_path = f"reports/ir-{args.symbol}-{sha}.json"
    json.dump(out, open(out_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"\n저장: {out_path}  / 라벨: {label_path}")


if __name__ == "__main__":
    main()
