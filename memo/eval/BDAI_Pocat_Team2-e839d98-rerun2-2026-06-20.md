# 검토 리포트 (2차 재검토) — BDAI_Pocat_Team2 (TickerTaka) @ `e839d98`

> **비고:** 직전 `a134b5b` **47/70 (67.1%) 등급 C** → 본 `e839d98` **60/70 (85.7%) 등급 A**. backend 59커밋·frontend 4커밋 추가분이 직전 미수확 항목(langfuse·MCP서버·멀티스테이지·golden셋·IR지표·그래프타임아웃)을 실코드+동적증적으로 정조준 보완. 항목3(langfuse)·항목6(MCP서버)·항목7(Ollama) 진위 판정 본문 하단 포함.

- 모드: **Full** (정적 5종 병렬 + 동적: docker build [D]·MCP tools/list round-trip [D]·debate timeout pytest 3 passed [D]·실토론 SSE 타이밍 로그 [D]) / 검토 일시: 2026-06-20
- 대상: `BDAI_Pocat_Team2/TickerTaka-backend` (메인, `e839d98`) + `TickerTaka-Frontend` (`1b25830`)
- 직전 리포트: `reports/BDAI_Pocat_Team2-a134b5b-rerun-2026-06-13.md` (backend `a134b5b`, 47/70 C) — **보존, 미변경**
- 종합: **60 / 70 (85.7%) → 등급 A**
- 소프트 게이트: **미발동** — 항목1=5·2=5·8=5 모두 충족, 동적 검증 성공(Full 유지, Reduced 전락 없음).
- 오케스트레이션: Supervisor가 5 auditor를 1턴 병렬 디스패치 → 증거 형식 검사 → 문서-코드 교차검증 → 단독 합성. **Supervisor 직접 재확인**: SSE 로그가 e839d98 커밋에 실존(`git cat-file -e`), langfuse CallbackHandler가 `config["callbacks"]`에 실주입(import-only 아님), 그래프 타임아웃 `asyncio.wait_for` 실구현, MCP FastMCP `@mcp.tool()` 6종.

---

## 항목별 스코어카드

