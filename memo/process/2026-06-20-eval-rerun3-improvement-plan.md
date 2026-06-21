# 3차 재검토(A, 62/70) 대응 개선 계획 (2026-06-20)

## 0. 배경

3차 공식 재검토 리포트: [BDAI_Pocat_Team2-39b3daf-rerun3-2026-06-20.md](/home/syt07203/TickerTaka-backend/memo/eval/BDAI_Pocat_Team2-39b3daf-rerun3-2026-06-20.md:1)

- **60/70 A → 62/70 (88.6%) A** (+2). 항목4 4→5, 항목7 2→3. **회귀 0건.**
- 항목별: 1(5) 2(5) 3(**3**) 4(5) 5(5) 6(**4**) 7(**3**) 8(5) 9(5) 10(**4**).
- 합계: `(5+5+3+5)×2 + (5+5+4+3+5+4)×1 = 36 + 26 = 62`.
- 남은 미수확: **항목3(×2, 3→4~5)**, **항목7(×1, 3→5)**, **항목6(×1, 4→5)**, **항목10(×1, 4→5)**.

> 배점: 항목 **1·2·3·8 = ×2**, 나머지 ×1 ([evaluation_criteria.md](/home/syt07203/TickerTaka-backend/memo/eval/evaluation_criteria.md:1)).

이 문서는 (1) **항목3이 왜 점수를 못 받았는지**를 코드·리포트로 확정하고 개선책을 정리하고, (2) **항목7 vLLM을 팀원이 만점(5) 받기 위한 주의사항**을 과거 리포트 패턴에서 추출해 정리하고, (3) **전체 점수 개선 계획**을 가중점 ROI 순으로 배열한다.

---

## 1. 항목3 (sLLM + 검증 Agent + langfuse, ×2) — 왜 3점에 묶였나

### 1-1. 우리 전략 (강사 합의) vs 평가자 프레이밍 — 이게 핵심 엇갈림

- **강사 합의** ([[eval-item3-7-scope-sentiment-qwen]]): 항목3(langfuse)·7(vLLM)은 **토론이 아니라 감성분석 Qwen 경로**에서 해결한다. 토론은 프런티어(gpt-4o-mini/claude) 유지.
- **평가자(rerun3) 프레이밍**: 이 합의를 모른 채 "**토론 본경로**를 sLLM으로 전환해야 4점"으로 채점. 그래서 토론이 openai 기본인 것을 근거로 3점 상한.
- 결론: 평가자가 항목3을 **토론 경로 기준**으로 보고 있고, 우리 sLLM(=Qwen 감성분석)은 "감성분석 워커 1경로"로만 인정받았다. **합의 내용이 산출물(설계문서)에 안 보이니 매 평가마다 토론-프레이밍으로 재상한이 걸린다.**

### 1-2. 점수 못 받은 진짜 사유 (리포트 §항목3·§핵심진위판정② 기준)

| 조각 | 상태 | 판정 |
|---|---|---|
| sLLM(≤300B) | `RemoteQwenEvidenceAnalyzer`가 Qwen2.5:3b 사용(`evidence_analysis.py:392-458`) | 인정되나 "감성분석 1경로"로 축소 평가 |
| 검증 Agent | `moderator_check`(`moderator_node.py:158-255`) verdict→조건부 라우팅 실동작 | ✅ 문제 없음 |
| **langfuse** | 코드 주입(`debate_service.py:314-322` CallbackHandler)·deps(`langfuse==4.7.1`) 있으나 **키 미주입 → 실 trace 없음 → [S]** | ❌ **유일·결정적 결손** |

→ **langfuse 실 trace([D]) 부재**가 3점 상한의 핵심. 코드는 다 있는데 **"한 번 돌린 증적"이 없어** [S]에 묶였다(이건 a134b5b·e839d98·39b3daf 3연속 동일 사유).

### 1-3. multi-provider(sLLM/claude-sonnet) 토론 구현은 왜 점수에 도움이 안 됐나

