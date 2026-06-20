# 검토 리포트 (3차 재검토) — BDAI_Pocat_Team2 (TickerTaka) @ `39b3daf`

> **직전 `e839d98` 60/70 (85.7%) A → 본 `39b3daf` 62/70 (88.6%) 등급 A.** 항목4(문서동기화) 4→5, 항목7(Ollama compose 서빙) 2→3. 회귀 없음. backend 13커밋·frontend 6커밋 추가분이 직전 잔여약점(항목7 서빙 미기동·항목4 문서불일치 2건·항목6 MCP클라)을 정조준. 핵심 진위판정(① Ollama compose 실서빙 ② multi-provider의 sLLM 전환 여부/anthropic 오판 ③ MCP 양방향 증적의 실행 여부) 본문 하단 포함.

- 모드: **Full** (정적 5종 병렬 + 동적: `docker compose config --services` 격리검증 [D]·MCP `mcp_selftest` tools/list 7개 round-trip [D]·SSE 타이밍 로그 점진성 [D]) / 검토 일시: 2026-06-20
- 대상: `BDAI_Pocat_Team2/TickerTaka-backend` (메인, `39b3daf`) + `TickerTaka-Frontend` (`2ad5f2f`)
- 직전 리포트: `reports/BDAI_Pocat_Team2-e839d98-rerun2-2026-06-20.md` (backend `e839d98`, 60/70 A) — **보존, 미변경**
- 종합: **62 / 70 (88.6%) → 등급 A**
- 소프트 게이트: **미발동** — 항목1=5·2=5·8=5 충족, 동적 검증 성공(Full 유지, Reduced 전락 없음).
- 오케스트레이션: Supervisor가 5 auditor를 1턴 병렬 디스패치 → 증거 형식 검사 → 문서-코드 교차검증 → 단독 합성. **Supervisor 직접 재확인**: 항목7 서빙 artifact가 `.md` 서술(실 `.log` 아님)임을 git show로 확인하고 상향을 compose 서비스 실재+코드경로로만 근거화, 항목3 default provider=openai/gpt-4o-mini(config.py:82-88)임을 직접 확인해 sLLM 미전환 판정, openrouter 로그가 opt-in 1회 실행임을 확인.

---

## 항목별 스코어카드

