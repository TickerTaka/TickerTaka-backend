"""Reranker A/B 효과 검증 — RAG_RERANKER off vs on 비교.

validate_evidence_retrieval.py는 2건짜리 smoke test(경로 동작 확인)라
reranker 효과를 보여줄 수 없다. 이 스크립트는 실제 DB에 쌓인 news/filings를
후보로 써서 off/on의 (1) 순위 변화 (2) latency (3) context_precision(RAGAS)을
비교한다. 이 결과가 나와야 RAG_RERANKER_ENABLED 기본값/운영 전환을 판단할 수 있다.

사용:
    python -m scripts.eval_reranker_ab 000020
    python -m scripts.eval_reranker_ab 000020 --top-k 5 --candidates 60 --ragas
    python -m scripts.eval_reranker_ab 006360 --candidates 60 --judge

--ragas 는 OpenRouter LLM 호출이 필요하다(OPENROUTER_API_KEY). 생략하면
순위 변화 + latency만 측정한다(LLM 비용 0).

--judge 는 골든셋 없이 품질 방향을 보는 pairwise LLM judge다. 같은 질의에
off/on 두 랭킹을 LLM에 보여주고 어느 쪽이 더 관련 있는지 A/B로 판정하며,
위치 편향 제거를 위해 좌우를 바꿔 2회 평가한다. context_precision이
골든 정답 부재로 degenerate(off/on 모두 0)할 때의 대안 신호다.
"""
from __future__ import annotations

import argparse
import asyncio
import time
from uuid import uuid4

from app.config import get_settings
from app.core.db import session_scope
from app.domain.evidence_retrieval import (
    EvidenceRetrievalService,
    build_category_query,
)
from app.repositories.filing_cache_repository import FilingCacheRepository
from app.repositories.news_cache_repository import NewsCacheRepository

# reranker 효과는 후보군이 충분해야 드러난다. 이보다 적으면 비교가 무의미.
_MIN_CANDIDATES_FOR_SIGNAL = 8


def _retrieve(symbol: str, query: str, top_k: int) -> tuple[list[dict], float]:
    """단일 retrieval 실행 + 소요시간(ms). 현재 settings의 reranker 플래그를 그대로 사용."""
    started = time.perf_counter()
    with session_scope() as session:
        service = EvidenceRetrievalService(session)
        hits = service.search_symbol_evidence(query=query, symbol=symbol, top_k=top_k)
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    return hits, elapsed_ms


def _key(hit: dict) -> str:
    return hit.get("news_cache_id") or hit.get("filing_cache_id") or hit.get("source_url") or hit.get("source_title", "")


def _ranking(hits: list[dict]) -> list[tuple[str, str]]:
    return [(_key(h), h.get("source_title", "")) for h in hits]


def _count_candidates(symbol: str) -> int:
    with session_scope() as session:
        news = len(NewsCacheRepository(session).list_by_symbol(symbol))
        filings = len(FilingCacheRepository(session).list_by_symbol(symbol))
    return news + filings


def _symbols_with_data() -> list[str]:
    """DB에 news 또는 filing 캐시가 1건이라도 있는 종목 목록."""
    from sqlalchemy import distinct, select

    from app.models import FilingCache, NewsCache

    with session_scope() as session:
        news = set(session.scalars(select(distinct(NewsCache.symbol))))
        filings = set(session.scalars(select(distinct(FilingCache.symbol))))
    return sorted(news | filings)


async def _context_precision(symbol: str, query: str, hits: list[dict]) -> float | None:
    """주어진 (순서 있는) hits에 대해 RAGAS context_precision을 dry-run으로 계산."""
    from app.domain.debate_evaluation import evaluate_evidence_async

    result = await evaluate_evidence_async(
        session_id=str(uuid4()),
        initial_evidences=hits,
        evidence_query=query,
        agenda=[],          # ground_truth는 query로 폴백
        save=False,         # DB 저장 안 함
    )
    return result.avg_precision


def _format_ranking_for_judge(hits: list[dict]) -> str:
    return "\n".join(
        f"{i}. [{h.get('source_type', '?')}] {h.get('source_title', '')} — {(h.get('excerpt') or '')[:200]}"
        for i, h in enumerate(hits, start=1)
    )


