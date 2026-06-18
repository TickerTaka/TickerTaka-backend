# 구현 결과 — 감성분석 Qwen 원격 서빙(Ollama) + 항목7

- 작성: 2026-06-18 / 브랜치: `uc` / 커밋: `804b51a`
- 대상 평가항목: **항목7(로컬 서빙)** 주, **항목3(langfuse)** 연계
- 상위 계획: [2026-06-13-ollama-qwen-serving-plan.md](/home/syt07203/TickerTaka-backend/memo/plans/2026-06-13-ollama-qwen-serving-plan.md:1) Stage 1~4
- 범위 합의: 토론 경로 불변, 감성분석 Qwen 한 경로에서 항목3·7 해결(강사 합의, [[eval-item3-7-scope-sentiment-qwen]])

---

## 1. 무엇을 했나 (한 줄)

감성분석 sLLM(Qwen) 추론을 인-프로세스 `transformers.generate()` → **OpenAI 호환 원격 서빙(Ollama) 호출**로 전환할 수 있는 백엔드 분기를 추가하고, 로컬 그램(CPU)에서 **워커 E2E까지 실동작 검증**했다. 동일 코드가 vLLM에도 그대로 동작(운영 GPU 확보 시 `base_url`만 교체).

## 1-1. 왜 Ollama인가 (선택 근거)

서빙 엔진 후보는 ① transformers 직접 로드(현행) ② vLLM ③ Ollama 였다. Ollama로 정한 이유:

- **vLLM은 무료 GPU 경로가 막혔다.** 무료 Colab T4(sm_75)에 vLLM 0.23.0을 올리려다 실패 — torch/CUDA(cu128↔cu13) 불일치 + T4의 FlashInfer 미지원으로 추론 크래시 + `VLLM_ATTENTION_BACKEND` env 무시. RunPod 등 임대 GPU는 유료라 졸프 단계엔 과함.
- **transformers 직접 로드는 항목7(로컬 서빙)을 못 채운다.** "서빙 엔진"이 없는 인-프로세스 방식이고, CPU float32 추론도 느리다(기존 reranker가 CPU에서 못 켜진 것과 같은 부류).
- **Ollama는 위 둘의 약점을 동시에 해소한다.** llama.cpp 기반이라 **CUDA 버전과 무관**하게 그램(CPU)·Mac·서버에서 바로 동작하고, **OpenAI 호환**이라 앱 코드가 vLLM과 동일하다. 재평가 리포트도 항목7 grep에 `ollama` 포함 + 보완 #2에서 직접 권장 → 항목7 근거로 인정.

## 1-2. transformers 직접 로드 대비 이점

| | transformers 직접 로드 | Ollama (이번 도입) |
|---|---|---|
| 모델 위치 | 앱/워커 프로세스 **내부** 상주 | **별도 데몬**에 상주 |
| 앱 재시작 | 매번 모델 재로딩(수~수십 초) | 데몬 유지 → 재로딩 0 |
| 워커 N개 | 프로세스마다 모델 1벌(메모리 N배) | 데몬 1벌 공유 |
| 메모리/속도 | float32 CPU 느림 | **GGUF q4** → 메모리 1/3~1/4, CPU도 실용(그램 실측 10~22초/건) |
| CUDA 의존 | torch 빌드와 버전 일치 필요 | **무관**(vLLM Colab 실패의 정반대) |
| 인터페이스 | `model.generate` 파이썬 결합 | **OpenAI 호환 HTTP** → 미래 vLLM 전환 무비용(URL만) |
| 평가 항목7 | 미충족(서빙 엔진 부재) | **충족** + langfuse 자동계측 호환 |

> 트레이드오프: 별도 데몬 운영 + 네트워크 홉 + GGUF 양자화로 인한 미세 품질 저하. 금융 수치는 프롬프트(본문 외 숫자 금지)+`_parse_json`+consistency guard로 방어하고, 필요 시 `qwen2.5:7b` 상향. [[infra_stage_policy]] 졸프(로컬 Ollama)→운영(NCP GPU vLLM) 이전 경로와 정합.

## 2. 구현 내용 (file:line)

| 변경 | 위치 | 내용 |
|---|---|---|
| 서빙 백엔드 config | `app/config.py:35-40` | `ANALYSIS_GENERATION_BACKEND`(transformers\|remote) / `_BASE_URL` / `_API_KEY`. 기본 transformers(무회귀) |
| 원격 분석기 | `app/domain/evidence_analysis.py` `RemoteQwenEvidenceAnalyzer` | `LocalQwenEvidenceAnalyzer`와 **동일 `analyze()` 시그니처/출력 계약**. 프롬프트(`_build_prompt`)·파서(`_parse_json`) 재사용, 추론만 `chat.completions.create`(`response_format=json_object`, 거부 시 무옵션 1회 재시도) |
| langfuse 게이트 | 같은 클래스 `_get_client()` | `get_langfuse()`가 활성일 때만 `langfuse.openai` 드롭인(자동 generation trace), 비활성이면 표준 `openai`로 폴백 → "client disabled" 경고 회피 |
| 주입 분기 | `EvidenceAnalysisService.__init__` | `backend=="remote" and base_url` → Remote, 아니면 Local. 기본 경로 byte-동일 |
| 의존성 핀 | `requirements.txt` | `langfuse>=3.0.0` → `langfuse==4.7.1`(핀 규칙 정정), `openai==1.109.1` 명시(직접 사용) |
| 예시 env | `.env.example` | BACKEND/_BASE_URL/_API_KEY + Ollama 사용법 주석 |

