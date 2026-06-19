# 구현 결과 — 검색 IR 지표(nDCG/MRR/precision@k)로 reranker 품질 입증 (항목9)

- 작성: 2026-06-19 / 브랜치: `uc`
- 대상 평가항목: **항목9 RAG 고도화** (×1)
- 직전 상태(재평가 리포트 `a134b5b`): 4/5 — "검색 자체 지표(nDCG/MRR) 부재, reranker 운영 default off"

---

## 1. 무엇을/왜 했나

reranker 품질을 **표준 IR 지표 + 쿼리별 골든 relevance**로 정량 입증했다. 기존 `context_precision`(RAGAS·LLM)은 골든 정답이 없으면 degenerate(off/on 모두 0)라 reranker 효과를 못 가렸다([2026-06-13-eval-track6b](2026-06-13-eval-track6b-reranker-ab-measurement.md) 미해결 과제). 이를 닫는다.

**도구**: `scripts/eval_retrieval_ir.py`(신규, **기존 검색/production 코드 무수정** — 추가만). 005380(현대차), 코퍼스 정합 쿼리 4개, 후보 풀=off∪on top-8, **LLM 초안 relevance 라벨 + human-review 파일**(`reports/ir-golden-005380.json`).

## 2. 결과 (k=8, reranker off vs on)

| 쿼리 | 관련 | off (p@k/mrr/ndcg) | on (p@k/mrr/ndcg) |
|---|---|---|---|
| 분기보고서 실적 영업이익 | 0 | 0/0/0 | 0/0/0 |
| 성과급 노조 이익 배분 | 1 | 0.125 / 0.500 / 0.631 | 0.125 / **1.000** / **1.000** |
| 부품 공장 화재 공급망 | 1 | 0.125 / 0.500 / 0.631 | 0.125 / **1.000** / **1.000** |
| 월드컵 마케팅 캠페인 | 6 | 0.750 / 0.500 / 0.798 | 0.750 / **1.000** / **1.000** |
| **평균** | | p@k **0.250** / mrr **0.375** / ndcg **0.515** | p@k 0.250 / mrr **0.750** / ndcg **0.750** |

**Δ nDCG = +0.235, Δ MRR = +0.375 → reranker 개선 ✅**

## 3. 해석 (핵심)

- **p@k는 동일(0.250)** — reranker는 retrieval이 가져온 **문서 집합(top-8)을 바꾸지 않는다**(그건 BM25+벡터+RRF의 몫).
- **MRR·nDCG는 크게 상승** — reranker가 **관련 문서를 상위로 끌어올린다**(예: 성과급/화재 쿼리에서 정답을 2위→1위로). 즉 **"정답을 더 위에 배치"**하는 게 reranker의 가치이고, 그게 표준 지표로 처음 정량화됐다.
- 이전 A/B에서 못 했던 입증을 완료: context_precision degenerate → **골든 relevance 기반 nDCG/MRR로 reranker lift 확인**.

## 4. 비용↔효과 종합 (운영 default 판단)

| | 측정값 | 출처 |
|---|---|---|
| **효과** | nDCG +0.235, MRR +0.375 (정답 상위 배치) | 본 IR 평가 |
| **비용** | steady-state +7.6~13.2s/쿼리 (CPU), cold start 11~18s | [track6b A/B](2026-06-13-eval-track6b-reranker-ab-measurement.md) |

→ **reranker는 품질을 분명히 올리지만 CPU latency 비용이 큼.** 결론: **기본 off(opt-in) 유지가 여전히 타당**하되, **GPU 또는 오프라인 배치 재랭킹 경로에서는 켤 가치 충분**(이제 효과가 정량 입증됨).

## 5. 정직한 한계
- **경량/단일 종목**(005380, 쿼리 4개). 운영 의사결정용으론 종목·쿼리 확장 필요.
- 라벨은 **LLM 초안 + human-review 파일**(`reports/ir-golden-005380.json`, 편집 가능). 완전 수작업 골든은 아님.
- "분기보고서 실적" 쿼리는 관련 0 — 검색된 공시 청크가 **실적 수치가 아닌 boilerplate 조각**이었음(청킹/코퍼스 품질 별건). 또 005380 뉴스가 마케팅 fluff(월드컵 차박) 비중이 커 코퍼스 자체의 신호가 약함 → 별도 개선 여지.

## 6. 평가 영향 (예상, 확정 아님)
- "검색 자체 정량 지표(nDCG/MRR/p@k) 부재" 해소 + reranker 품질 입증 → **항목9 4→5 예상**(×1, +1). 정식 확정은 신규 SHA 재평가 후.

## 7. 관련/artifact
- 도구: `scripts/eval_retrieval_ir.py`
- 결과: `reports/ir-005380-b36248a.json` / 라벨: `reports/ir-golden-005380.json`
- 비용 측면: `memo/results/2026-06-13-eval-track6b-reranker-ab-measurement.md`
- 재평가 보완 #5 / 개선계획 P2-1: [BDAI_Pocat_Team2-a134b5b-rerun-2026-06-13.md](/home/syt07203/TickerTaka-backend/memo/eval/BDAI_Pocat_Team2-a134b5b-rerun-2026-06-13.md:1)
