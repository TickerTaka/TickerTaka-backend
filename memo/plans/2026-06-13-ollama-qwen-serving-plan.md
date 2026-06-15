# Ollama(Qwen) 로컬 서빙 도입 계획 (2026-06-13)

## 0. 목적 / 범위

평가 항목 **7(로컬 서빙)** + 항목 **3(sLLM + langfuse)** 을 **감성분석 Qwen 경로 한 곳**에서 닫는다.
- 토론 Agent는 건드리지 않는다(강사 합의 — [[eval-item3-7-scope-sentiment-qwen]]).
- 상위 계획: [2026-06-13-eval-rerun-c-improvement-plan.md](/home/syt07203/TickerTaka-backend/memo/process/2026-06-13-eval-rerun-c-improvement-plan.md:1) 의 P0-1/P0-2를 **Ollama 기반으로 확정**한 실행 문서.

### 왜 vLLM이 아니라 Ollama인가 (의사결정 근거)
- vLLM을 무료 Colab **T4(sm_75)** 에서 띄우려다 **세 단계 충돌**을 확인: ① torch cu128↔vLLM cu13 불일치, ② T4가 FlashInfer 미지원이라 추론 시 EngineCore 크래시, ③ vLLM 0.23.0이 `VLLM_ATTENTION_BACKEND` env를 무시(계속 FLASHINFER 선택). → **무료 T4 + 현행 vLLM은 추론 불가**로 결론.
- Ollama(llama.cpp/GGUF)는 **CUDA/torch 버전에 무관**하게 T4·CPU·Mac에서 바로 동작하고 **OpenAI 호환 API**를 제공 → 앱 연동 코드가 vLLM과 동일.
- 핵심: Ollama든 vLLM이든 **둘 다 OpenAI 호환**이므로, 앱은 단일 `base_url` 분기로 양쪽을 모두 지원한다(아래 §3). 즉 이 계획은 "Ollama 우선, 추후 GPU 생기면 vLLM URL로 교체"까지 포함한다.

---

## 1. 평가 정합 (Ollama가 항목7을 충족하는 근거)

- **기준 원문**([evaluation_criteria.md](/home/syt07203/TickerTaka-backend/memo/eval/evaluation_criteria.md:8)): `7) vLLM 사용(GPU or 맥OS)` — 글자상 "vLLM".
- **실제 채점 리포트**([a134b5b-rerun](/home/syt07203/TickerTaka-backend/memo/eval/BDAI_Pocat_Team2-a134b5b-rerun-2026-06-13.md:22))는 항목7을 **Ollama/MLX/llama.cpp 포함**으로 판정:
  - 증거 grep 대상: `vllm/ollama/mlx/llama.cpp` (= 이 중 무엇이든 있으면 인정)
  - 코멘트: "macOS 대안(Ollama/MLX)도 미적용"
  - 보완 #2: "**macOS이므로 Ollama(qwen2.5:7b) 서비스**를… OpenAI 호환 분기" — 평가자가 직접 권장.
- **남은 리스크 2개** (반드시 처리):
  1. 문구 리스크 → 강사에게 "Ollama 로컬 서빙으로 항목7 인정?" 1줄 확인.
  2. 자체 문서 모순 → `docs/evidence-llm-analysis-implementation-plan.md:553-574`가 과거 "Ollama 없이 transformers 직접"을 **의도적으로 선택**했었음. 그 사유는 "앱 내장 분석이라 서버 분리 이점 없음"이었는데, 지금은 **평가 요건(서빙 스택)** 이라는 새 사유로 번복하는 것이므로 그 문서에 정정 메모 1줄 추가(§4 Stage 6).

---

## 2. 현황 (코드 확인됨)

감성분석 Qwen은 이미 구현돼 있으나 **transformers 직접 로드 + 기본 비활성**:
- `app/domain/evidence_analysis.py:253-362` `LocalQwenEvidenceAnalyzer`
  - `_get_model()`(`:293-305`): `transformers.AutoModelForCausalLM.from_pretrained` (mps/cpu)
  - `analyze(title, text, *, kind, max_new_tokens=768) -> dict|None`(`:266-291`): chat template → `model.generate` → `_parse_json`
  - `_build_prompt`(`:307-337`, @staticmethod), `_parse_json`(`:339-362`, @staticmethod) — **재사용 가능**
