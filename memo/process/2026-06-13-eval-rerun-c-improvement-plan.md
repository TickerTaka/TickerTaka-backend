# 평가 재검토(C, 47/70) 대응 개선 계획 (2026-06-13)

## 0. 배경 / 목적

이번엔 **실제 평가 Agent**가 소스코드를 직접 검토해 재평가 리포트를 산출했다:
[BDAI_Pocat_Team2-a134b5b-rerun-2026-06-13.md](/home/syt07203/TickerTaka-backend/memo/eval/BDAI_Pocat_Team2-a134b5b-rerun-2026-06-13.md:1)

- 종합: **47 / 70 (67.1%) → 등급 C** (직전 fc3f2b7 = 25/70 F 대비 +22)
- 소프트 게이트: 미발동(항목8 RAGAS 4로 해소)
- 미수확 핵심: **항목3 langfuse(×2)**, **항목7 로컬 서빙**, 그리고 **다수 항목이 [S](정적)에 묶여 5점 보류** — 동적 증적([D]) 부재

이 문서는 리포트의 "보완 필요" 9개를 **가중점 ROI 순 + 의존관계**로 재배열하고, 각 항목을 검증된 `file:line` 기준으로 닫는 구체 작업으로 분해한다.

> 배점: **항목 1·2·3·8 = ×2**, 나머지 ×1 ([evaluation_criteria.md](/home/syt07203/TickerTaka-backend/memo/eval/evaluation_criteria.md:1)).
> 현재 합계: `(4+4+2+4)×2 + (4+4+3+0+4+4)×1 = 28 + 19 = 47`.

---

## 1. 코드 재검증 결과 (리포트 ↔ 실제 소스)

작성 전 리포트 인용을 실제 파일과 1:1 대조함. **리포트는 정확했고**, 계획의 출발점으로 신뢰 가능:

| 리포트 주장 | 실제 확인 | 결과 |
|---|---|---|
| RAGAS 비위조 (`evaluate()` 실호출, df 추출, 상수 0건) | `app/domain/debate_evaluation.py:130-169,225-251` | ✅ 일치 |
| golden set 1건뿐 | `run_ragas_eval.py:36-65` `GOLDEN_CASES` 길이 1 (golden-001) | ✅ 일치 |
| RAGAS artifact 미커밋 | `ragas-*.json` repo에 0건 (`run_ragas_eval.py:178` 루트에 생성만) | ✅ 일치 |
| 회귀 게이트 존재 | `tests/test_agents/test_ragas_regression.py:39-102` 3개 parametrized test | ✅ 일치 |
| langfuse 완전 미구현 | requirements·`app/` 코드 grep 0건 (docs/memo만 언급) | ✅ 일치 |
| 본 토론 OpenAI 독점, sLLM은 평가 1경로만 | `llm_factory.py:38-44` ChatOpenAI(gpt-4o-mini), `debate_evaluation.py:21` sLLM | ✅ 일치 |
| RRF 공식 `1/(rrf_k+index)` | `evidence_retrieval.py:297` | ✅ 일치 |
| reranker 연결·default off | `evidence_retrieval.py:306-335`, `config.py:45` `RAG_RERANKER_ENABLED=false` | ✅ 일치 |
| vLLM/Ollama/MLX 0건 | grep 0건, Qwen은 transformers 직접로드 + 기본 비활성(`config.py:34` `analysis_generation_model=None`) | ✅ 일치 |
| interface 문서가 SSE stream을 "추후 예정"으로 격하 | `memo/design/interface-definition.md:219-221`(§6), 실제 구현 `app/api/debate.py:128` | ✅ 일치 |
| MCP 단방향 클라이언트만 | `app/integrations/notion_mcp.py:45-164` stdio JSON-RPC 자체구현, Python `mcp` SDK 미사용 | ✅ 일치 |

추가 발견:
- `tests/test_agents/test_ragas_regression.py:29`가 `from run_ragas_eval import GOLDEN_CASES`로 **golden set을 단일 소스로 공유** — 케이스를 `run_ragas_eval.py`에서 늘리면 테스트도 같이 강화됨(작업 1회로 항목8 두 축 동시 상승).

---

## 2. ROI 우선순위 요약