| # | 항목 | 점수 | 가중 | 가중점 | 신뢰 | 증거 (file:line / cmd) | 코멘트 (직전 e839d98 대비) |
|---|------|:---:|:---:|:---:|:---:|------|------|
| 1 | Multi-Agent 구조 | 5 | ×2 | 10 | [S] | `debate_graph.py:52-71` StateGraph 7노드, `:27-48` `_router` 3변수(moderator_flag·topic_index·turn) 4방향 동적 라우팅, `:68-69` `judge_agent→moderator_summary→END` 수렴 고정. `84fa1b6` 변경은 `_router` 조기종료를 hallucination_count 하드코딩→`moderator_flag=="end"` 상태기반으로 리팩터(구조 무변) | **5→5 (회귀 없음)**: bull/bear/moderator_node 변경이 Supervisor-worker 분리·동적라우팅·수렴 3요건 무손상. per-agent 환각카운터 도입으로 중재로직 오히려 강화 |
| 2 | 에러핸들링·폴백 | 5 | ×2 | 10 | [S] | 노드별 try/except+fallback(`bull_node.py:103-108`,`bear_node.py:107-112` `@retry(3,exp)`, moderator/judge/summary 각 except+의미있는 fallback), `llm_factory.py:124-155` retry+`invoke_with_fallback`, `debate_service.py:339-352` `asyncio.wait_for` 데드라인. `84fa1b6` `_build_client`·`_fallback_model_id`가 provider별 fallback 체인 완성, `_sanitize_verdict()` parser 방어 추가 | **5→5 (실질 강화)**: multi-provider 폴백 분기·parser hardening·corrections 이력이 기존 5계층 방어에 누적. `except:pass` 침묵삼킴 0건 |
| 3 | sLLM+검증+langfuse | 3 | ×2 | 6 | [S] | (a)sLLM: `config.py:82` provider default=**openai**, `:85-88` debate 전노드 **gpt-4o-mini**. `evidence_analysis.py:392-458` `RemoteQwenEvidenceAnalyzer`만 Qwen2.5:3b(오픈웨이트 ≤300B) 1경로. (b)검증: `debate_graph.py:67`+`moderator_node.py:158-255` verdict→conditional routing 게이팅 실동작. (c)langfuse: `debate_service.py:314-322` CallbackHandler `config["callbacks"]` 실주입, `moderator_node.py:268-276` create_score. `requirements.txt:51` langfuse==4.7.1 | **3→3 (상향 차단 유지)**: `84fa1b6` multi-provider는 anthropic(claude=독점 프런티어, **sLLM 불인정**)+openrouter(opt-in, 기본 아님) 추가일 뿐 **debate 본경로 sLLM 전환 아님**. `debate-openrouter-*.log`는 실 실행이나 선택형 1회. sLLM은 여전히 감성분석 워커 1경로. langfuse 키 미주입으로 실trace [S] |
| 4 | 5대 설계문서 | 5 | ×1 | 5 | [S] | 5종 실존(부재 0). `interface-definition.md:134` `decision_agent` 요청바디 필드 추가↔`schemas/debate.py:15` `pattern="^(moderator\|judge)$"` 타입·기본값 1:1 일치. `component-design.md:56-61`·`sequence-diagram.md:156-158` `judge_agent` 노드 추가↔`debate_graph.py:23-24,59,68` 일치. multi-provider 문서가 "기본 openai"로 정직 기재(`component-design.md:114-116`)↔코드 일치, 과장 0 | **4→5**: 직전 5점 차단했던 2건(`decision_agent` 요청바디 미기재·`judge_agent` 노드 설계누락)이 `4e23f9a`로 3개 문서에 코드대조 가능 실내용으로 해소. 잔여는 `judge_agent_node` 위치 라인오기(`:224`↔실제 `:281`)뿐 — 노드 존재·흐름 정확해 감점 불가 경미 |
| 5 | Dockerise | 5 | ×1 | 5 | [D] | `Dockerfile:3` `AS builder`/`:25` `AS runtime` 멀티스테이지 **무변경**. `docker compose config --services`→`chroma redis app worker`(ollama/ollama-init·postgres 모두 기본 up 격리). `docker-compose.yml:137,155` 신규 ollama/ollama-init `profiles:["ollama"]`, app·worker `depends_on:service_healthy`+healthcheck 유지 | **5→5 (회귀 없음)**: ollama 서비스 +50줄은 순수 추가(L117-164), 기존 5서비스 라인 무수정. profile 격리로 기본 `up` 무영향. `docker compose config` exit 0 [D] |
| 6 | MCP / A2A | 4 | ×1 | 4 | [D] | `mcp_server.py:34,62,74,86,99,111,129` `@mcp.tool()` **7개**(직전 6+`add_watchlist`). `add_watchlist`(`:130-179`)는 WatchlistService+5 sync 순차호출, 개별 실패 격리. **[D] `mcp_selftest` tools/list→7 tool 반환** round-trip PASS. `notion_mcp.py:84-111` 공식 `ClientSession`/`stdio_client` SDK | **4→4**: 서버측 6→7 tool+selftest [D]로 강화됐으나, `reports/mcp-e2e-2026-06-20.md`는 **자가서술 docs**(Notion publish JSON 학생 기재, 내부 tools/list 캡처가 구버전 6개)로 클라이언트 round-trip 독립 [D] 증거 부재 → 5점 차단 |
| 7 | vLLM | 3 | ×1 | 3 | [S] | vllm deps 여전히 없음(주석만 `docker-compose.yml:133`). **신규 ollama compose 서비스**(`docker-compose.yml:136-164` ollama+ollama-init, `profiles:["ollama"]`, `ollama-init`이 `qwen2.5:3b` 자동 pull). `evidence_analysis.py:392-458` `RemoteQwenEvidenceAnalyzer`가 base_url→Ollama/vLLM OpenAI호환 `chat.completions.create`. 증적 `reports/ollama-remote-serving-e2e-2026-06-20.md`(curl POST 200+`fp_ollama`)·`ollama-serving-e2e-2026-06-20.md`(httpx POST 200) | **2→3**: 직전 "compose 미포함" 약점 해소 — ollama가 **실제 compose 서비스로 진입**(profile)+모델 자동pull+코드경로 연결. macOS/로컬 Ollama 서빙 인정 원칙 적용. **단 증적은 `.log` 실행캡처 아닌 `.md` 서술**이라 [D] 미달, vLLM은 여전히 가이드뿐(가점 없음)→4 차단 |
| 8 | RAGAS | 5 | ×2 | 10 | [S] | `run_ragas_eval.py:44-325` golden 10건, `debate_evaluation.py:162,252` `evaluate()` 실호출, `reports/ragas-b4f6c3d.json` 비위조(faithfulness golden-005=0.857 외 1.0, answer_relevancy 10개 이질소수점, evidence_precision 0/0.999/0.5 혼합), `test_ragas_regression.py` 10×3=30 회귀. `git log e839d98..39b3daf -- <RAG코어>`→0커밋 | **5→5 (회귀 없음)**: 13커밋이 RAG 평가 코어 무변경 확인. artifact 비위조 재확인(소수점 분산=실측) |
| 9 | RAG 고도화 | 5 | ×1 | 5 | [S] | `evidence_retrieval.py:9,116-131,285-304` BM25+Vector+RRF(`1.0/(rrf_k+index)`), `:306-335` CrossEncoder `model.predict()` 실연결, `dart/client.py:594-644` 섹션→문자한도 2단계 청킹, `scripts/eval_retrieval_ir.py:55-75` nDCG/MRR/p@k, `reports/ir-005380-b36248a.json` MRR 0.375→0.75·nDCG 0.515→0.75(Δ+0.235). `git log`→0커밋 | **5→5 (회귀 없음)**: hybrid+reranker+청킹+IR지표 4요건 39b3daf 유지 |
| 10 | 스트리밍·비동기 | 4 | ×1 | 4 | [D] | `debate.py:9,135` `EventSourceResponse`, `debate_service.py:328-336` `astream` 노드완료 즉시 yield, `data_node.py:26` `asyncio.gather` 5병렬fetch. `reports/debate-sse-e11a0291.log` 청크 2~4s 간격 점진(43s 전체). `debate_service.py` +5줄은 state키 초기화(corrections·hallucination_count)로 SSE경로 무영향 | **4→4 (회귀 없음)**: SSE 점진성 [D] 유지. 여전히 **노드단위**(`stream_mode="messages"` 미전환)→토큰단위 아님으로 5 차단 |