| # | 항목 | 점수 | 가중 | 가중점 | 신뢰 | 증거 (file:line / cmd) | 코멘트 (직전 대비) |
|---|------|:---:|:---:|:---:|:---:|------|------|
| 1 | Multi-Agent 구조 | 5 | ×2 | 10 | [D] | `debate_graph.py:56-75` StateGraph 7노드, `:27-52` `_router` 조건부 라우팅+hallucination_count≥2 강제종료, 모든 경로 `moderator_summary` 수렴. `reports/debate-sse-e11a0291.log`(커밋 실존) 실토론: data_agent→moderator_pre→bull→moderator_check(환각 탐지·개입)→bear→bull→count2 강제종료→summary | **4→5**: 직전 동적 trace 부재로 상향 보류였던 것을, 실토론 SSE 로그(나노초 타임스탬프·실제 환각판정·강제 라우팅·summary 단독생성)로 [S]→[D] 격상. Supervisor·worker 분리+동적 라우팅+수렴 3요건 런타임 실관측 |
| 2 | 에러핸들링·폴백 | 5 | ×2 | 10 | [D] | 노드별 try/except+fallback(bull/bear/moderator_pre/judge/summary), `llm_factory.py:67-102` tenacity retry+`invoke_with_fallback`, `config.py:86` `DEBATE_TIMEOUT_SECONDS=300`, **`debate_service.py:325-345` `asyncio.wait_for` 그래프 전체 데드라인 루프**, `tests/test_agents/test_debate_timeout.py` pytest **3 passed** | **4→5**: 직전 잔여 미달(그래프 전체 타임아웃 부재)을 커밋 1957ade `asyncio.wait_for` deadline 루프로 해소, 테스트 3건 [D] 통과. 노드단위 retry/fallback+fallback모델체인+그래프타임아웃+세션 fail마킹 5계층 방어 |
| 3 | sLLM+검증+langfuse | 3 | ×2 | 6 | [S] | (c)langfuse **실주입**: `debate_service.py:309-312` `CallbackHandler()`를 `config["callbacks"]`에 주입+`metadata` 세션연결(Supervisor 직접확인), `evidence_analysis.py:269-316` Qwen generation span+`score_current_trace`, `analysis_worker.py:95` `flush()`, `moderator_node.py:201-220` hallucination score. (a)sLLM: `evidence_analysis.py:253-329` Qwen2.5-1.5B/3B 감성분석 실호출. (b)`moderator_check_node`가 verdict로 그래프 라우팅 게이팅 | **2→3**: 직전 상향차단 사유(langfuse deps·코드 전무)가 **완전 해소** — import-only가 아니라 콜백 실주입 와이어업 확인. 다만 키 미주입(sandbox)으로 실 trace 동적 미확인 [S]이고, **토론 본경로는 여전히 gpt-4o-mini 독점 프런티어**(sLLM은 감성분석 워커 1경로). 두 사유로 4 아닌 3 |
| 4 | 5대 설계문서 | 4 | ×1 | 4 | [S] | `docs/design/{use-case,component-design,interface-definition,sequence-diagram,erd}.md` 5종 실존(부재 0). `interface-definition.md:157-173` `POST /api/debates/sessions`·`:187-207` `GET /.../stream` **정식 기재**↔`debate.py:64,130` 일치, ERD 18엔티티↔`app/models/` 전수일치. 신규 Langfuse/Qwen/MCP/Hybrid 서술 코드 과장 0 | **4→4**: 직전 경미 미달(sessions·stream 미기재)은 커밋 333f924로 해소. 단 잔존 경미 2건 — `DebateCreateRequest.decision_agent` 요청바디 미기재(`interface-definition.md:129-133`↔`schemas/debate.py:15`), `judge_agent` 노드가 시퀀스/컴포넌트 설계에 누락(`debate_graph.py:14,63`)으로 5 아닌 4 유지 |
| 5 | Dockerise | 5 | ×1 | 5 | [D] | `Dockerfile:3` `AS builder`/`:25` `AS runtime` 2스테이지, `:13` builder만 build-essential, `:40` `COPY --from=builder /opt/venv`(빌드도구 최종이미지 제외). `docker build --no-cache` exit 0, 최종 9.61GB(직전 단일 10GB). 전 서비스 healthcheck+`depends_on: service_healthy` | **4→5**: 직전 미달(단일스테이지 10GB) 해소 — 멀티스테이지 builder/runtime 분리+빌드도구 제외 [D] 빌드성공·이미지 축소 확인 |
| 6 | MCP / A2A | 4 | ×1 | 4 | [D] | `app/mcp_server.py:19-130` FastMCP 서버+`@mcp.tool()` **6 tool**, `notion_mcp.py:85-100` 공식 `ClientSession`/`stdio_client` SDK 교체, `requirements.txt:51` mcp==1.28.0. **[D] tools/list 실증**: `mcp._tool_manager.list_tools()`→6 tool 반환, `mcp_selftest.py` `ListToolsRequest` round-trip PASS | **3→4**: 직전 한계(단방향 클라만·서버 tool 노출 없음·SDK 미사용) 3가지 모두 해소 — 서버 신설+tools/list [D]+공식 SDK. 5점 미달: 클라이언트 E2E(Notion 발행·call_tool)는 키/DB 미주입으로 [S](self-test의 call_tool은 DB 미연결로 미실증) |
| 7 | vLLM | 2 | ×1 | 2 | [S] | vllm deps·compose 서비스 **없음**. `evidence_analysis.py:392-458` `RemoteQwenEvidenceAnalyzer`가 `ANALYSIS_GENERATION_BACKEND=remote`+`base_url` 시 Ollama/vLLM OpenAI호환 `chat.completions.create` 실호출, `:639-647` config 분기. 기본값 `transformers`(인프로세스), `.env.example` base_url 공란 | **0→2**: 직전 0(서빙 전무)에서, Ollama/vLLM OpenAI호환 원격서빙 분기가 **실코드로 연결**(보고서-only 아님)됨을 확인해 탈출. 단 compose에 서빙서비스 없음+기본 비활성+감사자 독립 동적기동 불가로 3 미달. macOS 로컬서빙 인정 원칙 적용했으나 코드만 닫혀있고 미기동 |
| 8 | RAGAS | 5 | ×2 | 10 | [S] | `run_ragas_eval.py:44-325` golden **10건**(golden-001~010), `reports/ragas-b4f6c3d.json` 커밋된 실행 artifact **비위조**(faithfulness 9건 1.0/1건 0.857=6/7, answer_relevancy 0.244~0.445 각기 다른 소수·eval_at·git_sha 동반), `debate_evaluation.py:162-169` `evaluate()` 실호출+df 추출(상수 0건), `test_ragas_regression.py:29` 10건×3지표=30 회귀테스트 | **4→5**: 직전 5점 미달 2사유(golden 1건·artifact 미커밋) 모두 해소 — golden 10건 코드확인+비위조 artifact 커밋. Supervisor 재확인: 소수점 분산=실측, 하드코딩 아님 |
| 9 | RAG 고도화 | 5 | ×1 | 5 | [S] | `evidence_retrieval.py:9,119-303` BM25+Vector+RRF(`1.0/(rrf_k+index)`), `:306-335` CrossEncoder reranker 실연결, `dart/client.py:594-644` 섹션+크기 2단계 청킹. **신규 IR지표**: `scripts/eval_retrieval_ir.py:55-75` nDCG/MRR/p@k 공식 직접구현, `reports/ir-005380-b36248a.json` 4쿼리 off/on 실측(avg nDCG off=0.515→on=0.75, Δ+0.235) | **4→5**: 직전 미달(검색 자체 지표 nDCG/MRR 부재) 해소 — IR지표 공식 실구현+실측결과 커밋. 하이브리드+reranker+청킹+IR평가 4요건 충족 |
| 10 | 스트리밍·비동기 | 4 | ×1 | 4 | [D] | `debate.py:130` `EventSourceResponse`, `debate_service.py:122-136` `astream` 노드완료 즉시 yield, `data_node` `asyncio.gather` 병렬fetch. `reports/debate-sse-e11a0291.log` 청크 2~5초 간격 점진 도착([D] 점진성), keepalive ping 포함 | **4→4**: 직전 [D] 미수행(DB 미기동)이던 SSE 청크 점진성을 실토론 로그로 [D] 확정. 그러나 토큰단위 아닌 **노드단위**, bull/bear 토론구조상 직렬(`asyncio.gather` 팬아웃 없음) 구조 한계가 [D]로 재확인되어 5 아닌 4 |