`84fa1b6`의 multi-provider(openrouter sLLM + anthropic claude-sonnet)는 **항목3 점수를 1점도 못 올렸고**, 오히려 평가자가 오판을 피하느라 검증 비용만 늘었다:

- **claude-sonnet = 독점 프런티어 모델 → sLLM(≤300B) 불인정.** 항목3 가점 대상 자체가 아니다.
- **openrouter sLLM = opt-in·1회 실행**(`reports/debate-openrouter-b926963-f88af4e9.log`). 기본값이 여전히 openai(`config.py:82`)라 "본경로 전환"으로 인정 안 됨.
- 즉 토론 multi-provider는 **항목2(폴백/복원력)·이식성** 관점에선 플러스지만(리포트도 항목2 "실질 강화"로 인정), **항목3 sLLM 요건과는 무관**.

> **단, 버리지 말 것.** 사용자가 "sLLM 활용 시 문제를 정리해 기록"한 것은 **"토론은 왜 sLLM을 안 쓰고 프런티어를 쓰는가"의 설계 근거**다. 이건 항목3 점수가 아니라 **설계 의사결정의 정당화**로 활용한다(아래 1-4 ③).

### 1-4. 항목3 개선 작업 (감성분석 Qwen 경로 = 강사 합의 범위)

1. **★ langfuse 실 trace 1회 캡처([S]→[D]) — 최우선·최대 ROI(×2)**
   - `.env.local`에 `LANGFUSE_PUBLIC_KEY`/`LANGFUSE_SECRET_KEY`/`LANGFUSE_TRACING_ENABLED=true` 주입.
   - 감성분석 워커(`analysis_worker.py`)로 뉴스/공시 **1건 이상** 실분석 → langfuse UI에 **Qwen 분석 trace**(입력 제목·본문길이 → 구조화 JSON 출력 → 모델명·latency·게이트) 1건.
   - vLLM(OpenAI 호환)으로 돌리면 `from langfuse.openai import openai` drop-in으로 **토큰/latency 자동 계측** — 항목7 E2E와 **같은 1회 실행**에서 동시 캡처(항목3·7 동시 [D]).
   - **닫힘 증적**: langfuse trace URL/스크린샷 + raw 응답을 `reports/`에 커밋(SHA 연동).
2. **Qwen 감성분석 경로 default-active 보장** — `ANALYSIS_GENERATION_MODEL`이 비어 있으면(`config.py` 기본 None) `qwen_available=False`라 "sLLM 미사용"으로 보일 수 있다. 시연/평가 환경에서 모델 지정으로 **실제 활성** 상태를 명시·증적.
3. **설계문서에 강사 합의(sLLM=감성분석 경로) 명문화** — `component-design.md`/인터페이스 문서에 "토론=프런티어(품질·안정성 근거 기록 링크), sLLM(Qwen2.5:3b)=감성분석 evidence_analysis 경로, 검증 Agent=moderator_check, langfuse=감성분석+토론 양쪽 계측"을 1:1 코드대조 가능하게 기재. **이게 있어야 다음 평가에서 토론-프레이밍 재상한을 막는다.** sLLM-for-debate 품질 문제 기록(`debate-openrouter-*.log` + 비교 메모)을 "토론을 프런티어로 둔 근거"로 링크.

**예상 효과**: langfuse [D] 확보 시 **3→4 확실(+2 가중점)**, 검증 Agent+sLLM+langfuse 3요건이 모두 [D]+문서 명문화되면 **5 도전(+4 가중점)**.

---

## 2. 항목7 (vLLM, ×1) — 팀원이 만점(5) 받기 위한 주의사항

### 2-1. 현황과 방침

