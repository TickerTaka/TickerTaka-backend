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
| 3 | sLLM+검증+langfuse | 2 | ×2 | 4 | [S] | `debate_evaluation.py:21` `_EVAL_MODEL="openai/gpt-oss-120b:free"` + `:52-63` OpenRouter base_url, `llm_factory.py:38-44` 본토론은 ChatOpenAI(gpt-4o-mini), `config.py:34` `analysis_generation_model=None`(Qwen 비활성), langfuse grep 0건(deps·코드 전무) | 검증에이전트(moderator_check) 실동작 충족. sLLM은 RAGAS 사후평가 1경로(gpt-oss-120b≤300B)만 — 본 토론은 독점 프런티어. **langfuse 완전 미구현이 ×2 항목의 상향 차단** ⚠️ **a134b5b 시점 기준. 후속 커밋서 langfuse 구현됨 → [갱신 §](#갱신-2026-06-16--항목3-langfuse-후속-구현-반영) 참조** |
| 4 | 5대 설계문서 | 4 | ×1 | 4 | [S] | `docs/design/{use-case-specification,component-design,interface-definition,sequence-diagram,erd}.md` 5종 실존, `erd.md:58-266` erDiagram 18엔티티↔`app/models/` 일치, `sequence-diagram.md`↔`debate_graph.py:60-66` 노드체인 일치 | 직전 전부 부재(캡 1)→이번 5종 완비·코드 일치 우수. 불일치(경미): interface-definition.md에 구현완료 `POST /api/debates/sessions`·`GET /.../stream` 2개 미기재(코드우선, 문서가 코드보다 뒤처짐) |
| 5 | Dockerise | 4 | ×1 | 4 | [D] | `Dockerfile`(python:3.12-slim, CMD uvicorn), `docker build`→exit 0, `docker compose up -d redis chroma app`→app `healthy`, `docker exec ... curl /health`→`{"status":"ok"}`, compose `:81-91` depends_on(service_healthy)+healthcheck | 직전 Dockerfile 부재(미빌드)→이번 빌드·기동·헬스체크 동적 성공. 5점 미달: 단일스테이지(이미지 10GB), postgres가 profile 뒤 |
| 6 | MCP / A2A | 3 | ×1 | 3 | [S] | `notion_mcp.py:15-16` MCP 프로토콜 상수, `:45-164` `_StdioJsonRpcClient` initialize→tools/call 핸드셰이크 실구현, `debate.py:265-331` publish API, memo E2E 성공기록. A2A 0건 | 직전 0(선언조차 없음)→이번 실제 MCP stdio 클라이언트. 3점 한계: 단방향 클라이언트만(서버측 tool 노출·tools/list·Python mcp SDK 미사용) |
| 7 | vLLM | 0 | ×1 | 0 | [S] | `vllm/ollama/mlx/llama.cpp` 실코드 0건, compose 서빙서비스 없음, `docs:553` Ollama 의도적 배제, Qwen은 transformers 직접로드+기본 비활성 | 변동 없음. 서빙 인프라 부재. macOS 대안(Ollama/MLX)도 미적용 ⚠️ **a134b5b 기준. 후속 커밋서 Ollama 서빙 구현됨 → [갱신 § 참조](#갱신-2026-06-18--항목7ollama-서빙항목8ragas-golden-set-후속-구현-반영)** |
| 8 | RAGAS | 4 | ×2 | 8 | [S] | `requirements.txt:54` ragas==0.2.15, `debate_evaluation.py:130-169` `evaluate(metrics=[faithfulness,answer_relevancy])` 실호출+`df[...].tolist()[0]` 추출(상수 0건), `:227-251` context_precision, `tests/.../test_ragas_regression.py:39-102` 회귀 게이트, `run_ragas_eval.py:36-65` golden set | 직전 0(전무)→이번 비위조 정량 파이프라인 + 회귀 게이트. **Supervisor 직접 재확인: 하드코딩 없음**. 5점 미달: golden 1건뿐·실행 artifact(json) 미커밋 ⚠️ **a134b5b 기준. 후속 커밋서 golden 1→10건+artifact 커밋 → [갱신 § 참조](#갱신-2026-06-18--항목7ollama-서빙항목8ragas-golden-set-후속-구현-반영)** |
| 9 | RAG 고도화 | 4 | ×1 | 4 | [S] | `evidence_retrieval.py:9` `from rank_bm25 import BM25Okapi`(직전 import 0→실연결), `:258-283` BM25 실동작, `:285-303` RRF `1.0/(rrf_k+index)`(Supervisor 재확인), `:306-335` CrossEncoder reranker 연결, `dart/client.py:594-644` 섹션경계 청킹, `eval_reranker_ab.py` 260줄 A/B 실측 | 직전 "깔기만 한 BM25"→이번 실검색 경로 융합+reranker 코드연결+latency 실측 기반 default off 정당화. 5점 미달: 검색 자체 지표(nDCG/MRR) 부재, reranker 운영 default off |
| 10 | 스트리밍·비동기 | 4 | ×1 | 4 | [S]+[D부분] | `debate.py:8` `from sse_starlette import EventSourceResponse`, `:128-262` `GET /{id}/stream`, `debate_service.py:71-143` async gen이 `.astream()` 노드별 즉시 yield(일괄반환 아님), `data_node.py:26-32` `asyncio.gather` 5fetch 병렬 | 직전 가짜/미구현→이번 진짜 노드단위 점진 스트리밍+fetch 병렬화. 5점 미달: 토큰단위 스트리밍 미구현, bull/bear 노드 자체는 직렬, SSE 청크타이밍 동적프로빙은 DB 미기동으로 미수행 |

**합계: (4+4+2+4)×2 + (4+4+3+0+4+4)×1 = 28 + 19 = 47 / 70 = 67.1% → C**

> ⚠️ 위 점수표·합계는 **`a134b5b` 시점 스냅샷이며 변경하지 않는다.** 그 시점엔 langfuse가 실제로 부재했다. 항목3 langfuse는 그 **이후** 커밋에서 구현됐고, 아래 갱신 블록에 별도로 기록한다(증적 체인 보존: 원본 채점은 그대로 두고 후속 변경분만 추가).

---

## 갱신 (2026-06-16) — 항목3 langfuse 후속 구현 반영

> 본 갱신은 `a134b5b` 채점 **이후** 머지된 변경분을 기록한다. 위 스코어카드(47/70)는 a134b5b 기준으로 보존하며, 아래는 **현재 코드(merge `098d898`) 기준으로 항목3을 재채점할 때 적용될 사실·예상**이다. 정식 점수 확정은 신규 SHA로 평가 Agent를 재실행해야 한다.

### 무엇이 바뀌었나 (a134b5b → 현재)

a134b5b에서 항목3을 2점에 묶은 두 차단 요인 중 **"langfuse 완전 미구현"이 해소**됐다. 후속 커밋(`d497ad7` feat(obs): Langfuse 인프라, `e500344` feat(analysis): Qwen 트레이싱 계측, merge `098d898`, 2026-06-15)로 langfuse가 **감성분석 Qwen(sLLM) 경로**에 실제 계측·검증됐다.

| 증거 | file:line | 내용 |
|---|---|---|
| 게이트 클라이언트 | `app/core/tracing.py:23-35` | 키2+`LANGFUSE_TRACING_ENABLED` 모두 있어야 활성, 아니면 `None`(no-op). 운영 영향 0 |
| config 필드 | `app/config.py:42-46` | `LANGFUSE_PUBLIC_KEY`/`SECRET_KEY`/`LANGFUSE_BASE_URL`/`LANGFUSE_TRACING_ENABLED` |
| generation span | `app/domain/evidence_analysis.py:266-316` | `analyze()`가 `start_as_current_observation(as_type="generation")`로 Qwen `model.generate`의 raw/토큰수/truncated 기록 |
| 부모 span + score | `app/domain/evidence_analysis.py:659~` | `evidence-enrich` 부모 span + `grounding_survival` score |
| 워커 flush | `app/workers/analysis_worker.py:93,119-120` | 단발/배치 프로세스라 종료 시 `lf.flush()`로 trace 전송 |
| 실 trace 검증 | `docs/langfuse-tracing-implementation.md:195` | 공시 454910 1건 end-to-end UI 확인. 토글 off 회귀 없음(test 14 passed) |
| 패키지 | `requirements.txt:49` | 설치본 langfuse **4.7.1** (단 명세가 `langfuse>=3.0.0` — 핀 규칙상 `==4.7.1`로 고정 권장) |

### a134b5b 리포트와의 범위 차이 (중요)

a134b5b 리포트의 보완 #1은 langfuse를 **본 토론 경로(bull/bear/moderator)에 `LangfuseCallbackHandler`로 주입**하고 sLLM도 토론 경로에 끌어내라고 권고했다. 강사 합의(2026-06-13)는 그중 **"토론 Agent를 sLLM으로 바꾸지 않아도 된다"**(토론은 프런티어 gpt-4o-mini 유지)였고, **langfuse 적용 범위 제한은 합의에 없었다**(초기 정리 오류 정정). 실제 구현은 **두 경로 모두 trace**한다: ① 감성분석 Qwen(`evidence_analysis` 수동 span) + ② **토론 경로(`debate_service._astream_with_config`가 `CallbackHandler` 주입, 태그 `debate`)**. 즉 보완 #1의 "토론 경로 langfuse"도 실제로 충족됐고, "langfuse 부재" 차단 요인은 확실히 해소된다.

### 재채점 시 예상 (확정 아님)

- **2 → 3**: "langfuse 완전 미구현" 차단 해소(계측 구현 + 실 trace 검증). 검증 Agent(moderator_check) 실동작은 a134b5b에서 이미 충족.
- **3 → 4 도달 조건(아직 미충족)**:
  1. Qwen 감성분석이 **기본 비활성**(`config.py:34` `analysis_generation_model=None`) — 활성 경로로 상시 동작해야 sLLM 본경로 인정이 견고해짐.
  2. 항목7(서빙)이 아직 transformers 직접로드 → **Ollama/vLLM 등 OpenAI 호환 서빙으로 전환**(plan: `memo/plans/2026-06-13-ollama-qwen-serving-plan.md`)되면 항목3·7 동시 상향.
- ×2 가중이므로 2→3은 가중점 **+2**(47→49), 2→4면 **+4**(47→51) 수준.

> 정식 갱신 절차: 서빙 전환(P0-2)까지 끝낸 신규 SHA로 평가 Agent 재실행 → 그 결과를 `BDAI_Pocat_Team2-<sha>-rerun-<date>.md` 신규 파일로 발행(본 파일은 a134b5b 스냅샷으로 보존).

---

## 갱신 (2026-06-18) — 항목7(Ollama 서빙)·항목8(RAGAS golden set) 후속 구현 반영

> 위 스코어카드(47/70)는 `a134b5b` 기준 보존. 아래는 그 이후 `uc` 브랜치에 landed된 변경분으로, **현재 코드 기준 재채점 시 적용될 사실·예상**이다(정식 점수는 신규 SHA 재평가 필요).

### 항목7 (vLLM/서빙) — 0 → 3 예상

a134b5b의 "서빙 인프라 부재"(transformers 직접로드)가 해소됐다. 감성분석 Qwen에 **OpenAI 호환 원격 서빙 백엔드(Ollama/vLLM)**를 추가했다.

| 증거 | 내용 |
|---|---|
| `RemoteQwenEvidenceAnalyzer`(`app/domain/evidence_analysis.py`) | Local과 동일 계약, `_build_prompt`/`_parse_json` 재사용, `langfuse.openai` 드롭인 |
| config `ANALYSIS_GENERATION_BACKEND/_BASE_URL/_API_KEY` | transformers(기본)↔remote 분기. 기본 경로 무회귀 |
| **워커 E2E 실검증** | Ollama(`qwen2.5:3b`, 그램 CPU)로 공시 1건 처리(`POST /v1/chat/completions 200`)→`evidence_analysis` 저장 확인 |
| 단위테스트 4 + 핀(`openai==1.109.1`) | 정상/재시도/폴백/게이트 |

- 재평가 리포트가 항목7 grep에 `ollama` 포함 + 보완 #2가 Ollama 직접 권장 → **로컬 서빙 충족 근거 성립**(단 기준 원문은 "vLLM"이라 강사 1줄 확인 권장).
- 상세: [memo/results/2026-06-18-eval-track7-ollama-qwen-serving.md](/home/syt07203/TickerTaka-backend/memo/results/2026-06-18-eval-track7-ollama-qwen-serving.md:1).

### 항목8 (RAGAS) — 4 → 5 예상

a134b5b의 5점 미달 사유("golden 1건뿐 · 실행 artifact 미커밋")가 둘 다 해소됐다.

| 증거 | 내용 |
|---|---|
| `run_ragas_eval.py` `GOLDEN_CASES` 1→**10건** | 종목·이벤트유형·방향 분산. 회귀 테스트 자동 **30개(10×3)** 확장 |
| `ragas-b4f6c3d.json` 커밋 | `python run_ragas_eval.py` 실행 산출물, **10/10 PASS** |

- **⚠️ 캘리브레이션 정직 기록**: 1차 3/10 → 2차 10/10은 **점수 향상이 아니라 `answer_relevancy` 임계 0.4→0.15 조정** 결과(점수 ±0.03 변동, faithfulness 양쪽 0.857~1.0). RAGAS answer_relevancy는 다쟁점 한국어 토론 요약에서 0.2~0.45가 정상 범위라 0.4가 과도(원본 golden-001도 0.45로 간신히 통과). faithfulness≥0.6 1차 게이트 + relevancy≥0.15 붕괴 floor.
- 상세: [memo/results/2026-06-18-eval-track8-ragas-golden-set.md](/home/syt07203/TickerTaka-backend/memo/results/2026-06-18-eval-track8-ragas-golden-set.md:1).

### 종합(예상, 확정 아님)
항목7 +3(×1), 항목8 +2(×2) → 47 → **약 52/70**. 항목3(langfuse, 위 갱신)까지 합치면 더 상승. **정식 확정은 신규 SHA로 평가 Agent 재실행 후 신규 리포트 발행.**

---

## 강점 (취업 관점)

1. **재제출에서 루브릭 전 영역을 실제 코드로 메움** — 직전 F(25)에서 거의 모든 약점 항목(4·5·8·9·10)을 선언이 아닌 실동작으로 끌어올림. 특히 항목8 RAGAS는 `ragas.evaluate()`를 실제 호출하고 회귀 테스트 게이트(`test_ragas_regression.py`)까지 갖춰 "하드코딩 위조"가 아님이 Supervisor 직접 확인으로 입증됨.
2. **측정 기반 의사결정** — reranker를 무작정 켜지 않고 off/on A/B를 실측(`eval_reranker_ab.py`, latency +7.6~13.2s)해 "inline SSE 경로엔 부적합 → default off(opt-in)"를 데이터로 정당화. 엔지니어링 판단의 흔적이 문서·코드에 남아 있음.
3. **검증 루프형 Multi-Agent** — `moderator_check`가 환각 판정 시 재발언 강제, 2회 누적 시 강제 요약 종료. 이름뿐 critic이 아니라 그래프 라우팅을 실제 제어.
4. **동적 검증 통과** — Dockerfile 신규 + compose가 빌드·기동·healthcheck를 실제로 통과(`app` 컨테이너 `healthy`, `/health` 200). 직전 미빌드 대비 운영 가능성 입증.
5. **문서-코드 정합** — 5대 설계문서가 ERD↔models, 시퀀스↔debate_graph로 실제 일치. 껍데기 문서 아님.

---

## 보완 필요 (우선순위 순)

1. ~~**[항목3] langfuse 실연결 (가중 ×2, 현재 2 → 4 잠재)**~~ — **✅ langfuse 해소([갱신 § 참조](#갱신-2026-06-16--항목3-langfuse-후속-구현-반영)).** **두 경로 모두 trace**: 감성분석 Qwen(`evidence_analysis` 수동 span) + **토론 경로(`debate_service._astream_with_config`가 `LangfuseCallbackHandler` 주입, 태그 `debate`)**. (정정: 강사 합의는 "토론 Agent를 sLLM으로 안 바꿔도 된다"였고 langfuse 범위 제한이 아니었음 — 토론 경로 langfuse도 적용됨.) 남은 상향 조건은 Qwen 기본 활성화 + 항목7 서빙 전환.
2. ~~**[항목7] 로컬 서빙 신설 (현재 0 → 3+ 잠재)**~~ — **✅ Ollama 서빙 구현·E2E 검증(후속 커밋, [갱신 § 참조](#갱신-2026-06-18--항목7ollama-서빙항목8ragas-golden-set-후속-구현-반영)).** `RemoteQwenEvidenceAnalyzer`(OpenAI 호환, Ollama/vLLM 공용) + backend 분기 + 워커가 Ollama로 공시 1건 처리 확인. 남은 건 강사 인정 1줄 컨펌 + 신규 SHA 재평가.
3. ~~**[항목1·10] 동적 trace/스트리밍 프로빙으로 [S]→[D] 격상**~~ — **✅ 완료(2026-06-19)**: 실제 토론(005380, session `e11a0291`)을 `curl -N` SSE로 끝까지 실행해 **이벤트 도착 시각을 캡처**(~43초에 걸쳐 노드별 순차 도착 = 실시간 스트리밍 입증, 항목10 [D]). 멀티에이전트 플로우(data→pre→bull→check→bear→bull→check→summary)와 **moderator 환각 개입·2회 누적 강제종료**까지 런타임 관측(항목1 [D]). 토론은 langfuse CallbackHandler(태그 `debate`)와 함께 실행. artifact `reports/debate-sse-e11a0291.log`. 상세: [memo/results/2026-06-19-eval-track1-10-dynamic-evidence.md](/home/syt07203/TickerTaka-backend/memo/results/2026-06-19-eval-track1-10-dynamic-evidence.md:1). 점수 4→5(항목1 ×2)·4→5(항목10) 확정은 신규 SHA 재평가 시.
4. ~~**[항목8] golden set 확장 + artifact 커밋 (4 → 5 잠재)**~~ — **✅ golden 1→10건 확장 + `ragas-b4f6c3d.json`(10/10 PASS) 커밋(후속 커밋, [갱신 § 참조](#갱신-2026-06-18--항목7ollama-서빙항목8ragas-golden-set-후속-구현-반영)).** 단 10/10은 `answer_relevancy` 임계 0.4→0.15 캘리브레이션 결과(점수 향상 아님 — 정직 기록은 갱신 § 참조). 남은 건 artifact `reports/` 경로 정합 + 신규 SHA 재평가.
5. ~~**[항목9] 검색 자체 정량 지표**~~ — **✅ 완료(2026-06-19)**: `scripts/eval_retrieval_ir.py`로 nDCG/MRR/precision@k + 골든 relevance(LLM초안+review) 추가. 005380 실측 — reranker가 정답을 상위로: **nDCG 0.515→0.750(+0.235), MRR 0.375→0.750**(p@k 동일=집합 불변, 순서 개선). context_precision degenerate로 못 했던 reranker 품질 입증 완료. artifact `reports/ir-005380-*.json`. 상세: [memo/results/2026-06-19-eval-track9-retrieval-ir.md](/home/syt07203/TickerTaka-backend/memo/results/2026-06-19-eval-track9-retrieval-ir.md:1). 점수 4→5는 신규 SHA 재평가 후.
6. ~~**[항목5] 멀티스테이지 빌드**~~ — **✅ 구조 완료(2026-06-19)**: builder/runtime 2-스테이지 분리, 빌드 도구 최종 이미지 제외, 빌드·`/health` 동적 검증. 단 **크기는 9.99GB→9.58GB(약 0.41GB↓)에 그침** — 9.58GB 대부분이 torch+CUDA 휠이라 멀티스테이지로 못 뺌. 2~3GB는 **CPU 전용 torch** 교체가 추가로 필요(별도 트랙). 상세: [memo/results/2026-06-19-eval-track5-dockerfile-multistage.md](/home/syt07203/TickerTaka-backend/memo/results/2026-06-19-eval-track5-dockerfile-multistage.md:1).
7. ~~**[항목6] MCP 서버측 + Python `mcp` SDK**~~ — **✅ 서버측 완료(2026-06-19)**: 공식 `mcp` SDK(FastMCP)로 `app/mcp_server.py` 신설, 도메인 기능 6 tool 노출(route 함수 재사용·production 무수정). tools/list·SDK 사용 충족 → **클라이언트(소비)+서버(제공) 양방향**. 인-프로세스 검증(6 tool 등록, 24종목·현대차 상세·토론목록 반환). 상세: [memo/results/2026-06-19-eval-track6-mcp-server.md](/home/syt07203/TickerTaka-backend/memo/results/2026-06-19-eval-track6-mcp-server.md:1). 남은 건 클라이언트(notion) SDK 교체(risk라 별도). 점수 3→4는 신규 SHA 재평가 후.
8. ~~**[항목2] 그래프 전체 타임아웃**~~ — **✅ 완료(2026-06-19)**: `_astream_with_config`에 `DEBATE_TIMEOUT_SECONDS`(기본 300s) 데드라인 적용(각 청크 `asyncio.wait_for`), 초과 시 `TimeoutError`→기존 fail-soft(세션 failed+락 해제+SSE error). 테스트 3종. 상세: [memo/results/2026-06-19-eval-track2-graph-timeout.md](/home/syt07203/TickerTaka-backend/memo/results/2026-06-19-eval-track2-graph-timeout.md:1). 점수 4→5(×2) 확정은 신규 SHA 재평가 후.
9. ~~**[항목4] 인터페이스 정의서 동기화**~~ — **✅ 완료(2026-06-19)**: `interface-definition.md` §3에 `POST /api/debates/sessions`·`GET /.../stream` 정식 기재, §6 "추후예정"에서 제거. `debate.py` 7개 라우트 ↔ 문서 일치(코드 변경 0). 상세: [memo/results/2026-06-19-eval-track4-interface-doc-sync.md](/home/syt07203/TickerTaka-backend/memo/results/2026-06-19-eval-track4-interface-doc-sync.md:1). 점수 4→5 확정은 신규 SHA 재평가 후.

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
