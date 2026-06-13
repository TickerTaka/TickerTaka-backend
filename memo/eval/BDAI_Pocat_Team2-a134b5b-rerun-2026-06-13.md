# 검토 리포트 (재검토) — BDAI_Pocat_Team2 (TickerTaka) @ `a134b5b`

- 모드: **Full** (정적 5종 병렬 + 동적 Docker 빌드·기동·healthcheck 프로빙 성공) / 검토 일시: 2026-06-13
- 대상: `BDAI_Pocat_Team2/TickerTaka-backend` (메인, `a134b5b`) + `TickerTaka-Frontend` (`2e4dc03`)
- 직전 리포트: `reports/BDAI_Pocat_Team2-fc3f2b7.md` (backend `fc3f2b7`, 25/70 F) — **보존, 미변경**
- 종합: **47 / 70 (67.1%) → 등급 C**
- 소프트 게이트: **미발동** — 직전 B-cap 사유였던 항목8 RAGAS=0이 이번에 4로 해소(`ragas.evaluate()` 실호출 확인). 항목1·2 모두 ≥3.
- 오케스트레이션: 메인 세션(Supervisor)이 5개 auditor를 1턴 병렬 디스패치 → 증거 검사 → 문서-코드 교차검증 → Supervisor 단독 합성. RAGAS 비위조·RRF 공식·stream 엔드포인트 존재는 Supervisor가 직접 재확인.

---

## 항목별 스코어카드