| 우선 | 항목(가중) | 현재→목표 | 가중점 Δ | 핵심 작업 | 난이도 |
|---|---|---|---|---|---|
| **P0-E** | (공통 enabler) | [S]→[D] | (1·8·10 잠금해제) | DB 기동 + 키 주입 + E2E 1회 + artifact 생성 | 中 |
| **P0-1** | 항목3 (×2) | 2→4 | **+4** | langfuse 연결 + sLLM 본토론 1노드 | 中 |
| **P0-2** | 항목7 (×1) | 0→3 | **+3** | Ollama/Qwen 로컬 서빙 (항목3과 동시) | 中 |
| **P1-1** | 항목8 (×2) | 4→5 | **+2** | golden 1→10~20 + `reports/ragas-<sha>.json` 커밋 | 低 |
| **P1-2** | 항목2 (×2) | 4→5 | **+2** | 그래프 전체 `asyncio.wait_for` 타임아웃 | 低 |
| **P1-3** | 항목1 (×2) | 4→5 | **+2** | 동적 멀티에이전트 trace([D]) — P0-E·P0-1로 충족 | 低(의존) |
| P2-1 | 항목9 (×1) | 4→5 | +1 | golden relevance + nDCG/MRR/precision@k | 中 |
| P2-2 | 항목10 (×1) | 4→5 | +1 | SSE 청크타이밍 [D] 프로빙 — P0-E로 충족 | 低(의존) |
| P3-1 | 항목5 (×1) | 4→5 | +1 | Dockerfile 멀티스테이지(10GB→2~3GB) | 低 |
| P3-2 | 항목6 (×1) | 3→4 | +1 | Python `mcp` SDK + 서버측 tool 노출 | 中 |
| P3-3 | 항목4 (×1) | 4→5 | +1 | interface-definition.md 동기화 | 極低 |

**현실적 목표**: P0~P1 완수 시 `47 + (4+3+2+2+2) = 60/70 (≈ B)`. P2~P3까지 `+4 = 64/70 (≈ A 진입)`.

> 핵심 통찰: 보완 9개 중 **항목 1·10은 "기능 부재"가 아니라 "동적 증적 부재"**다(리포트 §재현정보: 외부 키 미주입·DB 미기동으로 [D] 보류). 따라서 **P0-E(런타임 증적 확보)** 하나가 항목 1·10의 5점과 항목 8의 artifact를 동시에 푼다 — 비용 대비 효과 최상.

---

## P0-E. 런타임 증적 확보 (cross-cutting enabler)

**문제**: 리포트가 항목 1·3·8·10에서 5점/[D] 상향을 보류한 사유 = "OpenAI/외부 API 키 미주입 또는 DB(postgres) 미기동". 즉 코드는 있는데 **실행 증적이 없어** 점수가 묶임.

**작업**:
1. **DB 기동 경로 정리** — `docker-compose.yml:9-27`의 postgres가 `profiles:["local-db"]` 뒤에 있어 기본 `up`에서 안 뜸. 평가/시연용으로 둘 중 하나:
   - (권장) `docker compose --profile local-db up -d` 절차를 README/실행 메모에 명문화하고, 평가자가 그대로 따라 SSE까지 보게 한다.
   - 또는 평가 편의를 위한 `docker-compose.eval.yml` override로 postgres를 기본 기동 + app `depends_on`에 postgres(service_healthy) 추가.
2. **키 주입** — `.env.local`에 `OPENAI_API_KEY`, `OPENROUTER_API_KEY`(이미 유효 확인됨, [[2026-06-13-eval-track6b-reranker-ab-measurement]] 참조), DART/Naver 키.
3. **E2E 1회 실행 + 캡처**:
   - `POST /api/debates/sessions` → `GET /api/debates/{id}/stream`을 `curl -N`으로 받아 `text/event-stream` **청크 도착 타임스탬프**를 로그로 저장 → 항목10 [D].
   - 같은 실행의 langfuse trace(아래 P0-1) → 항목1 [D].
4. **RAGAS artifact 생성** — `python run_ragas_eval.py` → `ragas-<sha>.json` → `reports/`로 이동·커밋(항목8, P1-1과 연결).

**닫힘 기준**: `memo/results/`에 (a) SSE 청크 타이밍 로그, (b) langfuse trace 스크린샷/URL, (c) `reports/ragas-<sha>.json`이 남는다.

---

## P0-1. 항목3 — langfuse 연결 + sLLM 본토론 진입 (×2, +4)