> 설계상 핵심: Ollama·vLLM 둘 다 OpenAI 호환 → **분석기 1개로 양쪽 커버**, 엔진 교체는 `base_url`만. [[infra_stage_policy]] 졸프(로컬)→운영(NCP GPU) 이전과 정합.

## 3. 검증 (실측)

환경: 그램 노트북 — 16GB RAM, i7-1195G7(4C/8T), GPU 없음(Iris Xe) → **CPU 추론**. 서버: WSL Ubuntu 내 `ollama serve` + `qwen2.5:3b`(q4).

### 3-1. 분석기 스모크 (큐 무관, 경로 검증)
- 공시 텍스트 입력 → 구조화 JSON 정상 산출. **10.0 ~ 22.4초/건**(CPU, 입력 길이 의존).
- 출력 예: `event_type=단일판매공급계약, sentiment=positive, impact_score=2, key_points=[배터리 공급계약, 계약금액 2조원]` — 본문 grounding 정확.

### 3-2. 워커 E2E (실제 운영 경로)
- done 공시 1건(005380)을 `enqueue()`로 pending 재설정 → `run_once()` 1배치.
- 로그: `POST http://localhost:11434/v1/chat/completions "HTTP/1.1 200 OK"` → **워커가 Ollama 실호출**.
- 결과: `processed=1`, job `done`(attempts=1, error=None). `evidence_analysis` 갱신:
  ```
  event_type=잠정실적, sentiment=mixed, impact_score=0, confidence=0.774
  summary    = 영업(잠정)실적(공정공시) — 매출 감소 · 영업이익 감소 · 순이익 감소
  key_points = ['매출 감소','영업이익 감소','순이익 감소']   (정확)
  ```
- 흐름 검증: 워커 claim → DART 원문 fetch → `RemoteQwenEvidenceAnalyzer` → Ollama → 하네스(grounding/noise/consistency) → 저장 → mark_done.

### 3-3. 무회귀
- 기본 `backend=transformers` → 기존 `LocalQwenEvidenceAnalyzer` 경로 그대로(분기 else). 테스트는 model 미설정 시 분석기 미생성.
- venv 핀 일치 확인: `openai 1.109.1`, `langfuse 4.7.1`.

## 4. 발견 → 수정한 별건 (기존 하네스 결함, 2026-06-18 해결)

- **발견**: 위 005380 결과의 `risks` 필드에 표 헤더 덤프 누출(`실적내용: (-) | 단위 : 백만원, %: 증감율(%)...`).
- **원인**: `_is_noise_fragment`는 그 조각을 정확히 노이즈로 판정하나, **추출형 baseline(`ExtractiveSummaryBuilder.build`)의 key_points/risks는 `_filter_noise`를 안 거쳤다.** Qwen 후보가 전부 노이즈로 걸러져 비면(`if cand_risks:` False) 필터 안 된 baseline이 그대로 저장됨. transformers 경로에서도 동일하게 발생하는 선재 결함(Ollama 무관).
- **수정**: baseline 생성 직후 `key_points = _filter_noise(key_points)` / `risks = _filter_noise(risks)` 적용(`_analyze_text_impl`). 회귀 테스트 `test_baseline_risks_are_noise_filtered` 추가 — 표덤프 제거 + 깨끗한 서술형 리스크 보존 검증.

## 5. 설정 요약 (로컬)

`.env.local`(`.env`보다 우선)에서 예전 vllm/ngrok 잔재 제거 후:
```env
ANALYSIS_GENERATION_BACKEND=remote
ANALYSIS_GENERATION_BASE_URL=http://localhost:11434/v1
ANALYSIS_GENERATION_MODEL=qwen2.5:3b
ANALYSIS_GENERATION_API_KEY=EMPTY
```
워커 상시 기동: `python -m app.workers.analysis_worker`.

## 6. 남은 것 (코드 외)

- [x] `docs/evidence-llm-analysis-implementation-plan.md:553`("Ollama 의도적 배제") 정정 (2026-06-18)
- [x] `risks` 노이즈 필터 (§4, baseline에도 `_filter_noise` 적용 + 테스트)
- [ ] watchlist `/feed` 프론트에서 감성 필드 실표시 확인(API는 이미 노출)
- [ ] langfuse 키+`LANGFUSE_TRACING_ENABLED=True`로 trace 1건 실확인(선택)
- [ ] 강사에게 Ollama 항목7 인정 1줄 컨펌
- [ ] 모델 크기 최종 결정: langfuse `consistency=conflict` 비율 보고 1.5B/3B/7B (팀원 design 규율)
- [ ] **항목7 점수 확정**: 신규 SHA로 평가 Agent 재실행 → 재평가 리포트 갱신(증적 체인)

## 7. 관련 문서

- 구현 계획: [2026-06-13-ollama-qwen-serving-plan.md](/home/syt07203/TickerTaka-backend/memo/plans/2026-06-13-ollama-qwen-serving-plan.md:1)
- langfuse(1단계) 설계/구현: `docs/langfuse-sllm-tracing-design.md`, `docs/langfuse-tracing-implementation.md`
- 재평가 리포트(항목3 갱신 반영, 항목7 점수는 재평가 대기): [BDAI_Pocat_Team2-a134b5b-rerun-2026-06-13.md](/home/syt07203/TickerTaka-backend/memo/eval/BDAI_Pocat_Team2-a134b5b-rerun-2026-06-13.md:1)
- 상위 개선 계획: [2026-06-13-eval-rerun-c-improvement-plan.md](/home/syt07203/TickerTaka-backend/memo/process/2026-06-13-eval-rerun-c-improvement-plan.md:1) P0-2