- 주입부 `EvidenceAnalysisService`(`:538-546`): `analysis_generation_model`이 있으면 `LocalQwenEvidenceAnalyzer` 생성
- 게이팅/소비: `qwen_available`(`:554-557`), enrich 경로(`:603-620`), `_analyze`(`:654-707`)
- 비동기 워커: `app/workers/analysis_worker.py` (프로세스당 1회 로드)
- 기본 비활성: `app/config.py:34` `analysis_generation_model=None`
- **소비처(프론트)**: `app/api/watchlist.py:104-208` `GET /api/watchlists/{user_id}/feed` 가 `sentiment/impact_score/...`를 내려줌 → Ollama 보강 결과가 여기로 흐른다.

---

## 3. 아키텍처

```
[앱 / analysis_worker] --OpenAI HTTP--> [Ollama :11434/v1  또는  vLLM :8000/v1] -- Qwen2.5-3B
        (openai 클라이언트만 추가, torch/transformers 무변경 → 의존성 충돌 0)
```

핵심 설계: **Ollama와 vLLM 모두 OpenAI 호환**이므로 analyzer는 하나(`RemoteQwenEvidenceAnalyzer`)만 만들고 `base_url`만 바꾼다.
- 졸프/시연: 로컬 `ollama serve`(Mac/WSL) 또는 Colab Ollama + 터널.
- 운영: NCP GPU에 Ollama 또는 vLLM 상시 서빙([[infra_stage_policy]]) → `base_url`만 교체.

`analyze()` 인터페이스(입력 프롬프트·출력 JSON 파싱)는 `LocalQwenEvidenceAnalyzer`와 **동일**하게 유지 → 다운스트림 게이팅/consistency(`:654-707`) **무수정**.

---

## 4. 구현 단계

### Stage 0 — Ollama 서버 띄우기 (택1)
**로컬(Mac/WSL, 권장):**
```bash
# 설치
curl -fsSL https://ollama.com/install.sh | sh
ollama serve &          # :11434, OpenAI 호환 /v1 제공
ollama pull qwen2.5:3b  # (여유되면 qwen2.5:7b)
```
**Colab(무료 GPU 활용 시):** 위와 동일 + `pyngrok`로 `ngrok.connect("127.0.0.1:11434")` → 공개 URL을 `base_url`로.

검증:
```bash
curl -s http://localhost:11434/v1/chat/completions \
  -d '{"model":"qwen2.5:3b","messages":[{"role":"user","content":"한 문장 자기소개"}]}'
```

### Stage 1 — config 추가 (`app/config.py`)
```python
# 생성형 분석 백엔드: transformers(로컬 직접) | remote(OpenAI 호환: Ollama/vLLM)
analysis_generation_backend: str = Field(default="transformers", alias="ANALYSIS_GENERATION_BACKEND")
analysis_generation_base_url: str = Field(default="http://localhost:11434/v1", alias="ANALYSIS_GENERATION_BASE_URL")
analysis_generation_api_key: str = Field(default="EMPTY", alias="ANALYSIS_GENERATION_API_KEY")
# Ollama면 "qwen2.5:3b", vLLM이면 "Qwen/Qwen2.5-3B-Instruct"
```
- 기존 `analysis_generation_model`(`:34`)은 모델명으로 그대로 사용.
- `==` 핀 정책과 무관(코드 변경) — openai 클라이언트는 이미 langchain-openai로 사실상 존재.