**합계: (5+5+3+5)×2 + (4+5+4+2+5+4)×1 = 36 + 24 = 60 / 70 = 85.7% → A**

---

## 강점 (취업 관점)

1. **재제출 회귀를 실측 증적으로 마감** — 직전 미수확 ×2 항목인 langfuse(항목3)를 import-only가 아닌 `config["callbacks"]` 실주입 와이어업으로, RAGAS(항목8)를 비위조 10건 artifact 커밋으로 닫음. "선언이 아닌 실동작" 기준을 통과.
2. **동적 증적 자가 생성** — 실토론 SSE 타이밍 로그(`debate-sse-e11a0291.log`)를 커밋에 포함시켜 항목1(멀티에이전트 런타임)·항목10(SSE 점진성)을 [S]→[D]로 스스로 끌어올림. 나노초 타임스탬프·실제 환각판정·강제종료가 실관측됨.
3. **MCP 양방향 완성** — FastMCP 서버 신설+공식 mcp SDK 클라이언트 교체로 tools/list round-trip이 실제 6 tool을 반환(감사자 직접 실행). 직전 단방향 클라 한계 해소.
4. **측정 기반 RAG** — nDCG/MRR/p@k 공식을 직접 구현하고 reranker off/on을 실측(nDCG +0.235)해 고도화를 표준 IR 지표로 입증. RAGAS는 임계 캘리브레이션 과정까지 정직하게 문서화.
5. **방어 5계층** — 노드별 retry/fallback + fallback 모델체인 + 그래프 전체 타임아웃(`asyncio.wait_for`, 테스트 통과) + 세션 fail 마킹. 운영 회복탄력성을 테스트로 증명.

