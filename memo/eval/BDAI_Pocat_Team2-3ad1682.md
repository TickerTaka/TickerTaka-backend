# 검토 리포트 — BDAI_Pocat_Team2 (TickerTaka) @ `3ad1682`

- 모드: **Full (정적 코드/문서 재평가 + 기존 런타임 검증 결과 반영)** / 검토 일시: 2026-06-12
- 대상: `TickerTaka-backend`
- 기준: [evaluation_criteria.md](/home/syt07203/TickerTaka-backend/memo/eval/evaluation_criteria.md:1)
- 비교 기준 리포트: [BDAI_Pocat_Team2-fc3f2b7.md](/home/syt07203/TickerTaka-backend/memo/eval/BDAI_Pocat_Team2-fc3f2b7.md:1)
- 종합: **58 / 70 (82.9%) → 등급 B**
- 게이트: **해제** (`항목8 RAGAS > 0`)
- 변화: **25 / 70 → 58 / 70 (+33점)**
- 재검증: 2026-06-12, 전 항목 증거를 `3ad1682` 실제 코드와 1:1 대조해 확인. 항목 9의 reranker 구현 여부를 정정(아래 참조), 나머지 점수는 코드 근거로 유지.

---

## 항목별 스코어카드

| # | 항목 | 점수 | 가중 | 가중점 | 신뢰 | 증거 (file:line / report) | 코멘트 |
|---|------|:---:|:---:|:---:|:---:|------|------|
| 1 | Multi-Agent 구조 | 5 | ×2 | 10 | [S] | `app/agents/debate_graph.py:22-68`, `app/agents/nodes/moderator_node.py:83-292` | data/bull/bear/moderator(pre/check/summary) 구조와 conditional routing, 환각 개입/강제 요약까지 동작. supervisor 역할이 실질적으로 존재 |
| 2 | 에러 핸들링 & 폴백 | 5 | ×2 | 10 | [S] | `app/agents/nodes/moderator_node.py:83-107,175-189,236-281`, [2026-06-07-eval-track2-error-handling.md](/home/syt07203/TickerTaka-backend/memo/results/2026-06-07-eval-track2-error-handling.md:1) | moderator_pre 기본 의제, moderator_summary fallback summary, evidence 저장 실패 비전파, RAGAS 백그라운드 실패 분리까지 반영 |
| 3 | sLLM 모델 + 검증 Agent + langfuse | 3 | ×2 | 6 | [S] | `app/agents/nodes/moderator_node.py:111-162`(moderator_check), `app/domain/debate_evaluation.py:21,45-63`(sLLM 팩토리), `app/config.py:57-60`, `rg langfuse` → app/ 코드 0건 | 검증 Agent(moderator_check)는 실동작하나 모델은 `gpt-4o-mini`(OpenAI). sLLM(`openai/gpt-oss-120b:free`, ≤300B)은 **RAGAS 평가 경로에서만** 사용되어 "검증 Agent에 sLLM" 요건은 부분 충족. bull/bear/moderator 본 토론 경로는 전부 OpenAI이고 Langfuse는 코드 미구현 |
| 4 | 5대 설계문서 | 5 | ×1 | 5 | [S] | `docs/design/use-case-specification.md:1`, `component-design.md:1`, `interface-definition.md:122-205`, `sequence-diagram.md:1`, `erd.md:1-220` | 5종 존재, API/ERD/MCP/평가 경로까지 현재 구현 기준으로 정리됨 |
| 5 | Dockerise | 5 | ×1 | 5 | [S/D] | `Dockerfile:1-23`, `docker-compose.yml:63-91`, [2026-06-09-eval-track4-dockerise.md](/home/syt07203/TickerTaka-backend/memo/results/2026-06-09-eval-track4-dockerise.md:205) | 앱 Dockerfile, compose app 서비스, healthcheck, HF cache volume, local-db profile 분리까지 완료. `/health` 실기동 성공 보고 존재 |
| 6 | MCP or A2A | 5 | ×1 | 5 | [S/D] | `app/api/debate.py:265-320`, `app/integrations/notion_mcp.py:166-312`, [2026-06-09-eval-track3-mcp-notion-publish.md](/home/syt07203/TickerTaka-backend/memo/results/2026-06-09-eval-track3-mcp-notion-publish.md:1) | Notion MCP 경유 발행 API와 멱등 컬럼 저장 구현. 실제 Notion page URL 반환까지 E2E 성공 |
| 7 | vLLM 사용 | 0 | ×1 | 0 | [S] | `rg -n "vllm|ollama|mlx|llama.cpp"` → 0건 | 여전히 로컬 서빙(vLLM/Ollama/MLX) 부재 |
| 8 | 정량 평가 파이프라인 (RAGAS) | 4 | ×2 | 8 | [S] | `requirements.txt:54`, `app/domain/debate_evaluation.py:1-260`, `app/models/debate.py:167-175`, `app/agents/nodes/moderator_node.py:253-279` | `faithfulness`, `answer_relevancy`, `context_precision` 평가와 DB 저장까지 구현. 다만 배치 스크립트/리포트 artifact/golden-set 회귀는 미완 |
| 9 | RAG 고도화 | 4 | ×1 | 4 | [S/D] | `app/domain/evidence_retrieval.py:105-133`(hybrid 파이프라인), `:285-335`(RRF 융합 + reranker), `:547-551`(CrossEncoder), `app/agents/nodes/data_node.py:26-43`, [2026-06-11-eval-track6-rag-hybrid-retrieval.md](/home/syt07203/TickerTaka-backend/memo/results/2026-06-11-eval-track6-rag-hybrid-retrieval.md:1) | BM25 + vector + RRF hybrid 실제 연결, `rank/score_type` 메타, `to_thread`/`gather` 보강. cross-encoder reranker(`BAAI/bge-reranker-v2-m3`)는 `_maybe_rerank` + 예외 폴백 + `lru_cache` 싱글톤까지 구현되었고, **실제 실행 검증에서 `score_type='reranker'` 출력까지 확인**됐다. 다만 정량 개선(before/after)과 운영 기본값 판단(latency / memory / cold start)은 아직 후속 튜닝 영역이다 |
| 10 | 스트리밍 & 비동기 처리 | 5 | ×1 | 5 | [S/D] | `app/api/debate.py:62-262`, [2026-06-10-eval-track5-streaming-and-sse.md](/home/syt07203/TickerTaka-backend/memo/results/2026-06-10-eval-track5-streaming-and-sse.md:1) | `POST /sessions` + `GET /stream` SSE 경로, replay/failed/running 분기, disconnect 정리까지 런타임 검증 완료 |