### Stage 2 — RemoteQwenEvidenceAnalyzer 추가 (`app/domain/evidence_analysis.py`)
`LocalQwenEvidenceAnalyzer`와 같은 시그니처. 프롬프트/파싱은 기존 static 재사용.
```python
class RemoteQwenEvidenceAnalyzer:
    """Ollama/vLLM 등 OpenAI 호환 서버로 구조화 분석을 받는다(앱 프로세스에 모델 로드 안 함)."""
    def __init__(self, model_name: str, base_url: str, api_key: str = "EMPTY") -> None:
        from openai import OpenAI
        self.model_name = model_name
        self._client = OpenAI(base_url=base_url, api_key=api_key)

    def analyze(self, title: str, text: str, *, kind: str, max_new_tokens: int = 768) -> dict | None:
        try:
            resp = self._client.chat.completions.create(
                model=self.model_name,
                messages=[
                    {"role": "system", "content": "너는 사실 기반 한국 금융 공시/뉴스 분석기다. JSON 한 개만 출력한다."},
                    {"role": "user", "content": LocalQwenEvidenceAnalyzer._build_prompt(title, text, kind)},
                ],
                temperature=0,
                max_tokens=max_new_tokens,
                # Ollama/vLLM 모두 지원: JSON 강제로 파싱 실패율↓
                response_format={"type": "json_object"},
            )
            return LocalQwenEvidenceAnalyzer._parse_json(resp.choices[0].message.content)
        except Exception as exc:
            logger.info("remote Qwen analysis unavailable: %s", exc)
            return None
```
> `response_format=json_object`: Ollama·vLLM 둘 다 지원. 미지원 서버면 빼고 `_parse_json`의 salvage 로직에 의존.

### Stage 3 — 주입 분기 (`evidence_analysis.py:545-546`)
```python
if self.analyzer is None and self.settings.analysis_generation_model:
    if self.settings.analysis_generation_backend == "remote":
        self.analyzer = RemoteQwenEvidenceAnalyzer(
            self.settings.analysis_generation_model,
            self.settings.analysis_generation_base_url,
            self.settings.analysis_generation_api_key,
        )
    else:
        self.analyzer = LocalQwenEvidenceAnalyzer(self.settings.analysis_generation_model)
```
- `qwen_available`(`:554-557`) 조건은 그대로(model 설정 시 True).

### Stage 4 — langfuse trace (항목3)
`analyze()` 호출을 langfuse로 계측. transformers/remote 공통이 되도록 `_analyze`(`:654-707`)의 analyzer 호출 지점을 감싸거나, remote면 **langfuse OpenAI drop-in** 사용:
```python
# requirements.txt: langfuse==<핀>
# .env: LANGFUSE_PUBLIC_KEY / SECRET_KEY / HOST / LANGFUSE_ENABLED=true
# 방법A(공통): analyze 호출부를 @observe(name="qwen-evidence-analysis")로 감싸 입력(title/kind/len)·출력(sentiment/impact/event_type)·model·latency 기록
# 방법B(remote 전용): from langfuse.openai import openai  → RemoteQwenEvidenceAnalyzer의 OpenAI 클라이언트가 자동 trace(토큰/latency 포함)
```
- `LANGFUSE_ENABLED=false`면 no-op(운영 부담 0).
- **닫힘**: langfuse UI에 공시/뉴스 1건의 Qwen 분석 trace가 보임 → 항목3 langfuse 결손 해소.

### Stage 5 — (선택) docker-compose Ollama 서비스
운영/로컬 통합 시연용. 단 졸프 시연이 Colab/로컬 `ollama serve`로 충분하면 생략 가능.
```yaml
  ollama:
    image: ollama/ollama:latest
    container_name: tickertaka-ollama
    ports: ["11434:11434"]
    volumes: [ollamadata:/root/.ollama]
    healthcheck:
      test: ["CMD","ollama","list"]
      interval: 10s
      timeout: 5s
      retries: 5
# app.environment: ANALYSIS_GENERATION_BASE_URL=http://ollama:11434/v1
# volumes: ollamadata:
```
초기 1회: `docker exec tickertaka-ollama ollama pull qwen2.5:3b`.