---

## 보완 필요 (우선순위 순)

1. **[항목3] 토론 본경로 sLLM 전환 (가중 ×2, 현재 3 → 4 잠재)** — 가장 큰 미수확 가중점. 현재 sLLM(Qwen2.5)은 감성분석 워커 1경로뿐이고 bull/bear/moderator/judge 본토론은 gpt-4o-mini 독점 프런티어. 토론 노드 중 하나 이상을 OpenRouter(≤300B) 또는 로컬 Qwen으로 전환하면 (a) 충족이 강화되어 항목3이 3→4(가중점 +2). 동시에 키 주입 환경에서 langfuse 실 trace를 1회 캡처해 첨부하면 [S]→[D]로 확정.
2. **[항목7] 서빙 기동 증적 (현재 2 → 3+ 잠재)** — Ollama 경로가 코드로는 닫혀 있으나 기본 비활성+compose 미포함이라 [S] 2점. compose에 ollama 서비스를 추가하거나 `ANALYSIS_GENERATION_BACKEND=remote` 기동 로그(`POST /v1/chat/completions 200`)를 artifact로 커밋하면 2→3.
3. **[항목6] MCP 클라이언트 E2E 증적 (현재 4 → 5 잠재)** — DB 연결 환경에서 `mcp_selftest.py` 전체 PASS(call_tool이 실제 종목 데이터 반환) 또는 Notion 발행 페이지 URL 포함 E2E 로그를 커밋하면 클라이언트측도 [D]로 4→5.
4. **[항목10] 토큰단위 스트리밍 (현재 4 → 5 잠재)** — 현재 노드단위 yield. LLM `stream=True` 토큰 패스스루를 SSE에 연결하면 5점 도달.
5. **[항목4] 설계문서 동기화 잔여 2건** — `DebateCreateRequest.decision_agent` 요청바디 필드와 `judge_agent` 노드를 interface-definition·sequence-diagram·component-design에 반영하면 4→5.

---

## 교차검증 노트 (문서 ↔ 코드)

- **신규 서술 과장 검사(코드우선)**: docs-auditor가 이번 추가된 Langfuse/Qwen서빙/MCP서버/Hybrid 서술을 llm-stack·architecture·rag-eval auditor의 코드 실태와 대조 — 4건 모두 과장 없음(문서가 "운영중"으로 허위주장하지 않고 "선택형 백엔드"로 정확히 기술). 항목4 추가 감점 없음.
- **잔존 불일치(경미)**: `decision_agent` 요청바디 미기재, `judge_agent` 노드 설계 누락 2건은 문서가 코드보다 뒤처진 방향(허위주장 아님)이라 항목4 경미 감점에 그쳐 4 유지.
- **비위조 재확인(Supervisor 직접)**: 위조 가능성 높은 항목8·9를 Supervisor가 `ragas-b4f6c3d.json`(소수점 분산 실측)·`ir-005380-b36248a.json`(off/on Δ) 원문 확인. 하드코딩 상수 부재.
- **[D] 증적 진위(Supervisor 직접)**: `git cat-file -e e839d98:reports/debate-sse-e11a0291.log` → 커밋 실존 확인. 나노초 타임스탬프+실 LLM 한국어 summary+keepalive ping = 자가서술 문서가 아닌 실행 산출. 항목1·10 [D] 인정.

---

## 신뢰도·게이트 적용

- 소프트 게이트 미발동: 항목1=5·2=5·8=5 충족, 동적 검증 Full 성공(Reduced 전락 없음).
- 신뢰도 규칙: 항목8·9는 동적 ragas/IR 재실행이 API키 미주입으로 불가해 [S]이나, **5점 근거가 "커밋된 비위조 artifact + 공식 실호출 코드"**이며 환경 한계(키 미주입)는 감점 사유 아님 → 5 유지(상향 보류 대상은 동적 가능 항목의 [S]인데, 본 항목들은 실행 산출 artifact가 증거로 동봉되어 보류 불필요). 항목3은 score 3으로 ≥4 보류 규칙 비대상, 토론 본경로 미전환이 실질 상향 차단.