**합계: (5+5+3+4)×2 + (5+5+0+4+5+5)×1 = 34 + 24 = 58 / 70 = 82.9%**

---

## 이전 평가 대비 변화

기존 평가(`fc3f2b7`)는 아래가 주된 감점 원인이었다.

- 5대 설계문서 부재
- 앱 Dockerfile 부재
- MCP/A2A 전무
- RAGAS 0점
- BM25 설치만 되고 미연결
- SSE/스트리밍 부재

현재는 이 중 대부분이 해소됐다.

1. **설계문서 5종 완성**
2. **moderator fail-soft / 평가 비전파 / fallback 요약**까지 에러핸들링 보강
3. **Notion MCP E2E 성공**
4. **앱 컨테이너 + compose healthcheck + `/health` 런타임 성공**
5. **SSE 스트리밍 + disconnect cleanup** 완료
6. **RAGAS 평가 + DB 영속화**
7. **BM25 + vector + RRF hybrid retrieval**

즉, 초기 리포트에서 0~2점대였던 항목 4/5/6/8/9/10이 대거 상향됐다.

---

## 강점

1. **실동작하는 토론형 Multi-Agent**
- 단순 노드 연결이 아니라 moderator가 환각을 검증하고 재발언/강제요약까지 제어한다.

2. **런타임 복원력**
- LLM 실패가 즉시 전체 실패로 번지지 않도록 fallback summary, background eval 분리, stream disconnect cleanup까지 반영돼 있다.

3. **MCP·Docker·SSE까지 닫힌 수직 슬라이스**
- 설계 → 구현 → 런타임 검증 → 결과 문서가 연결되어 있다.

