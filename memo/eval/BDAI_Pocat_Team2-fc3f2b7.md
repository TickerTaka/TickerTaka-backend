# 검토 리포트 — BDAI_Pocat_Team2 (TickerTaka) @ `fc3f2b7`

- 모드: **Full** (정적 5종 병렬 + 동적 Docker 프로빙) / 검토 일시: 2026-06-01 / backend `fc3f2b7`, frontend `5001217`
- 대상: `BDAI_Pocat_Team2/TickerTaka-backend` (메인) + `TickerTaka-Frontend`
- 종합: **25 / 70 (35.7%) → 등급 F**
- ⚠ GATE: 항목8 RAGAS = 0 → 등급 상한 **B** (단 percent 자체가 F 구간)
- 오케스트레이션: 메인 세션이 5개 auditor를 병렬 디스패치 → 교차검증 → Supervisor 단독 합성

---

## 항목별 스코어카드

| # | 항목 | 점수 | 가중 | 가중점 | 신뢰 | 증거 (file:line / cmd) | 코멘트 |
|---|------|:---:|:---:|:---:|:---:|------|------|
| 1 | Multi-Agent 구조 | 4 | ×2 | 8 | [S] | `debate_graph.py:50–68` StateGraph 6노드, `:65` `add_conditional_edges("moderator_check", _router)`, `:66` `moderator_summary→END` | moderator(pre/check/summary)=supervisor, bull/bear=ReAct sub, 최종수렴 1곳. 단 bull/bear 동일모델·data_agent는 비-LLM → 에이전트 이질성 부족 |
| 2 | 에러핸들링·폴백 | 3 | ×2 | 6 | [S] | `bull_node.py:48–61`/`bear_node.py:47–60` try/except 대체값, `data_node.py:44–46` yfinance 폴백, `debate_runtime_guard.py:79–81` Redis fail-open | 다층 보호 양호하나 **moderator(supervisor) LLM 호출에 try/except·retry 부재 = SPOF**, 에이전트 노드에 tenacity 미적용 |
| 3 | sLLM+검증+langfuse | 2 | ×2 | 4 | [S] | `config.py:37–40,53` 전 노드 `gpt-4o-mini`, `llm_factory.py:38–44` OpenAI 직접호출, langfuse grep 0건 | 검증에이전트(`moderator_check`)는 실동작[출력 게이팅+재발언 루프], 그러나 sLLM 미사용(OpenAI 독점)·langfuse 전무. dead config(`JUDGE_LLM_MODEL`) |
| 4 | 5대 설계문서 | 1 | ×1 | 1 | [S] | `sequenceDiagram`/`erDiagram` grep 0건, docs/memo는 전부 작업일지·계획서·구현보고서 | **5대 문서 전부 부재 → 누락 상한 캡 1**. 코드 내부 정합성(ORM↔alembic, API보고서↔라우터)은 양호하나 설계문서 형식 0 |
| 5 | Dockerise | 2 | ×1 | 2 | [D] | `Dockerfile` 부재(Glob 0건), `docker-compose.yml` 인프라 전용("Local development only"), `docker compose up`→redis healthy[D]·chroma up | **앱 Dockerfile 없음** → 앱 컨테이너 빌드 불가. compose는 postgres/redis/chroma 인프라만. depends_on 없음 |
| 6 | MCP / A2A | 0 | ×1 | 0 | [S] | `mcp`/`modelcontextprotocol`/`a2a`/`AgentCard` grep 전부 0건 (FE 포함) | 선언조차 없음 |
| 7 | vLLM | 0 | ×1 | 0 | [S] | `vllm`/`ollama`/`mlx`/`llama.cpp` 실코드 0건, compose에 서빙 서비스 없음 | 계획문서에만 언급, 미구현. macOS 대안(Ollama/MLX)도 미적용 |
| 8 | RAGAS | 0 | ×2 | 0 | [S] | `ragas` grep 0건, `faithfulness/answer_relevancy/...` 0건, `tests/` 하위 빈 폴더 | 정량평가 파이프라인 전무. scripts/는 통합 정합성 검증(건수 assert)이지 품질 지표 아님 |
| 9 | RAG 고도화 | 2 | ×1 | 2 | [S] | `rank-bm25` 설치됐으나 코드 import 0건, `evidence_retrieval.py:77–92` chroma vector만, reranker 0건, `dart/client.py:435–485` 청킹 실동작 | "깔기만 한 BM25"(문서가 의도적 제외 명시), reranker 미선언. 공시 청킹만 실동작(overlap 0 고정크기) |
| 10 | 스트리밍·비동기 | 2 | ×1 | 2 | [S] | `sse-starlette` 설치됐으나 `EventSourceResponse`/`text/event-stream` 0건, `debate.py:37,61` 완료 후 일괄 return, `data_node.py:25–29` 5개 fetch 순차 await, `asyncio.gather` 0건 | **가짜/미구현 스트리밍**(LangGraph astream을 HTTP로 안 흘림), 비동기 병렬화 없음. watchlist BackgroundTasks만 부분 존재 |