---

## 직전 리포트(a134b5b) 대비 진전 — SHA 비교

- **저장소 진전 확인**: `git merge-base --is-ancestor a134b5b e839d98` → YES. `git diff --stat a134b5b e839d98`: **61 files, +3957 / -355**, backend 59커밋·frontend 4커밋.
- **항목별 변화**: 1 (4→**5**), 2 (4→**5**), 3 (2→**3**), 4 (4→4), 5 (4→**5**), 6 (3→**4**), 7 (0→**2**), 8 (4→**5**), 9 (4→**5**), 10 (4→4).
- **종합**: **47 → 60 / 70 (67.1% → 85.7%), 등급 C → A (+13점).** 직전 잔존 미수확이던 langfuse(항목3 와이어업)·MCP서버(항목6)·멀티스테이지(항목5)·golden셋/artifact(항목8)·IR지표(항목9)·그래프타임아웃(항목2)이 실코드+동적증적으로 마감. 잔여 상승여지는 토론 본경로 sLLM 전환(항목3)·서빙 기동(항목7)에 집중.

---

## 진위 판정 요약 (자평 강세 항목)

- **langfuse: 진짜 (와이어업).** import-only 아님 — `debate_service.py:309-312`에서 `CallbackHandler()`를 `config["callbacks"]`에 실주입+세션 metadata 연결, Qwen generation span·grounding/hallucination score·flush까지 실행경로에 존재. 단 키 미주입으로 실 trace는 [S]. 점수 3(토론 본경로 sLLM 미전환이 4 차단).
- **MCP 서버: 진짜 (양방향).** `app/mcp_server.py` FastMCP `@mcp.tool()` 6종이 `tools/list`에 실제 응답([D] round-trip PASS), 공식 mcp SDK 클라이언트로 교체. "docs(eval) 자가서술"이 아니라 코드+실행으로 확인. 점수 4(클라 E2E [S]로 5 차단).
- **Ollama(항목7): 절반 진짜 (코드 연결O, 기동 증적X).** `RemoteQwenEvidenceAnalyzer`가 OpenAI호환 base_url로 `chat.completions.create` 실호출하는 분기는 실코드(보고서-only 아님). 그러나 compose에 서빙서비스 없음+기본 비활성+감사자 독립 기동 불가 → [S] 2점.

---

## 재현 정보

- 커밋: backend `e839d98` / frontend `1b25830` (둘 다 git 저장소, `git rev-parse --short HEAD`).
- 직전 대비: `git diff --stat a134b5b e839d98` → 61 files, +3957/-355 (backend 59커밋).
- 동적([D]): `docker build --no-cache` exit 0(이미지 9.61GB, 멀티스테이지 builder/runtime) · `mcp._tool_manager.list_tools()`→6 tool, `mcp_selftest.py` ListToolsRequest PASS · `pytest tests/test_agents/test_debate_timeout.py` 3 passed · `git cat-file -e e839d98:reports/debate-sse-e11a0291.log`(SSE 타이밍 로그 커밋 실존, 청크 2~5s 점진).
- 미검증(동적): 항목3 langfuse 실 trace 생성·sLLM 실호출, 항목7 Ollama 서빙 기동, 항목8 ragas 재실행, 항목6 MCP call_tool/Notion E2E — 모두 외부 API키 미주입 또는 DB 미기동 사유. 정적 증거+커밋된 artifact로 채점, 환경한계 감점 없음.
- 5 auditor 도메인: architecture(1·2·6), llm-stack(3·7), rag-eval(8·9), runtime-infra(5·10), docs(4) — 전부 [S]/[D] 증거 동봉.
- 보존 정책: `reports/BDAI_Pocat_Team2-a134b5b-rerun-2026-06-13.md`·`reports/BDAI_Pocat_Team2-fc3f2b7.md`·`summary.csv`·`rank_change.csv` 미변경. 본 리포트만 신규 파일.