### Stage 6 — 문서/설정 정리
- `.env.example`에 `ANALYSIS_GENERATION_BACKEND/_BASE_URL/_MODEL/_API_KEY`, `LANGFUSE_*` 추가.
- `docs/evidence-llm-analysis-implementation-plan.md:553-574`에 정정 메모: "초기엔 앱 내장 transformers를 택했으나, **평가 항목7(로컬 서빙 스택) 대응**으로 OpenAI 호환 원격 서빙(Ollama/vLLM) 백엔드를 추가 — backend 플래그로 선택."

### Stage 7 — 검증 (E2E)
1. `ANALYSIS_GENERATION_BACKEND=remote`, `_MODEL=qwen2.5:3b`, `_BASE_URL=<ollama>/v1`, `analysis_async_enabled=true`로 워커 기동.
2. 공시/뉴스 1건 인덱싱 → `analysis_jobs` 큐잉 → 워커가 Ollama로 구조화 분석.
3. `evidence_analysis` 행에 `sentiment/impact_score/summary/key_points/...` 저장 확인.
4. `GET /api/watchlists/{user_id}/feed`에 해당 항목의 감성 필드가 채워져 내려오는지 확인(프론트 연동 지점).
5. langfuse UI에 그 분석 trace 1건 확인(항목3).

---

## 5. config / env 요약
```env
ANALYSIS_ENABLED=true
ANALYSIS_ASYNC_ENABLED=true
ANALYSIS_GENERATION_BACKEND=remote          # transformers | remote
ANALYSIS_GENERATION_BASE_URL=http://localhost:11434/v1   # Ollama. vLLM/ngrok면 그 URL
ANALYSIS_GENERATION_MODEL=qwen2.5:3b         # vLLM이면 Qwen/Qwen2.5-3B-Instruct
ANALYSIS_GENERATION_API_KEY=EMPTY
LANGFUSE_ENABLED=true
LANGFUSE_PUBLIC_KEY=...
LANGFUSE_SECRET_KEY=...
LANGFUSE_HOST=...
```

## 6. 닫힘 기준
- [ ] Ollama가 Qwen2.5-3B를 `/v1`로 서빙, 추론 1건 성공
- [ ] `ANALYSIS_GENERATION_BACKEND=remote`로 워커가 Ollama 호출 → `evidence_analysis` 저장
- [ ] watchlist feed에 감성 필드 노출(프론트 확인)
- [ ] langfuse에 Qwen 분석 trace 1건
- [ ] `docs:553` 정정 + `.env.example` 갱신
- [ ] (확인) 강사에게 Ollama 인정 여부 1줄 컨펌

## 7. 리스크 / 트레이드오프
- **품질**: Ollama는 GGUF 양자화(q4 등) → fp16 대비 미세한 품질 저하. 금융 수치 grounding은 프롬프트(본문 외 숫자 금지)+`_parse_json`+기존 consistency guard로 방어. 필요 시 `qwen2.5:7b`로 상향.
- **모델명 규약**: Ollama=`qwen2.5:3b`, vLLM/HF=`Qwen/Qwen2.5-3B-Instruct` — backend별로 `ANALYSIS_GENERATION_MODEL`만 다르게.
- **세션 휘발성**: Colab/ngrok 서빙은 세션마다 URL 변동 → `BASE_URL` 갱신 필요. 상시성은 NCP GPU.
- **response_format 미지원 서버**: 일부 버전은 `json_object` 미지원 → 제거하고 salvage 파싱에 의존.

## 8. 관련 문서
- 상위 계획: [2026-06-13-eval-rerun-c-improvement-plan.md](/home/syt07203/TickerTaka-backend/memo/process/2026-06-13-eval-rerun-c-improvement-plan.md:1) (P0-1/P0-2)
- 재평가 리포트: [BDAI_Pocat_Team2-a134b5b-rerun-2026-06-13.md](/home/syt07203/TickerTaka-backend/memo/eval/BDAI_Pocat_Team2-a134b5b-rerun-2026-06-13.md:1)
- 과거 설계(정정 대상): `docs/evidence-llm-analysis-implementation-plan.md:553-574`
- 범위 합의: [[eval-item3-7-scope-sentiment-qwen]] / [[infra_stage_policy]] / [[feedback_requirements_pinning]]