4. **RAG 품질 향상 방향이 코드로 반영됨**
- BM25/vector/RRF 융합 + score 해석 메타에 더해, cross-encoder reranker까지 (비활성 상태로) 코드에 들어가 있어 “설치만 한 RAG” 단계는 확실히 지났다.

---

## 남은 보완

1. **항목 3: Langfuse 미구현**
- sLLM은 RAGAS 평가 경로에서 쓰이지만, 본 토론 경로 전체와 tracing 관점에서는 아직 부분 점수다.

2. **항목 7: vLLM/Ollama/MLX 부재**
- 현재 총점의 가장 큰 구조적 미달 요인이다.

3. **항목 8: 정량평가 증적 체인 미완**
- `scripts/run_ragas_eval.py`, golden-set, 리포트 산출물(`reports/ragas-<sha>.json`)이 추가되면 더 안정적으로 최고점에 가까워질 수 있다.

4. **항목 9: reranker는 실행 검증까지 끝났지만 효과 비교와 운영 판단은 남음**
- cross-encoder reranker 코드(`_maybe_rerank` + `BAAI/bge-reranker-v2-m3` + 예외 폴백)는 이미 들어가 있었고, `RAG_RERANKER_ENABLED=true` 경로와 `score_type='reranker'` 출력까지 확인됐다. 다만 이때 사용한 `scripts/validate_evidence_retrieval.py`는 시드가 news 1 + filing 1 = **2건뿐인 smoke test**라 "경로 동작"만 증명할 뿐 reranker 효과는 보여줄 수 없다(후보 2개는 재정렬이 무의미).
- 효과 검증용으로 `scripts/eval_reranker_ab.py`(off↔on A/B)를 추가하고 실데이터로 측정했다(`006360`, 후보 138건, top-4). **측정 결과:**
  - **순위 변화: 4/4 전부 재정렬** — off(rrf)에서 상위였던 비핵심 뉴스(타사 분양시장 기사)가 밀려나고, on(reranker)에서 자사 공시(기업가치제고계획·실적공시)가 상위로 올라옴. 정성적으로는 더 on-topic.
  - **latency: steady-state +7.6 ~ +13.2s (3회, CPU 기준)**, cold start 11~18.6s. 이 비용으론 인라인 토론/SSE 경로에 켤 수 없다.
  - **품질(context_precision) A/B는 비결론**: off/on 모두 0.0으로 degenerate. 원인은 reference 기반 지표에 골든 정답이 없으면(키워드 쿼리를 ground_truth로 쓰면) 변별이 안 되기 때문 — reranker 무효가 아니라 **지표 선택의 한계**다. 제대로 가리려면 쿼리별 골든 relevance + nDCG/precision@k 또는 pairwise LLM judge가 필요하며, 이는 항목 8 golden-set과 같은 작업이다.
- 골든셋 없이 방향만 보는 **pairwise LLM judge**(`--judge`)도 스크립트에 추가했다(off/on 두 랭킹을 LLM에 좌우 swap 2회로 비교). 단일 종목으론 결론 불가이며, 최종 입증은 항목 8 골든셋과 함께 본다.
- **결론: 코드 기본값 `false`(opt-in) 유지가 실측으로 정당화됨.** 켤 경우 GPU 또는 오프라인 배치 재랭킹 경로로 한정하고, 본 토론 inline default-on은 latency만으로도 부적합. 품질 입증(5점 상향 근거)은 골든셋 확보 이후로 미룬다. 상세 실측: [2026-06-13-eval-track6b-reranker-ab-measurement.md](/home/syt07203/TickerTaka-backend/memo/results/2026-06-13-eval-track6b-reranker-ab-measurement.md:1)

---

## 판정

현재 상태는 더 이상 “기획 대비 미구현이 많은 상태”가 아니다.  
특히 항목 4/5/6/8/9/10이 실구현과 검증까지 따라오면서, 기존 F 수준에서 **B 수준**까지 올라왔다.

다만 아래 두 항목이 아직 명확한 상한으로 남아 있다.

- **항목 3**: Langfuse 부재, sLLM 적용 범위 제한
- **항목 7**: vLLM/Ollama/MLX 부재

따라서 현재 평가는 **58/70, B**가 가장 보수적이고 설득력 있는 판정이다.
