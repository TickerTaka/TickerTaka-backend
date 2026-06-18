# 구현 결과 — 감성분석 Qwen 원격 서빙(Ollama) + 항목7

- 작성: 2026-06-18 / 브랜치: `uc` / 커밋: `804b51a`
- 대상 평가항목: **항목7(로컬 서빙)** 주, **항목3(langfuse)** 연계
- 상위 계획: [2026-06-13-ollama-qwen-serving-plan.md](/home/syt07203/TickerTaka-backend/memo/plans/2026-06-13-ollama-qwen-serving-plan.md:1) Stage 1~4
- 범위 합의: 토론 경로 불변, 감성분석 Qwen 한 경로에서 항목3·7 해결(강사 합의, [[eval-item3-7-scope-sentiment-qwen]])

---

## 1. 무엇을 했나 (한 줄)

감성분석 sLLM(Qwen) 추론을 인-프로세스 `transformers.generate()` → **OpenAI 호환 원격 서빙(Ollama) 호출**로 전환할 수 있는 백엔드 분기를 추가하고, 로컬 그램(CPU)에서 **워커 E2E까지 실동작 검증**했다. 동일 코드가 vLLM에도 그대로 동작(운영 GPU 확보 시 `base_url`만 교체).

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

## 4. 발견된 별건 (Ollama 무관, 기존 하네스 결함)

- 위 005380 결과의 `risks` 필드에 **표 헤더 덤프**가 누출(`실적내용: (-) | 단위 : 백만원, %: 증감율(%)...`).
- 원인: `_filter_noise`/grounding이 summary·key_points엔 강하게 걸리나 **risks엔 동일 수준으로 적용되지 않음**. transformers 경로에서도 동일하게 발생하는 선재 결함.
- 조치: 별도 트랙(`_filter_noise`를 risks에도 적용 검토). langfuse 트레이스로 발생 빈도 진단 후 처리 권장. **본 항목7 구현의 블로커 아님.**

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

- [ ] `docs/evidence-llm-analysis-implementation-plan.md:553-574`("Ollama 의도적 배제") 정정 메모
- [ ] watchlist `/feed` 프론트에서 감성 필드 실표시 확인(API는 이미 노출)
- [ ] langfuse 키+`LANGFUSE_TRACING_ENABLED=True`로 trace 1건 실확인(선택)
- [ ] 강사에게 Ollama 항목7 인정 1줄 컨펌
- [ ] 모델 크기 최종 결정: langfuse `consistency=conflict` 비율 보고 1.5B/3B/7B (팀원 design 규율)
- [ ] **항목7 점수 확정**: 신규 SHA로 평가 Agent 재실행 → 재평가 리포트 갱신(증적 체인)
- [ ] (별건) `risks` 노이즈 필터 후속

## 7. 관련 문서

- 구현 계획: [2026-06-13-ollama-qwen-serving-plan.md](/home/syt07203/TickerTaka-backend/memo/plans/2026-06-13-ollama-qwen-serving-plan.md:1)
- langfuse(1단계) 설계/구현: `docs/langfuse-sllm-tracing-design.md`, `docs/langfuse-tracing-implementation.md`
- 재평가 리포트(항목3 갱신 반영, 항목7 점수는 재평가 대기): [BDAI_Pocat_Team2-a134b5b-rerun-2026-06-13.md](/home/syt07203/TickerTaka-backend/memo/eval/BDAI_Pocat_Team2-a134b5b-rerun-2026-06-13.md:1)
- 상위 개선 계획: [2026-06-13-eval-rerun-c-improvement-plan.md](/home/syt07203/TickerTaka-backend/memo/process/2026-06-13-eval-rerun-c-improvement-plan.md:1) P0-2