가장 큰 미수확 가중점. 두 개의 독립 결손(langfuse 0건 / sLLM이 평가경로 한정)을 함께 닫는다.

### 3-A. langfuse tracing
- `requirements.txt`에 핀 추가: `langfuse==<2.x 최신>` ([[feedback_requirements_pinning]] — 반드시 `==` 고정).
- `app/config.py`에 env 추가: `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_HOST`(self-host 시), `LANGFUSE_ENABLED`(default false로 안전).
- 주입 지점은 **단일**: `app/core/llm_factory.py:38-44` `ChatOpenAI(...)` 생성부. `callbacks=[CallbackHandler(...)]`를 조건부로 추가하면 bull/bear/moderator **전 호출이 자동 trace 적재**된다(노드별 코드 수정 불필요).

```python
# llm_factory.py (개념)
callbacks = []
if settings.langfuse_enabled and settings.langfuse_public_key:
    from langfuse.langchain import CallbackHandler  # langfuse v3
    callbacks.append(CallbackHandler())
client = ChatOpenAI(model=model_id, temperature=temperature,
                    api_key=..., max_retries=3, timeout=60, callbacks=callbacks)
```
- 주의: `bull_node.py:49`/`bear_node.py`는 `get_llm(..., cached=False)`로 받아 `create_react_agent`에 넣는다. ChatOpenAI에 callbacks가 박혀 있으면 ReAct 내부 호출도 trace된다. moderator는 `_call()`(`moderator_node.py:27-29`)이 `get_llm("moderator")`를 쓰므로 동일 적용.

**닫힘**: langfuse UI에 1회 토론의 data→moderator_pre→bull→check→bear→...→summary span tree가 보인다(→ 항목1 [D]도 동시 충족).

### 3-B. sLLM을 본 토론 경로로
현재 `config.py:57-60` bull/bear/moderator/fallback 전부 `gpt-4o-mini`(OpenAI). sLLM(≤300B)을 **검증 또는 토론 노드 1개 이상**에 실연결한다.
- 옵션 A(저위험): `bear_model`만 OpenRouter sLLM(예: `openai/gpt-oss-120b:free` 또는 `meta-llama/llama-3.3-70b-instruct`)으로 교체.
- `llm_factory.py:38-44`가 OpenAI 키만 쓰므로, **role별 base_url/key 분기**를 추가해야 한다(모델 id에 `/`가 있으면 OpenRouter로 라우팅):

```python
is_openrouter = "/" in model_id  # e.g. "openai/gpt-oss-120b:free"
client = ChatOpenAI(
    model=model_id, temperature=temperature, max_retries=3, timeout=60,
    api_key=settings.openrouter_api_key if is_openrouter else settings.openai_api_key,
    base_url=settings.openrouter_base_url if is_openrouter else None,
    callbacks=callbacks,
)
```
- `config.py:27-28`의 `openai_api_key` required 가드(`llm_factory.py:27-28`)는 OpenRouter-only 구성도 통과하도록 완화 필요.

**닫힘**: 1회 토론에서 bear 노드가 sLLM(≤300B) 응답을 생성하고 langfuse trace의 모델명이 OpenRouter sLLM으로 찍힌다. 항목3 코멘트의 "본 토론은 독점 프런티어" 사유 해소 → 2→4.

---

## P0-2. 항목7 — 로컬 서빙 신설 (×1, +3)

리포트 권장: macOS이므로 Ollama. 항목3 sLLM 본경로와 **같이** 해결 가능(로컬 서빙 모델을 토론/분석 노드에 연결).

### 옵션 A — Ollama 서비스 (권장, 가장 빠른 0→3)
- `docker-compose.yml`에 서비스 추가:
```yaml
  ollama:
    image: ollama/ollama:latest
    container_name: tickertaka-ollama
    ports: ["11434:11434"]
    volumes: [ollamadata:/root/.ollama]
    healthcheck:
      test: ["CMD", "ollama", "list"]
      interval: 10s
      timeout: 5s
      retries: 5
```
  + `volumes: ollamadata:` 추가, 최초 `docker exec tickertaka-ollama ollama pull qwen2.5:7b`.
- `config.py`에 `OLLAMA_BASE_URL=http://ollama:11434/v1` env. Ollama는 OpenAI 호환 `/v1`을 제공하므로 `llm_factory.py`의 base_url 분기에 ollama 라우트만 추가하면 토론 노드가 그대로 사용.