| # | 항목 | 점수 | 가중 | 가중점 | 신뢰 | 증거 (file:line / cmd) | 코멘트 |
|---|------|:---:|:---:|:---:|:---:|------|------|
| 1 | Multi-Agent 구조 | 4 | ×2 | 8 | [S] | `debate_graph.py:51-66` StateGraph 6노드, `:22-47` `_router` 조건부 라우팅, `moderator_check`→`moderator_summary` 단일 수렴, `bull/bear_node` create_react_agent | moderator 3노드=Supervisor, bull/bear=Worker, 모든 sub 출력이 moderator_check 경유 후 summary 단독 생성. 동적 trace([D]) 부재로 상향 보류 |
| 2 | 에러핸들링·폴백 | 4 | ×2 | 8 | [S] | 5노드 각 try/except+폴백(`bull_node:59-61`,`bear_node:58-60`,`data_node:48-50/165-167` 이중폴백,`moderator_node:94-96/186-189`), `embedding.py:78`·`dart/client.py:657` tenacity, `llm_factory.py:43` max_retries=3+timeout, `debate_runtime_guard.py:77-81` Redis fail-open, `except: pass` 0건 | 직전 SPOF(moderator)·tenacity 미적용 지적 대부분 해소. 잔여: 그래프 전체 타임아웃(`asyncio.wait_for`) 부재 |
| 3 | sLLM+검증+langfuse | 2 | ×2 | 4 | [S] | `debate_evaluation.py:21` `_EVAL_MODEL="openai/gpt-oss-120b:free"` + `:52-63` OpenRouter base_url, `llm_factory.py:38-44` 본토론은 ChatOpenAI(gpt-4o-mini), `config.py:34` `analysis_generation_model=None`(Qwen 비활성), langfuse grep 0건(deps·코드 전무) | 검증에이전트(moderator_check) 실동작 충족. sLLM은 RAGAS 사후평가 1경로(gpt-oss-120b≤300B)만 — 본 토론은 독점 프런티어. **langfuse 완전 미구현이 ×2 항목의 상향 차단** |
| 4 | 5대 설계문서 | 4 | ×1 | 4 | [S] | `memo/design/{use-case-specification,component-design,interface-definition,sequence-diagram,erd}.md` 5종 실존, `erd.md:58-266` erDiagram 18엔티티↔`app/models/` 일치, `sequence-diagram.md`↔`debate_graph.py:60-66` 노드체인 일치 | 직전 전부 부재(캡 1)→이번 5종 완비·코드 일치 우수. 불일치(경미): interface-definition.md에 구현완료 `POST /api/debates/sessions`·`GET /.../stream` 2개 미기재(코드우선, 문서가 코드보다 뒤처짐) |
| 5 | Dockerise | 4 | ×1 | 4 | [D] | `Dockerfile`(python:3.12-slim, CMD uvicorn), `docker build`→exit 0, `docker compose up -d redis chroma app`→app `healthy`, `docker exec ... curl /health`→`{"status":"ok"}`, compose `:81-91` depends_on(service_healthy)+healthcheck | 직전 Dockerfile 부재(미빌드)→이번 빌드·기동·헬스체크 동적 성공. 5점 미달: 단일스테이지(이미지 10GB), postgres가 profile 뒤 |
| 6 | MCP / A2A | 3 | ×1 | 3 | [S] | `notion_mcp.py:15-16` MCP 프로토콜 상수, `:45-164` `_StdioJsonRpcClient` initialize→tools/call 핸드셰이크 실구현, `debate.py:265-331` publish API, memo E2E 성공기록. A2A 0건 | 직전 0(선언조차 없음)→이번 실제 MCP stdio 클라이언트. 3점 한계: 단방향 클라이언트만(서버측 tool 노출·tools/list·Python mcp SDK 미사용) |
| 7 | vLLM | 0 | ×1 | 0 | [S] | `vllm/ollama/mlx/llama.cpp` 실코드 0건, compose 서빙서비스 없음, `docs:553` Ollama 의도적 배제, Qwen은 transformers 직접로드+기본 비활성 | 변동 없음. 서빙 인프라 부재. macOS 대안(Ollama/MLX)도 미적용 |
| 8 | RAGAS | 4 | ×2 | 8 | [S] | `requirements.txt:54` ragas==0.2.15, `debate_evaluation.py:130-169` `evaluate(metrics=[faithfulness,answer_relevancy])` 실호출+`df[...].tolist()[0]` 추출(상수 0건), `:227-251` context_precision, `tests/.../test_ragas_regression.py:39-102` 회귀 게이트, `run_ragas_eval.py:36-65` golden set | 직전 0(전무)→이번 비위조 정량 파이프라인 + 회귀 게이트. **Supervisor 직접 재확인: 하드코딩 없음**. 5점 미달: golden 1건뿐·실행 artifact(json) 미커밋 |
| 9 | RAG 고도화 | 4 | ×1 | 4 | [S] | `evidence_retrieval.py:9` `from rank_bm25 import BM25Okapi`(직전 import 0→실연결), `:258-283` BM25 실동작, `:285-303` RRF `1.0/(rrf_k+index)`(Supervisor 재확인), `:306-335` CrossEncoder reranker 연결, `dart/client.py:594-644` 섹션경계 청킹, `eval_reranker_ab.py` 260줄 A/B 실측 | 직전 "깔기만 한 BM25"→이번 실검색 경로 융합+reranker 코드연결+latency 실측 기반 default off 정당화. 5점 미달: 검색 자체 지표(nDCG/MRR) 부재, reranker 운영 default off |
| 10 | 스트리밍·비동기 | 4 | ×1 | 4 | [S]+[D부분] | `debate.py:8` `from sse_starlette import EventSourceResponse`, `:128-262` `GET /{id}/stream`, `debate_service.py:71-143` async gen이 `.astream()` 노드별 즉시 yield(일괄반환 아님), `data_node.py:26-32` `asyncio.gather` 5fetch 병렬 | 직전 가짜/미구현→이번 진짜 노드단위 점진 스트리밍+fetch 병렬화. 5점 미달: 토큰단위 스트리밍 미구현, bull/bear 노드 자체는 직렬, SSE 청크타이밍 동적프로빙은 DB 미기동으로 미수행 |

**합계: (4+4+2+4)×2 + (4+4+3+0+4+4)×1 = 28 + 19 = 47 / 70 = 67.1% → C**

---

## 강점 (취업 관점)