- 현재 3점: Ollama를 compose 서비스로 올리고(`docker-compose.yml:136-164`, `profiles:["ollama"]`) 문서·`.md` 증적으로 2→3.
- **방침**: 이 Ollama 점수는 추후 빠질 수 있음(평가자가 [D] 미인정, vLLM은 여전히 주석뿐). **팀원이 실제 vLLM 서빙으로 전환**해 [D]로 닫는다.
- 코드 구조상 전환 비용은 거의 0: `RemoteQwenEvidenceAnalyzer`가 OpenAI 호환 `base_url`만 바꾸면 Ollama→vLLM. **`ANALYSIS_GENERATION_BASE_URL`을 vLLM으로 가리키기만** 하면 코드 변경 없음.

### 2-2. ★ 만점(5) 받기 위한 주의사항 — 과거 리포트 패턴에서 추출

> 3연속 리포트에서 항목6·7·1·10이 [D] 미달로 점수가 묶인 **공통 사유 = "`.md` 서술 증적(학생이 출력을 붙여넣음)이라 독립 재현 불가"**. vLLM은 이 함정을 반드시 피해야 한다.

1. **★ 반드시 "한 번 실제로 돌려서" RAW 터미널 `.log`로 커밋한다 (`.md` 서술 금지).**
   - `vllm serve` 기동 배너 + `GET /v1/models` 응답 + 실제 `chat.completions` 요청·응답을 **터미널 원본 그대로** `reports/vllm-serving-e2e-<sha>.log`에 저장. 손으로 정리한 `.md`는 평가자가 [D]로 인정 안 한다(ollama-*.md가 정확히 이 이유로 [D] 거부됨).
   - 리포트 표현: "`.log` 실행캡처 아닌 `.md` 서술이라 [D] 미달". → **raw log가 만점의 전제.**
2. **★ 서버 기동만이 아니라 E2E 1회를 캡처한다.** "vLLM이 뜸"만으론 부족. **감성분석 워커(`analysis_worker.py`)가 vLLM 엔드포인트를 호출 → `evidence_analysis` row 1건이 실제로 적재**되는 전체 흐름을 한 실행에서 로그로 남긴다(워커 로그 + DB row 확인 쿼리 결과).
3. **★ vLLM을 앱 venv에 설치하지 말 것** (2026-06-13 실측 충돌). vLLM은 torch를 강하게 핀 → 현재 `torch 2.12.0` 대규모 다운그레이드 강제 → `sentence-transformers`/`chromadb`/reranker 회귀 + `==` 핀 정책 정면 충돌. **별도 컨테이너/GPU 머신에서 OpenAI 호환 서버로만** 운용하고, 앱엔 `openai` HTTP 클라이언트만(이미 있음). [[feedback_requirements_pinning]] 보존.
4. **vLLM compose 서비스를 추가하되 GPU profile로 격리** (ollama 서비스 미러링). 또는 standalone 명령을 정확히 문서화:
   ```
   vllm serve Qwen/Qwen2.5-7B-Instruct --port 8000 --max-model-len 8192
   # app/worker: ANALYSIS_GENERATION_BASE_URL=http://<vllm-host>:8000/v1
   #             ANALYSIS_GENERATION_MODEL=Qwen/Qwen2.5-7B-Instruct
   ```
   `docker compose config --services`에서 기본 up에 안 끼도록 `profiles:["vllm"]`(평가자가 격리 직접 검증함).
5. **증적을 SHA에 연동·재현 가능하게.** 평가자는 자가증적 커밋을 `git show`로 실행산출 여부를 직접 검증한다(리포트 §교차검증). 정확한 실행 명령 + raw log + 결과 row를 함께 커밋하고, 파일명에 SHA/타임스탬프.
6. **langfuse drop-in으로 항목3·7 동시 [D].** vLLM은 OpenAI 호환이므로 `from langfuse.openai import openai`로 같은 호출에서 token/latency trace까지 캡처 → **한 번의 E2E 실행으로 항목3 langfuse [D] + 항목7 vLLM [D]를 동시에** 닫는다(1-4 ①과 동일 실행).
7. **환경(GPU or 맥OS) 주의.** vLLM은 CUDA 중심, Apple Silicon은 실험적.
   - (권장) GPU 머신(NCP GPU/실습실/Colab)에서 `vllm serve`만 띄우고 백엔드는 `ANALYSIS_GENERATION_BASE_URL`만 그쪽으로. [[infra_stage_policy]].
   - rubric이 "GPU **or** 맥OS"를 명시하므로 맥OS도 인정되나, 느리더라도 **반드시 1회 raw log로** 남길 것.