### 옵션 B — 기존 Qwen 분석 워커 서비스화
- 이미 `app/workers/analysis_worker.py`, `app/domain/evidence_analysis.py`(Qwen 보강 로직, `FILING_QWEN_TITLE_KEYWORDS` 등)가 있으나 `config.py:33-34` `analysis_summary_provider="extractive"` / `analysis_generation_model=None`로 **생성 경로 비활성**.
- `ANALYSIS_GENERATION_MODEL`을 Ollama/vLLM 서빙 모델로 지정 + 워커를 compose 서비스로 분리하면 "로컬 서빙으로 근거 분석 보강"이 실동작.

**닫힘**: compose에 서빙 서비스가 있고, 최소 1개 경로(토론 노드 또는 분석 워커)가 로컬 서빙 모델 응답을 사용. (5점 미만이라도 0→3은 확보)

> 항목3과 항목7은 **동일 base_url 분기 코드**로 만나므로 함께 진행하면 중복 작업이 없다.

---

## P1-1. 항목8 — golden set 확장 + artifact 커밋 (×2, +2)

현재 `run_ragas_eval.py:36-65` golden 1건. 닫는 작업:
1. `GOLDEN_CASES`를 **10~20건**으로 확장. 카테고리 분산(financial/technical/market/macro/synthesis) + 다양한 종목. 각 케이스에 `expected_*_min` 임계 설정.
   - 단일 소스이므로(`test_ragas_regression.py:29` import) 확장 즉시 회귀 테스트도 N배 강화.
2. `reports/` 디렉토리 신설 + `python run_ragas_eval.py` 결과(`ragas-<sha>.json`)를 `reports/`로 저장하도록 `run_ragas_eval.py:178`의 `out_path` 변경, **실행 산출물 1회 커밋**.
   - `.gitignore`가 `*.json`을 막으면 `!reports/ragas-*.json` 예외 추가.
3. (선택) CI에서 `pytest -m "not slow"`로 회귀를 빠르게, golden 풀셋은 수동/야간.

**닫힘**: `run_ragas_eval.py` 10+케이스 실행 PASS + `reports/ragas-<sha>.json` 커밋됨. 리포트의 "golden 1건뿐·artifact 미커밋" 사유 해소 → 4→5.

---

## P1-2. 항목2 — 그래프 전체 타임아웃 (×2, +2)

리포트 잔여 지적: 노드별 try/except·tenacity·fail-open은 충분하나 **그래프 전체 hang 방어(`asyncio.wait_for`) 부재**.
- `app/domain/debate_service.py`의 그래프 실행부(`run_session`의 `ainvoke`, `stream_session`의 `astream` 소비 루프)를 `asyncio.wait_for(..., timeout=settings.debate_total_timeout_seconds)`로 감싼다.
- `config.py`에 `DEBATE_TOTAL_TIMEOUT_SECONDS`(예: 180) 추가.
- 타임아웃 발생 시: 스트림 경로는 이미 `debate.py:256-260` `finally`가 `fail_session_if_running`으로 정리하므로, `TimeoutError`를 잡아 `error` SSE 이벤트 + 세션 failed 처리로 연결.

**닫힘**: 인위적 지연 주입 시 그래프가 무한 대기하지 않고 timeout→failed로 graceful 종료. 4→5.

---

## P1-3 / P2-2. 항목1·10 — 동적 증적으로 [S]→[D] (각 +2 / +1)

**별도 기능 개발 없음.** P0-E + P0-1로 생성되는 산출물을 리포트 근거로 제출:
- 항목1(×2): langfuse span tree = 멀티에이전트 호출 trace([D]) → 4→5(+2).
- 항목10(×1): `curl -N` SSE 청크 도착 타임스탬프 로그(노드별 점진 도착 증명) → 4→5(+1).
- 산출물을 `memo/results/2026-06-1x-eval-dynamic-traces.md`로 정리.

---

## P2-1. 항목9 — 검색 자체 IR 지표 (×1, +1)

리포트·자체 메모([[2026-06-13-eval-track6b-reranker-ab-measurement]])가 진단한 공백: context_precision은 reranker 품질 변별 불가. 표준 IR 지표 도입:
- 종목·쿼리별 **골든 relevance 라벨**(관련 문서 id 집합)을 소규모 구축.
- `scripts/eval_reranker_ab.py`에 `--ir` 모드 추가 또는 신규 `scripts/eval_retrieval_ir.py`: off/on 결과에 대해 **nDCG@k / MRR / precision@k** 계산.
- 골든 relevance는 항목8 golden set과 **공유**하면 작업 절감.