**합계: (4+3+2)×2 + 0×2(항목8) + (1+2+0+0+2+2)×1 = 18 + 0 + 7 = 25 / 70 = 35.7%**

---

## 강점 (취업 관점)

1. **실질적 토론형 Multi-Agent 구조(항목1, 최고점)** — `moderator_check`가 bull/bear 발언을 검증해 `verdict=hallucination` 시 재발언을 강제하고(`_router`), 환각 2회 누적 시 강제 요약 종료하는 **출력 게이팅 + 검증 루프**가 코드로 실존(`debate_graph.py:22–47`). 이름뿐인 검증이 아니라 동작하는 critic이다.
2. **다층 폴백(항목2)** — bull/bear LLM 실패 시 그래프 중단 없이 진행, data_agent의 DB→yfinance 폴백, Redis fail-open, 체크포인트 전구간 예외 처리.
3. **코드 내부 정합성** — 15개 테이블 ORM↔Alembic 마이그레이션 1:1 일치, API 구현보고서와 실제 라우터 대부분 일치. (문서 "형식"은 없지만 구현 일관성은 상위)

---

## 보완 필요 (우선순위 순)

1. **[항목8] RAGAS 정량평가 신설 (가중 ×2, 현재 0)** — golden Q&A 10–20쌍 + `scripts/run_ragas_eval.py`로 faithfulness/answer_relevancy/context_precision 산출, `reports/ragas-<sha>.json` 저장. 게이트(상한 B) 해제의 핵심.
2. **[항목3] sLLM 실연결 + langfuse (가중 ×2)** — `llm_factory.py`에 OpenRouter base_url 활성화 후 bull/bear 중 하나 이상을 `llama-3.3-70b`/`deepseek`(≤300B)로 교체(계획문서에 이미 슬러그 존재). langfuse 패키지 추가 + `LangfuseCallbackHandler`를 호출 경로에 연결.
3. **[항목10] 진짜 스트리밍 + 병렬화** — `debate.py`에 `EventSourceResponse` + async generator로 LangGraph `.astream()` 청크 점진 전송. `data_node`의 5개 fetch를 `asyncio.gather`로 병렬화, bull/bear를 `ainvoke()` 전환.
4. **[항목5] 앱 Dockerise** — 멀티스테이지 Dockerfile 작성 + compose에 FastAPI 앱 서비스 추가 + `depends_on(condition: service_healthy)` + chroma healthcheck.
5. **[항목4] 5대 설계문서 작성** — 코드가 이미 정합적이므로 도식화만 하면 됨: ERD(`erDiagram`, alembic init이 단일 소스), 시퀀스(debate_graph 노드체인), 컴포넌트(app/ 8패키지 책임표), 인터페이스 정의서(라우터 3파일 전 엔드포인트), 유스케이스 명세.
6. **[항목2] moderator SPOF 해소** — `moderator_node._call()`에 `@retry`/try-except 추가, 에이전트 노드 LLM 호출에 tenacity 적용.
7. **[항목9] RAG 고도화 실연결** — 깔려있는 `rank-bm25`를 검색 경로에 RRF로 융합, cross-encoder reranker 말단 연결, 청킹 overlap 추가.
8. **[항목6/7]** MCP 서버로 tools 노출 / Ollama·vLLM 서빙 추가 (선택).

---

## 교차검증 노트 (문서 ↔ 코드)

- **문서-코드 불일치**: `docs/frontend-required-api-implementation-report.md`가 주장하는 API 목록에서 `GET /api/watchlists/{user_id}/feed`, `DELETE /api/debates/{session_id}`, `GET /api/debates/{session_id}` 3개 엔드포인트 누락(코드엔 존재). 단 5대 설계문서 자체가 부재라 항목4는 이미 캡 적용.
- **계획 vs 실동작 괴리**: 계획 문서들엔 OpenRouter Llama/DeepSeek(항목3), 하이브리드+reranker(항목9), Ollama/Qwen 로컬서빙(항목7)이 풍부히 설계됐으나 **실행 경로엔 미반영**. "선언≠실동작"의 전형 — 기획 역량은 높으나 구현 마감이 미달.

---

## 재현 정보

- 커밋: backend `fc3f2b7` / frontend `5001217`
- 동적: `docker compose config` 통과, `docker compose up -d` → redis healthy(`redis-cli ping` PONG ×2), chroma up(healthcheck 미정의), postgres는 호스트 5432 충돌로 미기동(환경 문제, 무감점). 앱 컨테이너는 Dockerfile 부재로 미빌드.
- 5 auditor 도메인: architecture(1·2·6), llm-stack(3·7), rag-eval(8·9), runtime-infra(5·10), docs(4) — 전부 [S]/[D] 증거 동봉.