**합계: (5+5+3+5)×2 + (5+5+4+3+5+4)×1 = 36 + 26 = 62 / 70 = 88.6% → A**

---

## 강점 (취업 관점)

1. **서빙 인프라를 코드+compose로 닫음** — 직전 "코드만 닫혀있고 미기동"이던 Ollama 경로를 실제 compose 서비스(profile 격리)+모델 자동 pull로 승격. `RemoteQwenEvidenceAnalyzer`가 base_url만 교체하면 vLLM 전환되는 OpenAI호환 추상화라 운영 이식성이 좋다. 항목7 2→3.
2. **문서-코드 일치성 완성** — 직전 잔존 불일치 2건(decision_agent 요청바디·judge_agent 노드)을 코드대조 가능한 실내용으로 동기화. 5대 문서 전종 실존+신규기능 과장 0. 항목4 4→5(만점).
3. **회귀 0의 견고한 베이스** — 13커밋(multi-provider·parser hardening·캐시 wipe·MCP tool 추가)이 항목1·2(만점)·8·9·10을 단 한 항목도 깨뜨리지 않음. 특히 항목2는 multi-provider 폴백 분기+`_sanitize_verdict` parser 방어로 실질 강화.
4. **MCP 서버 확장** — `@mcp.tool()` 6→7(`add_watchlist`, 부분실패 격리). selftest tools/list round-trip 7개 [D] 직접 재현.
5. **정직한 증적 문화** — RAGAS/IR artifact가 비위조(소수점 분산), 문서가 multi-provider를 "운영중"으로 허위주장하지 않고 "기본 openai"로 정확히 기재.

---

## 보완 필요 (우선순위 순)