1. **재제출에서 루브릭 전 영역을 실제 코드로 메움** — 직전 F(25)에서 거의 모든 약점 항목(4·5·8·9·10)을 선언이 아닌 실동작으로 끌어올림. 특히 항목8 RAGAS는 `ragas.evaluate()`를 실제 호출하고 회귀 테스트 게이트(`test_ragas_regression.py`)까지 갖춰 "하드코딩 위조"가 아님이 Supervisor 직접 확인으로 입증됨.
2. **측정 기반 의사결정** — reranker를 무작정 켜지 않고 off/on A/B를 실측(`eval_reranker_ab.py`, latency +7.6~13.2s)해 "inline SSE 경로엔 부적합 → default off(opt-in)"를 데이터로 정당화. 엔지니어링 판단의 흔적이 문서·코드에 남아 있음.
3. **검증 루프형 Multi-Agent** — `moderator_check`가 환각 판정 시 재발언 강제, 2회 누적 시 강제 요약 종료. 이름뿐 critic이 아니라 그래프 라우팅을 실제 제어.
4. **동적 검증 통과** — Dockerfile 신규 + compose가 빌드·기동·healthcheck를 실제로 통과(`app` 컨테이너 `healthy`, `/health` 200). 직전 미빌드 대비 운영 가능성 입증.
5. **문서-코드 정합** — 5대 설계문서가 ERD↔models, 시퀀스↔debate_graph로 실제 일치. 껍데기 문서 아님.

---

## 보완 필요 (우선순위 순)

1. **[항목3] langfuse 실연결 (가중 ×2, 현재 2 → 4 잠재)** — 가장 큰 미수확 가중점. `langfuse`를 requirements에 추가하고 `LangfuseCallbackHandler`를 `llm_factory.py`의 ChatOpenAI 생성부 `callbacks=`에 주입해 bull/bear/moderator 전 호출을 trace 적재. 동시에 `openrouter_api_key`(이미 `config.py:56` 존재, 미사용)를 본 토론 1개 노드 이상에 연결해 sLLM을 사후평가 밖으로 끌어내면 항목3이 2→4로 상승(가중점 +4).
2. **[항목7] 로컬 서빙 신설 (현재 0 → 3+ 잠재)** — macOS이므로 Ollama(`qwen2.5:7b`) 서비스를 compose에 추가하고 `OLLAMA_BASE_URL`로 OpenAI 호환 분기, 또는 `ANALYSIS_GENERATION_MODEL` 활성화 + Qwen 워커 서비스화. 항목3 sLLM 본경로 연결과 동시 해결 가능.
3. **[항목1·10] 동적 trace/스트리밍 프로빙으로 [S]→[D] 격상** — postgres를 기본 기동(또는 SQLite fallback) 후 `curl -N`로 SSE `text/event-stream` 청크 타이밍 확인 + langfuse trace 첨부 시 항목1·10 각각 5점 도달 가능.
4. **[항목8] golden set 확장 + artifact 커밋 (4 → 5 잠재)** — golden Q&A를 1→10~20쌍으로 늘리고 `reports/ragas-<sha>.json` 실행 산출물을 커밋해 "한 번 이상 실제 실행" 증적 확보.
5. **[항목9] 검색 자체 정량 지표** — nDCG/MRR/precision@k를 golden relevance 기반으로 추가(memo에 본인이 필요성 진단함). reranker 품질을 context_precision 외 표준 IR 지표로 입증.
6. **[항목5] 멀티스테이지 빌드** — builder/runtime 분리로 10GB → 2~3GB 축소.
7. **[항목6] MCP 서버측 + Python `mcp` SDK** — `mcp.ClientSession`+`stdio_client`로 교체(tools/list 자동), FastAPI에 MCP 서버 엔드포인트 추가로 양단 완성 시 3→4+.
8. **[항목2] 그래프 전체 타임아웃** — `asyncio.wait_for`로 노드 hang 방어 시 4→5.
9. **[항목4] 인터페이스 정의서 동기화** — `POST /api/debates/sessions`, `GET /.../stream`(구현완료인데 "추후예정"으로 격하 표기됨)를 정식 기재.

---

## 교차검증 노트 (문서 ↔ 코드)

