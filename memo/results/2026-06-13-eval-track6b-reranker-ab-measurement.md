# 2026-06-13 Eval Track6b — Reranker A/B 실측 + 품질 측정 도구

## 1. 목적

Track6(`2026-06-11-eval-track6-rag-hybrid-retrieval.md`)에서 cross-encoder reranker는
**코드 구현 + 실행 성공 + `score_type='reranker'` 출력**까지 확인됐지만, "켜는 게 실제로
더 좋은가"는 미검증으로 남아 있었다. 이번 트랙은 그 공백을 메우기 위해:

- 실데이터 기반 reranker **off↔on A/B 측정 도구**를 만들고
- 실제로 측정해서 **default 값 판단의 근거(순위 변화 / latency / 품질)**를 확보하며
- context_precision이 부적합할 때를 위한 **pairwise LLM judge** 대안을 추가했다.

## 2. 추가/수정 파일

- (신규) [scripts/eval_reranker_ab.py](/home/syt07203/TickerTaka-backend/scripts/eval_reranker_ab.py:1)
  - `RAG_RERANKER_ENABLED` off↔on을 같은 종목·쿼리로 돌려 비교
  - 측정 항목: ① top-k 순위 변화 ② latency(cold/warm 분리) ③ context_precision(RAGAS, `--ragas`) ④ pairwise LLM judge(`--judge`)
  - 후보 0건이면 DB에 데이터 있는 종목을 추천하고 종료
  - latency는 warmup 후 측정하고 ON은 cold(첫 모델 로딩)/warm(steady-state)을 분리
- (수정) [app/config.py](/home/syt07203/TickerTaka-backend/app/config.py:42)
  - `rag_reranker_enabled`에 opt-in 정책 주석 추가(검증 스크립트 포인터 포함)

### 2-1. 기존 validate 스크립트의 한계 인지

[scripts/validate_evidence_retrieval.py](/home/syt07203/TickerTaka-backend/scripts/validate_evidence_retrieval.py:1)는
시드가 news 1 + filing 1 = **2건뿐인 smoke test**다. reranker가 재정렬할 후보가 2개라
"경로 동작"만 증명할 뿐 효과를 측정할 수 없다. 그래서 별도 A/B 스크립트가 필요했다.

## 3. 측정 결과 (`006360`, 후보 138건, top-4, CPU)

```bash
python -m scripts.eval_reranker_ab 006360 --candidates 60 --ragas
```

### 3-1. 순위 변화 — 4/4 전부 재정렬

| 순위 | OFF (rrf) | ON (reranker) |
|---|---|---|
| 1 | 반기보고서 (2025.06) | 기업가치제고계획(자율공시) |
| 2 | BS한양·대보건설·GS건설, 수도권 남부 분양시장 공략 | 반기보고서 (2025.06) |
| 3 | 투자판단관련주요경영사항 | 연결재무제표기준 영업(잠정)실적(공정공시) |
| 4 | 기업가치제고계획(자율공시) | 투자판단관련주요경영사항 |

- reranker가 **타사 분양시장 뉴스(비핵심)를 top-4에서 밀어내고**, 자사 공시(기업가치제고계획·실적공시)를 상위로 올림. 정성적으로는 더 on-topic.

### 3-2. latency — steady-state +7.6 ~ +13.2s (CPU)

3회 측정(warm 기준):

| run | OFF | ON cold | ON warm | steady 오버헤드 |
|---|---|---|---|---|
| 1 | 2381ms | 11026ms | 10003ms | +7622ms |
| 2 | 2333ms | 18438ms | 12987ms | +10654ms |
| 3 | 2308ms | 15640ms | 15460ms | +13152ms |

- CPU에서 cross-encoder(`BAAI/bge-reranker-v2-m3`, ~560M) 추론 비용. **이 정도면 인라인 토론/SSE 경로엔 켤 수 없다.**
- cold start(첫 모델 로딩)는 프로세스당 1회 11~18.6s.

### 3-3. context_precision(RAGAS) — 0.0/0.0, **비결론**

- off/on 모두 `0.0`으로 나왔으나 이는 reranker 무효가 아니라 **지표 사용 한계**다.
- A/B 스크립트가 `agenda=[]`로 호출 → `evaluate_evidence_async` 내부에서 `ground_truth = evidence_query`가 되어 **question == ground_truth == 키워드 쿼리**가 된다. reference 기반 `context_precision`은 골든 정답 문장이 없으면 모든 context를 "유용하지 않음(0)"으로 판정 → off/on 변별 불가.
- 운영 경로(`moderator_summary` → `evaluate_evidence_async`)는 `agenda`에 실제 토론 의제가 들어가므로 이 문제가 없다. 0.0은 오프라인 A/B의 빈 agenda 부작용이다.
- 교훈: **reranker 품질은 context_precision으로 가릴 수 없다.** 쿼리별 골든 relevance + nDCG/precision@k, 또는 pairwise LLM judge가 필요.

## 4. pairwise LLM judge 도입 (`--judge`)

골든셋 없이 품질 **방향**을 보기 위한 대안.

- 같은 질의에 off/on 두 랭킹(제목+발췌)을 LLM(`openai/gpt-oss-120b:free`, OpenRouter)에 보여주고 A/B/TIE로 판정.
- **위치 편향 제거**를 위해 좌우(A=off/B=on, A=on/B=off)를 바꿔 2회 평가 후 집계.
- 여러 종목에서 on 선호가 일관되면 품질 개선 신호로 본다. 단일 종목·2회로는 결론 불가.

```bash
python -m scripts.eval_reranker_ab 006360 --candidates 60 --judge
```

> judge는 골든셋의 완전 대체가 아니라 **저비용 방향 신호**다. 최종 판단은 항목8 golden-set 회귀와 함께 봐야 한다.

## 5. 판정

- **reranker는 실제로 순위를 바꾸며(4/4), 정성적으로 더 on-topic.** no-op가 아니다.
- 그러나 **CPU steady-state +7.6~13.2s/retrieval**는 인라인 토론/SSE에 부적합.
- **품질 정량 입증은 아직 없음**(context_precision 부적합, judge는 방향 신호용·미실측).

→ **결론: `RAG_RERANKER_ENABLED` 코드 기본값 `false`(opt-in) 유지가 실측으로 정당화됨.**
켤 경우 **GPU** 또는 **오프라인 배치 재랭킹** 경로로 한정한다. 본 토론 inline default-on은 latency만으로도 탈락.

## 6. 남은 후속 (항목 8과 동일 뿌리)

1. **골든 relevance 셋 구축** — 쿼리별 정답 근거 라벨 → nDCG/precision@k로 reranker off/on 정량 비교. 이게 생기면 항목8(RAGAS 회귀)과 항목9(reranker 품질 입증=5점 상향 근거)가 동시에 풀린다.
2. **judge 다종목 반복** — 골든셋 전, 여러 종목에 `--judge`를 돌려 on 선호 일관성 확인.
3. **GPU/배치 경로 분리** — reranker를 켤 경우의 서빙 위치 결정(인라인 금지).

## 7. 관련 메모

- [[2026-06-11-eval-track6-rag-hybrid-retrieval]] — hybrid(BM25+vector+RRF) 1차 구현 및 reranker 준비
- 평가 리포트 항목 9 / 보완 #4: [BDAI_Pocat_Team2-3ad1682.md](/home/syt07203/TickerTaka-backend/memo/eval/BDAI_Pocat_Team2-3ad1682.md:1)