1. **[항목3] 토론 본경로 sLLM 전환 (가중 ×2, 현재 3 → 4 잠재 = +2점)** — 가장 큰 미수확 가중점. multi-provider로 openrouter(≤300B 오픈웨이트) 경로는 열렸으나 **기본값이 여전히 openai/gpt-4o-mini**(`config.py:82-88`). `.env.example`의 `DEBATE_LLM_PROVIDER` 기본을 `openrouter`로 바꾸고 bull/bear/moderator 중 하나 이상을 검증된 오픈웨이트 모델(qwen2.5-72b 등)로 고정하면 (a) 충족이 "감성분석 워커 1경로"에서 "본토론 다경로"로 강화 → 3→4. anthropic(claude) 경로는 sLLM 가점 대상 아님에 유의. 동시에 키 주입 환경에서 langfuse 실 trace 1회 캡처 첨부 시 [S]→[D].
2. **[항목7] 서빙 기동 [D] 증적 (현재 3 → 4 잠재)** — compose 서비스는 갖췄으나 현 증적이 `.md` 서술(curl/httpx 출력 붙여넣기). sandbox에서 `docker compose --profile ollama up -d` 실기동 후 `/v1/models`·`/api/tags` 응답을 **터미널 raw `.log`로 커밋**하면 [S]→[D], 3→4.
3. **[항목6] MCP 클라이언트 E2E 독립증적 (현재 4 → 5 잠재)** — `mcp-e2e-2026-06-20.md`가 자가서술이고 내부 tools/list 캡처가 구버전(6개)이라 불일치. `NOTION_TOKEN` 유효 환경에서 Notion round-trip을 `mcp_selftest`급 스크립트로 캡처하거나 타임스탬프 포함 로그/스크린샷 제출 시 클라이언트측 [D], 4→5.
4. **[항목10] 토큰단위 스트리밍 (현재 4 → 5 잠재)** — `astream(state, config, stream_mode="messages")` 전환 후 LLM 토큰 청크를 SSE에 패스스루하면 노드단위→토큰단위, 5점.
5. **[항목4 경미]** — `component-design.md`의 `judge_agent_node` 위치 라인 `:224`를 실제 `:281`로 정정(감점 사유 아님, 정확성 차원).

---

## 핵심 진위 판정 요약 (이번 재검토 3대 쟁점)

- **① Ollama compose 실서빙: 절반 진짜 (compose O, [D]기동 X).** `docker-compose.yml:136-164`에 ollama+ollama-init 서비스가 **실제로 들어왔다**(profile 격리, `qwen2.5:3b` 자동 pull, healthcheck). Supervisor가 `docker compose config --services`로 기본 up 격리 직접 확인. 그러나 서빙 증적(`ollama-remote-serving-e2e-2026-06-20.md` 등)은 **`.log` 실행캡처가 아닌 `.md` 서술**(curl 응답·httpx 로그를 학생이 붙여넣음, `fp_ollama`·chatcmpl-id 등 형식은 실 산물에 부합하나 독립 재현 불가). 상향(2→3)은 "compose 서비스 실재+코드경로 연결"로 근거하고 [D]는 부여하지 않음. vLLM은 여전히 가이드(주석)뿐.
- **② multi-provider의 sLLM 전환: 전환 아님 (anthropic 오판 회피).** `84fa1b6`은 anthropic(claude-sonnet=**독점 프런티어, sLLM 불인정**)과 openrouter(opt-in)를 추가했을 뿐 **debate 기본 경로는 여전히 gpt-4o-mini**(`config.py:82` provider default=openai, `.env.example:35`). `debate-openrouter-*.log`는 실제 실행 로그이나 `--provider openrouter` **선택형 1회 실행**이며 기본/주 경로 전환이 아님. anthropic을 sLLM으로 오판하지 않았고, openrouter 선택형 실행을 기본전환으로 과대평가하지 않음 → 항목3 **3 유지**.
- **③ MCP 양방향 증적: 서버측 실행 [D] / 클라측 자가서술.** 서버 tools/list 7개는 `mcp_selftest` round-trip으로 **이번 세션 직접 재현 [D]**. 그러나 `mcp-e2e-2026-06-20.md`의 Notion 발행 E2E는 **자가서술 docs**(응답 JSON 학생 기재, 내부 tools/list 캡처가 add_watchlist 추가 전 구버전 6개라는 불일치). 클라이언트 round-trip 독립증거 부재 → 항목6 **4 유지**.

---

## 교차검증 노트 (문서 ↔ 코드)