8. **Ollama `.md` 증적을 주 증적으로 남기지 말 것.** 빠질 점수다. vLLM raw `.log`를 1차 증적으로, Ollama는 "동일 OpenAI 호환 추상화의 dev 폴백"으로만 문서 위치.

**예상 효과**: vLLM 실서빙 + E2E raw `.log` [D] + compose 서비스 → **항목7 3→5(+2)**. (서버만 [D]면 4, E2E+문서까지면 5)

---

## 3. 나머지 미수확 항목

### 3-1. 항목6 MCP 클라이언트 E2E — 4→5 (×1, +1)
- 사유: `reports/mcp-e2e-2026-06-20.md`가 **자가서술 docs**이고 내부 tools/list 캡처가 **구버전 6개**(현재 `add_watchlist` 추가로 7개)라 불일치 → 클라 round-trip 독립 [D] 부재.
- 작업: `NOTION_TOKEN` 유효 환경에서 Notion round-trip을 `mcp_selftest`급 스크립트로 **raw 캡처**(타임스탬프 포함), tools/list를 **7개로 갱신**해 일관성 확보. 서버측은 이미 selftest [D] 확보됨.
- **진행(2026-06-21)**:
  - ✅ **서버측 raw [D] 확보** — `python -m scripts.mcp_selftest` 실행 캡처를 `reports/mcp-selftest-2026-06-21.log`로 커밋(tools/list **7개** + call_tool `list_available_symbols` **27종목** + PASS, DB 연결 E2E). 직전 평가가 받은 OperationalError보다 강한 증적. `mcp-e2e-2026-06-20.md` §3 stale 6개 → 실 7개 raw로 교체, 요약표 갱신.
  - ✅ **클라이언트측 raw [D] 확보(2026-06-21)** — 라이브 uvicorn + redis/chroma 기동 후 미발행 세션(005380)에 `POST .../publish/notion` 실행: 1차 HTTP 200 **실 페이지 `386bee7e` 생성**, 2차 HTTP 200 **동일 page_id 반환(멱등)**. raw 응답을 `reports/notion-publish-e2e-2026-06-21.log`로 커밋, `mcp-e2e-2026-06-20.md` §1·요약표 갱신.
  - → **항목6 양방향(서버·클라) 모두 raw [D] 확보. 4→5 근거 완비**(신규 SHA 재평가 시 확정).

### 3-2. 항목10 토큰단위 스트리밍 — 4→5 (×1, +1)
- 사유: 현재 **노드단위** yield(`stream_mode` 미전환). 토큰단위 아님으로 5 차단.
- 작업: `astream(..., stream_mode="messages")` 전환 후 LLM 토큰 청크를 SSE에 패스스루.

### 3-3. 항목4 경미(점수 영향 없음) — ✅ 완료(2026-06-21)
- ✅ `component-design.md:61`의 `judge_agent_node` 위치 라인 `:224` → 실제 `:281`로 정정(`moderator_node.py:281` 확인).

---

## 4. 전체 점수 개선 계획 (가중점 ROI 순)