- **코드우선 적용**: interface-definition.md가 `GET /api/debates/{session_id}/stream`을 "추후 확장 예정"으로 격하 표기했으나, runtime-infra·docs 두 auditor가 독립적으로 `debate.py:128`에 완전 구현됨을 확인. 문서가 코드보다 **뒤처진** 방향(문서가 없는 기능을 허위 주장한 것이 아님)이라 항목4는 경미 감점에 그쳐 4점 유지.
- **비위조 재확인(Supervisor 직접)**: 항목8 RAGAS와 항목9 RRF는 위조 가능성이 높은 영역이라 Supervisor가 `debate_evaluation.py:157-166`(evaluate 실호출, df 추출)와 `evidence_retrieval.py:297`(RRF 공식) 원문을 직접 읽어 하드코딩 상수 부재를 확인. auditor 4점을 신뢰 가능.
- **신뢰도 규칙 적용**: 항목1·3·8·9는 동적 검증이 외부 API 키 미주입으로 불가해 [S] 단독. 4점 이상 항목(1·8·9)은 rubric-scoring 신뢰도 규칙대로 [S]에서 5점 상향을 보류하고 4에 고정.

---

## 직전 리포트(fc3f2b7) 대비 진전 — SHA 비교

- **저장소 진전 확인**: `git merge-base --is-ancestor fc3f2b7 a134b5b` → YES. backend `fc3f2b7`(직전 검토) → `a134b5b`(이번), `git diff --stat`: **95 files, +12,631 / -246**. 실제 후속 커밋(Dockerise·5대문서·SSE·BM25+RRF hybrid·reranker A/B·RAGAS·MCP/Notion)이 다수 추가됨. frontend `5001217` → `2e4dc03`(SSE 실시간 토론).
- **항목별 변화**: 1 (4→4), 2 (3→4), 3 (2→2, langfuse 여전히 0), 4 (1→**4**, 5대문서 신설), 5 (2→**4**, Dockerfile 신설·동적기동), 6 (0→**3**, MCP 실구현), 7 (0→0), 8 (0→**4**, RAGAS 신설), 9 (2→**4**, BM25/RRF/reranker 실연결), 10 (2→**4**, 진짜 SSE+병렬).
- **종합**: **25 → 47 / 70 (35.7% → 67.1%), 등급 F → C.** 직전 B-cap(항목8=0) 해소. 진전 명백 — "기획만 풍부하고 구현 미달"이던 직전 진단에서, 이번엔 계획 문서의 항목들(하이브리드·RAGAS·Dockerise·MCP)을 대부분 실코드로 마감. 다만 langfuse(항목3)·vLLM(항목7)은 여전히 미구현으로 ×2 항목3과 항목7이 잔존 미수확.

---

## 재현 정보

- 커밋: backend `a134b5b` / frontend `2e4dc03` (둘 다 git 저장소, `git -C <repo> rev-parse --short HEAD`로 확인)
- 직전 대비: `git -C TickerTaka-backend diff --stat fc3f2b7 a134b5b` → 95 files, +12631/-246
- 동적(항목5·일부10): `docker build -t tickertaka-app-audit:a134b5b .` exit 0 → `docker compose up -d redis chroma app` → app 컨테이너 `healthy`, `docker exec tickertaka-app curl -s localhost:8000/health` → `{"status":"ok"}`. postgres는 `profiles:["local-db"]`로 미기동 → SSE 청크타이밍 [D] 프로빙은 미수행(코드상 진짜 스트리밍 [S] 확인).
- 미검증(동적): 항목1 멀티에이전트 호출 trace, 항목3 sLLM/RAGAS 실호출, 항목8 ragas 재실행, 항목10 SSE text/event-stream 청크타이밍 — 모두 OpenAI/외부 API 키 미주입 또는 DB 미기동 사유. 점수는 정적 증거 기반, 신뢰도 규칙으로 4 이상 [S] 항목 상향 보류.
- 5 auditor 도메인: architecture(1·2·6), llm-stack(3·7), rag-eval(8·9), runtime-infra(5·10), docs(4) — 전부 [S]/[D] 증거 동봉.
- 보존 정책: `reports/BDAI_Pocat_Team2-fc3f2b7.md` 및 `reports/summary.csv` 미변경. 본 리포트는 신규 파일.