- **신규기능 과장 검사(코드우선)**: docs-auditor가 multi-provider·ollama compose·MCP add_watchlist 서술을 타 auditor 코드실태와 대조 — `component-design.md:114-116`이 "현재 공급자 OpenAI 직접, openrouter는 토론경로 미적용"으로 **정직 기재**(코드 default=openai와 일치), ollama는 미기재이나 "운영중" 허위주장 없음. 과장 0건, 항목4 추가 감점 없음.
- **직전 불일치 2건 해소(코드 file:line 대조)**: `decision_agent` 요청바디↔`schemas/debate.py:15`, `judge_agent` 노드↔`debate_graph.py:23-24,59,68` 모두 일치 확인 → 항목4 4→5.
- **자가증적 커밋 엄격검증(Supervisor 직접)**: 이 팀은 `docs(eval)` 자가증적 커밋이 많아, 항목6·7의 `.md` 증적이 실행산출인지 git show로 직접 확인. mcp-e2e·ollama-serving 모두 **서술형 docs**로 판정해 [D] 부여 보류, 점수는 실코드+compose+독립[D](selftest·compose config)로만 근거화.

---

## 신뢰도·게이트 적용

- 소프트 게이트 미발동: 항목1=5·2=5·8=5 충족, 동적 검증 Full 성공(Reduced 전락 없음).
- 신뢰도 규칙: 동적가능 항목 중 [S]로 4점 이상인 항목 없음(항목5·6·10은 [D] 확보, 항목3·7은 score 3이라 ≥4 상향보류 규칙 비대상). 항목8·9는 [S]이나 5점 근거가 "비위조 커밋 artifact+공식 실호출 코드"이며 키 미주입은 감점 사유 아님 → 5 유지. 항목7은 `.md` 서술 증적을 [D]로 인정하지 않고 [S] 3점으로 보수 처리.

---

## 직전 리포트(e839d98) 대비 진전 — SHA 비교

- **저장소 진전**: `git merge-base --is-ancestor e839d98 39b3daf` YES. `git diff --stat e839d98 39b3daf`: **28 files, +1359/-85**, backend 13커밋·frontend 6커밋.
- **항목별 변화**: 1(5→5), 2(5→5), 3(3→3), 4(4→**5**), 5(5→5), 6(4→4), 7(2→**3**), 8(5→5), 9(5→5), 10(4→4). **회귀 0건.**
- **종합**: **60 → 62 / 70 (85.7% → 88.6%), 등급 A 유지 (+2점).** 이미 최상위라 등급선 변동 없이 잔여약점 2개(문서동기화·서빙 compose)를 정직히 마감. 남은 상승여지는 항목3 본경로 sLLM 전환(+2 가중점)·항목7 [D]기동·항목6 클라E2E·항목10 토큰스트림.

---

## 재현 정보

- 커밋: backend `39b3daf` / frontend `2ad5f2f` (둘 다 git 저장소, `git rev-parse --short HEAD`).
- 직전 대비: `git diff --stat e839d98 39b3daf` → 28 files, +1359/-85 (backend 13커밋).
- 동적([D]): `docker compose config --services`→ollama/postgres 기본 up 격리(profile) 확인 · `python -m scripts.mcp_selftest` tools/list **7 tool** round-trip PASS(`add_watchlist` 포함, call_tool은 DB 미연결로 OperationalError=예상) · `reports/debate-sse-e11a0291.log` SSE 청크 2~4s 점진 · Dockerfile 멀티스테이지 무변경(직전 build exit 0 승계).
- 미검증(동적): 항목3 langfuse 실trace·debate sLLM 실호출, 항목7 Ollama 서빙 실기동(증적은 `.md` 서술), 항목8 ragas 재실행, 항목6 Notion call_tool E2E — 외부 API키 미주입/DB 미기동/Docker-Ollama 이미지 pull 불가 사유. 정적+커밋 artifact로 채점, 환경한계 감점 없음.
- 5 auditor 도메인: architecture(1·2·6), llm-stack(3·7), rag-eval(8·9), runtime-infra(5·10), docs(4) — 전부 [S]/[D] 증거 동봉.
- 보존 정책: `reports/BDAI_Pocat_Team2-e839d98-rerun2-2026-06-20.md`·`reports/BDAI_Pocat_Team2-a134b5b-rerun-2026-06-13.md`·`reports/BDAI_Pocat_Team2-fc3f2b7.md`·`summary.csv`·`rank_change.csv` 미변경. 본 리포트만 신규 파일.