| 우선 | 항목(가중) | 현재→목표 | 가중점 Δ | 핵심 작업 | 담당 |
|---|---|---|---|---|---|
| **P0** | 항목3 (×2) | 3→4(~5) | **+2(~4)** | 감성분석 Qwen에 langfuse **실 trace 1회 raw 캡처**([D]) + 설계문서에 강사합의 명문화 | — |
| **P1** | 항목7 (×1) | 3→5 | **+2** | **실제 vLLM 서빙** E2E **raw `.log`** 1회(§2 주의사항) — 앱 venv 분리, compose profile | 팀원 |
| **P2** | 항목6 (×1) | 4→5 | **+1** | Notion MCP round-trip raw 캡처 + tools/list 7개 갱신 | — |
| **P3** | 항목10 (×1) | 4→5 | **+1** | `stream_mode="messages"` 토큰 패스스루 | — |
| P4 | 항목4 | 5→5 | 0 | 라인오기 정정(품질) | — |

- **P0+P1은 같은 1회 E2E 실행에서 동시 충족** 가능(vLLM OpenAI 호환 + langfuse drop-in). 우선 묶어서 실행.
- 누적 예상: `62 + 항목3(+2~4) + 항목7(+2) + 항목6(+1) + 항목10(+1)` = **68~70/70**. 항목3가 5까지 가면 **만점(70)** 도 사정권.

### 권장 실행 순서
1. **P0+P1 묶음**: GPU/맥OS 환경 1대 확보 → `vllm serve Qwen` → 감성분석 워커가 vLLM 호출하도록 `ANALYSIS_GENERATION_BASE_URL` 설정 → 뉴스/공시 1건 실분석 → **(a) vLLM raw `.log`(항목7) + (b) langfuse trace(항목3)** 를 한 실행에서 캡처·커밋.
2. **P0 문서**: 설계문서에 강사 합의(sLLM=감성분석 경로) + 토론 프런티어 근거(sLLM 품질문제 기록 링크) 명문화.
3. **P2** Notion MCP raw 캡처 + tools/list 갱신.
4. **P3** 토큰 스트리밍.
5. 신규 SHA로 평가 Agent 재실행 → 점수 확정.

---

## 5. 작업 위생 / 주의

- 브랜치: `main` 직접 커밋 금지, 작업 브랜치 → merge ([[branch_strategy]]). 커밋은 사용자가 직접 ([[feedback-user-commits]]).
- 의존성 추가 시 `requirements.txt` 전부 `==` 핀 ([[feedback_requirements_pinning]]). **vLLM은 앱 venv에 넣지 말 것**(§2-3).
- 인프라: vLLM/langfuse는 졸프=로컬/단발 GPU, 운영 진입 시 NCP GPU 상시 서빙 ([[infra_stage_policy]]).
- **증적 원칙(가장 중요)**: 항목6·7·1·10이 반복적으로 [D] 미달한 사유 = **`.md` 서술**. 모든 실행 증적은 **raw `.log`/터미널 원본 + SHA 연동**으로 `reports/`에 커밋한다. "한 번 실제로 돌려서 원본을 남긴다"가 만점의 전제.

## 6. 관련 문서
- 3차 재검토: [BDAI_Pocat_Team2-39b3daf-rerun3-2026-06-20.md](/home/syt07203/TickerTaka-backend/memo/eval/BDAI_Pocat_Team2-39b3daf-rerun3-2026-06-20.md:1)
- 2차 대응계획: [2026-06-20-eval-rerun2-A-improvement-plan.md](/home/syt07203/TickerTaka-backend/memo/plans/2026-06-20-eval-rerun2-A-improvement-plan.md:1)
- C차 대응계획(항목3·7 상세 구현안): [2026-06-13-eval-rerun-c-improvement-plan.md](/home/syt07203/TickerTaka-backend/memo/process/2026-06-13-eval-rerun-c-improvement-plan.md:1)
- Ollama 서빙 결과(빠질 증적): [2026-06-20-eval-track7-ollama-onecommand-e2e.md](/home/syt07203/TickerTaka-backend/memo/results/2026-06-20-eval-track7-ollama-onecommand-e2e.md:1)
- 평가 기준: [evaluation_criteria.md](/home/syt07203/TickerTaka-backend/memo/eval/evaluation_criteria.md:1)