def _judge_once(llm, query: str, list_a: str, list_b: str) -> str:
    """랭킹 A/B 중 더 나은 쪽을 LLM에 묻는다. 반환: 'A' | 'B' | 'TIE'."""
    from langchain_core.messages import HumanMessage, SystemMessage

    system = (
        "너는 금융 리서치 검색결과 평가자다. 같은 질의에 대한 두 근거 랭킹(A, B)을 보고 "
        "어느 쪽이 질의에 더 관련 있고 유용한 근거를 상위에 배치했는지 판정하라. "
        "반드시 A, B, TIE 중 하나의 토큰으로만 답하라."
    )
    human = (
        f"[질의]\n{query}\n\n[랭킹 A]\n{list_a}\n\n[랭킹 B]\n{list_b}\n\n"
        "어느 랭킹이 더 좋은가? (A / B / TIE)"
    )
    resp = str(llm.invoke([SystemMessage(content=system), HumanMessage(content=human)]).content).strip().upper()
    for token in ("TIE", "A", "B"):
        if token in resp:
            return token
    return "TIE"


def _pairwise_judge(query: str, hits_off: list[dict], hits_on: list[dict]) -> None:
    from langchain_openai import ChatOpenAI

    settings = get_settings()
    if not settings.openrouter_api_key:
        print("⚠️ OPENROUTER_API_KEY 없음 — judge 모드 생략.")
        return

    llm = ChatOpenAI(
        model="openai/gpt-oss-120b:free",
        api_key=settings.openrouter_api_key,
        base_url=settings.openrouter_base_url,
        temperature=0,
        timeout=60,
        default_headers={"HTTP-Referer": "https://tickertaka.app", "X-Title": "TickerTaka RerankerJudge"},
    )
    off_str = _format_ranking_for_judge(hits_off)
    on_str = _format_ranking_for_judge(hits_on)

    # 위치 편향 제거: (A=off, B=on) 1회 + (A=on, B=off) 1회
    v1 = _judge_once(llm, query, off_str, on_str)
    v2 = _judge_once(llm, query, on_str, off_str)
    wins = {"off": 0, "on": 0, "tie": 0}
    wins[{"A": "off", "B": "on", "TIE": "tie"}[v1]] += 1
    wins[{"A": "on", "B": "off", "TIE": "tie"}[v2]] += 1

    print(f"\n[pairwise judge] off={wins['off']} on={wins['on']} tie={wins['tie']}  (2회, 좌우 swap)")
    if wins["on"] > wins["off"]:
        print("→ reranker(on) 선호. 여러 종목에서 일관되면 품질 개선 신호.")
    elif wins["off"] > wins["on"]:
        print("→ rrf(off) 선호. reranker가 오히려 손해.")
    else:
        print("→ 무승부/혼조. 단일 종목으론 결론 불가 — 더 많은 종목으로 반복하세요.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Reranker off vs on A/B 비교")
    parser.add_argument("symbol", nargs="?", default="000020", help="종목 코드 (기본 000020)")
    parser.add_argument("--top-k", type=int, default=4)
    parser.add_argument("--candidates", type=int, default=None,
                        help="RAG_LEXICAL_CANDIDATE_LIMIT 임시 오버라이드 (후보군 확대)")
    parser.add_argument("--category", default="synthesis")
    parser.add_argument("--ragas", action="store_true", help="context_precision까지 측정 (LLM 호출)")
    parser.add_argument("--judge", action="store_true",
                        help="pairwise LLM judge로 off/on 품질 방향 판정 (골든셋 불필요, LLM 호출)")
    args = parser.parse_args()

    settings = get_settings()
    settings.rag_hybrid_enabled = True           # 비교 전제: hybrid on
    if args.candidates is not None:
        settings.rag_lexical_candidate_limit = args.candidates

    query = build_category_query(symbol=args.symbol, symbol_name=args.symbol, category=args.category)

    pool = _count_candidates(args.symbol)
    print(f"\n=== Reranker A/B — symbol={args.symbol} top_k={args.top_k} category={args.category} ===")
    print(f"후보 풀(news+filings, DB): {pool}건  (lexical_limit={settings.rag_lexical_candidate_limit})")
    if pool == 0:
        avail = _symbols_with_data()
        print("🚫 이 종목은 DB에 캐시된 news/filings가 0건 — 비교 불가.")
        if avail:
            shown = ", ".join(avail[:20]) + (" ..." if len(avail) > 20 else "")
            print(f"   데이터가 있는 종목: {shown}")
            print(f"   예) python -m scripts.eval_reranker_ab {avail[0]} --candidates 60 --ragas")
        else:
            print("   DB 전체에 news/filings 캐시가 없습니다. 먼저 수집/인덱싱을 돌려 데이터를 적재하세요.")
        return
    if pool < _MIN_CANDIDATES_FOR_SIGNAL:
        print(f"⚠️  후보가 {pool}건뿐 — {_MIN_CANDIDATES_FOR_SIGNAL}건 미만이면 reranker 효과가 거의 없습니다. "
              f"가능하면 데이터가 더 쌓인 종목으로 측정하세요.")

    # warmup: 임베딩 모델 + chroma 경로를 미리 데워 latency에서 cold start 노이즈 제거
    settings.rag_reranker_enabled = False
    _retrieve(args.symbol, query, args.top_k)

    # OFF (warm)
    hits_off, ms_off = _retrieve(args.symbol, query, args.top_k)
    # ON #1 cold: CrossEncoder 최초 로딩 포함 / ON #2 warm: 운영 시 순수 오버헤드
    settings.rag_reranker_enabled = True
    _, ms_on_cold = _retrieve(args.symbol, query, args.top_k)
    hits_on, ms_on = _retrieve(args.symbol, query, args.top_k)

    rank_off = _ranking(hits_off)
    rank_on = _ranking(hits_on)

    print(f"\n[OFF=rrf]  latency={ms_off:7.1f}ms  score_type={hits_off[0]['score_type'] if hits_off else '-'}")
    for i, (_, title) in enumerate(rank_off, start=1):
        print(f"   {i}. {title}")
    print(f"\n[ON=reranker] latency={ms_on:7.1f}ms  score_type={hits_on[0]['score_type'] if hits_on else '-'}")
    for i, (_, title) in enumerate(rank_on, start=1):
        print(f"   {i}. {title}")

    keys_off = [k for k, _ in rank_off]
    keys_on = [k for k, _ in rank_on]
    if keys_off == keys_on:
        print("\n순위 변화: 없음 (off/on 동일) — 이 케이스에선 reranker가 순서를 바꾸지 못함")
    else:
        moved = sum(1 for a, b in zip(keys_off, keys_on) if a != b)
        print(f"\n순위 변화: 있음 — {moved}/{len(keys_on)} 위치 변경")

    print(f"latency: OFF={ms_off:.1f}ms | ON cold(첫 모델 로딩)={ms_on_cold:.1f}ms | ON warm={ms_on:.1f}ms")
    print(f"steady-state 오버헤드 (ON warm - OFF): {ms_on - ms_off:+.1f}ms  "
          f"(cold start는 프로세스당 1회: {ms_on_cold:.1f}ms)")

    if args.ragas:
        import math

        cp_off = asyncio.run(_context_precision(args.symbol, query, hits_off))
        cp_on = asyncio.run(_context_precision(args.symbol, query, hits_on))
        print(f"\ncontext_precision  OFF={cp_off}  ON={cp_on}")

        def _ok(x) -> bool:
            return isinstance(x, (int, float)) and not math.isnan(x)

        if _ok(cp_off) and _ok(cp_on):
            delta = cp_on - cp_off
            if cp_off == 0.0 and cp_on == 0.0:
                # reference 기반 context_precision은 골든 정답이 없으면(키워드 쿼리를
                # ground_truth로 쓰면) off/on 모두 0으로 degenerate → 변별 불가.
                print("⚠️ off/on 모두 0.0 — reranker 무효가 아니라 지표 한계입니다. "
                      "context_precision은 reference(골든 정답)가 있어야 변별됩니다. "
                      "reranker 품질은 쿼리별 골든 relevance + nDCG/precision@k 또는 "
                      "pairwise LLM judge로 측정하세요.")
            else:
                verdict = "개선 ✅" if delta > 0.02 else ("악화 🚨" if delta < -0.02 else "차이 미미 ➖")
                print(f"Δ context_precision = {delta:+.4f}  → {verdict}")
                print("\n판단 기준: 여러 종목에서 Δ가 안정적으로 +이고 latency 오버헤드가 허용 범위면 "
                      "운영 env에서 RERANKER_ENABLED=true 검토. 그렇지 않으면 기본 off 유지.")
        else:
            print("⚠️ context_precision이 NaN/None — RAGAS 심사 LLM 호출이 실패했습니다 "
                  "(예: OPENROUTER_API_KEY 401 'User not found'). 유효한 키 설정 후 재측정하세요. "
                  "지금 수치로는 품질 판단 불가.")
    elif not args.judge:
        print("\n(품질은 --ragas(context_precision) 또는 --judge(pairwise LLM)로 측정. "
              "정량/방향 증적 없이 default 전환은 보류 권장.)")

    if args.judge:
        _pairwise_judge(query, hits_off, hits_on)


if __name__ == "__main__":
    main()