**닫힘**: off/on의 nDCG@4 등 표준 지표 수치가 나오고, reranker on이 +인지 정량 확인. (+이면 항목9 5점 + reranker default 재검토 근거)

---

## P3. 저비용 마무리 (각 +1)

### P3-1. 항목5 — Dockerfile 멀티스테이지
현재 `Dockerfile:1-23` 단일 스테이지(torch/sentence-transformers 포함 ~10GB).
- builder 스테이지에서 `pip install --target`/wheel 빌드 → runtime 스테이지(`python:3.12-slim`)로 site-packages만 복사.
- 빌드툴(`build-essential`)을 runtime에서 제외.
- **닫힘**: 이미지 2~3GB대, `docker build` exit 0 + `/health` 200 유지.

### P3-2. 항목6 — MCP 양방향 + Python SDK
현재 `notion_mcp.py`는 stdio JSON-RPC를 손으로 구현한 **단방향 클라이언트**.
- `mcp` Python SDK(`ClientSession` + `stdio_client`)로 교체 → `tools/list` 자동 협상, 표준 준수.
- (상향용) FastAPI에 **MCP 서버 엔드포인트** 추가해 자사 도구(예: `get_debate_summary`)를 외부에 노출 → 양단 완성.
- **닫힘**: SDK 기반 tools/list 동작 + 서버측 1개 tool 노출 시 3→4.

### P3-3. 항목4 — 인터페이스 정의서 동기화 (極低, 즉시)
- `memo/design/interface-definition.md:219-221`(§6 "추후 확장 예정")의 **SSE stream endpoint**와 `POST /api/debates/sessions`를 §3 정식 구현 목록으로 이동(실제 `debate.py:62,128` 구현 완료).
- **닫힘**: 문서가 코드보다 뒤처진 항목 0 → 4→5. (가장 싼 +1)

---

## 4. 권장 실행 순서

1. **P3-3**(문서 동기화, 5분) — 즉시 +1, 무위험.
2. **P0-E + P0-1 + P0-2**(런타임 증적 + langfuse + sLLM + Ollama) — 한 묶음. base_url 분기 + callbacks 주입 + compose Ollama. 여기서 **항목 3(+4)·7(+3)·1(+2)·10(+1) = +10**이 한 번에 열린다.
3. **P1-1**(golden 확장 + artifact, +2) — 코드 적고 ROI 높음.
4. **P1-2**(타임아웃, +2) — 작은 변경.
5. 여유 시 **P2-1**(IR 지표) → **P3-1**(멀티스테이지) → **P3-2**(MCP SDK).

> 누적 예상: 1~4단계까지만 해도 **47 → 60/70(B)**. P2~P3까지 **64/70(A 진입)**.

## 5. 작업 위생 / 주의

- 브랜치: `main` 직접 커밋 금지 — 작업 브랜치에서 진행 ([[branch_strategy]]).
- 의존성: langfuse/mcp 등 추가 시 `requirements.txt` 전부 `==` 핀 ([[feedback_requirements_pinning]]).
- 인프라: Ollama/langfuse self-host는 졸프=로컬, 운영 진입 시 NCP 이전 ([[infra_stage_policy]]).
- sLLM 본경로 전환 시 latency/품질 회귀를 langfuse로 관측하며 점진 적용(전 노드 일괄 교체 금지).

## 6. 관련 문서

- 재평가 리포트: [BDAI_Pocat_Team2-a134b5b-rerun-2026-06-13.md](/home/syt07203/TickerTaka-backend/memo/eval/BDAI_Pocat_Team2-a134b5b-rerun-2026-06-13.md:1)
- 직전 자체 검증 리포트: [BDAI_Pocat_Team2-3ad1682.md](/home/syt07203/TickerTaka-backend/memo/eval/BDAI_Pocat_Team2-3ad1682.md:1)
- reranker 실측: [[2026-06-13-eval-track6b-reranker-ab-measurement]]
- 직전 후속계획: [2026-06-06-eval-followup-plan.md](/home/syt07203/TickerTaka-backend/memo/process/2026-06-06-eval-followup-plan.md:1)
