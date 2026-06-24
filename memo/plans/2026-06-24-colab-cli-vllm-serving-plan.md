# Colab CLI로 무료 T4 GPU 할당 → vLLM(Qwen) 감성분석 서빙 계획 (2026-06-24)

## 0. 목적 / 범위

평가 항목 **7(vLLM 사용, GPU)** 을 **"vLLM 글자 그대로 + 실제 GPU 추론"** 으로 닫기 위한 실행 문서.
- 대상: 감성분석 Qwen 경로 한 곳(토론 Agent 무관 — 강사 합의 [[eval-item3-7-scope-sentiment-qwen]]).
- 앱 연동은 이미 끝나 있다: `RemoteQwenEvidenceAnalyzer`(OpenAI 호환)가 `ANALYSIS_GENERATION_BASE_URL`만 바꾸면 Ollama든 vLLM이든 붙는다(상위 [Ollama 서빙 계획](/home/syt07203/TickerTaka-backend/memo/plans/2026-06-13-ollama-qwen-serving-plan.md:88) §2·§3). **즉 이 계획은 "서버 쪽을 Ollama → vLLM(Colab T4)로 교체"만 다룬다.**
- 전제: **무료 Colab 플랜 + T4**. "테스트 1~2회 후 시연" 시나리오.

### 이 계획의 위치 (Ollama plan과의 관계)
- Ollama plan은 "상시/저비용 폴백"으로 유지한다. 이 vLLM 경로는 **항목7 정합을 글자 그대로 + GPU 추론으로 강화**하는 트랙.
- 앱은 `base_url` 한 줄 분기라 **둘 다 동시 보유**가 가능하다. 시연 중 vLLM이 흔들리면 즉시 Ollama URL로 되돌린다(§6 폴백).

---

## 1. ⚠️ 가장 중요: 무료 T4 + vLLM 과거 실패 3건 (반드시 우회)

[Ollama plan §0](/home/syt07203/TickerTaka-backend/memo/plans/2026-06-13-ollama-qwen-serving-plan.md:9)에 **이전에 T4에서 vLLM 추론이 깨진 원인**이 기록돼 있다. Colab CLI를 써도 GPU는 동일한 **T4(sm_75)** 라 이 제약은 그대로다. 따라서 아래 우회를 적용하지 않으면 또 깨진다.

| 과거 실패 | 원인 | 본 계획의 우회 |
|---|---|---|
| ① torch ↔ vLLM CUDA 버전 불일치(cu128↔cu13) | 로컬에서 수동 설치한 torch와 vLLM 휠 불일치 | **Colab 런타임에서 `pip install vllm`** → vLLM이 자기 호환 torch를 함께 끌어옴(수동 torch 설치 금지) |
| ② T4가 FlashInfer/FlashAttention 미지원 → EngineCore 크래시 | sm_75는 FlashAttn(sm_80+)·FlashInfer 미지원 | **XFORMERS 어텐션 백엔드 강제** + `--enforce-eager` |
| ③ (과거 0.23.0이 env 무시했던 이력) | 버전별 백엔드 선택 차이 | **0.6.6.post1로 핀** + env `VLLM_ATTENTION_BACKEND=XFORMERS`. (※ 0.6.6엔 `--attention-backend` CLI 플래그 없음 — env로만. verify 로그의 `Using XFormers backend`로 실제 적용 확인) |

> **핵심 수칙**: 이 작업은 "한 번에 된다"고 가정하지 말 것. 아래 Step 4의 **백엔드 선택 로그 확인**이 통과해야 시연 후보다. 안 되면 §6 폴백(Ollama on 동일 T4)으로 즉시 전환 — Ollama는 같은 T4에서 이미 동작 검증됨.

---

## 2. 사전 준비 (로컬, 1회)

> Colab CLI는 **Linux/macOS** 지원 → **WSL Ubuntu에서 실행**(현 개발 환경과 동일).

```bash
# 1) CLI 설치 (uv 권장)
uv tool install google-colab-cli
# 또는: pip install google-colab-cli

# 2) 인증 (구글 계정 = 무료 Colab 쓰는 계정)
colab auth
```

확인:
```bash
colab --help
```

---

## 3. Step by Step (시연 30분~1시간 전에 1회 수행 → 그 세션 유지)

### Step 1 — T4 런타임 할당
```bash
colab new -s vllm --gpu T4
colab status -s vllm     # GPU=T4 할당 확인
```
- `-s vllm` = 세션 이름(이후 모든 명령에 `-s vllm`).
- 무료 플랜은 **피크 시간에 T4가 안 잡힐 수 있다** → 시연 직전 말고 **미리** 잡는다.
- CLI는 자동 keep-alive가 있어 브라우저 탭 없이도 유휴 종료를 막아준다(단, 무료 플랜의 최대 세션 상한 자체는 별개).

> ⚠️ **정정(2026-06-24, 실행 후)**: 본 §3은 작성 당시 *추정*으로 쓴 부분이 있었고, 실제 실행에서 아래 4가지가 **그대로는 안 됐다**. 정정 완료본은 커밋된 스크립트(`scripts/colab/*.py`)와 실측 런북([2026-06-24-eval-track7-vllm-colab-t4-runbook.md](/home/syt07203/TickerTaka-backend/memo/results/2026-06-24-eval-track7-vllm-colab-t4-runbook.md))이 정본이다.
> 1. `colab exec`는 파일을 **Python 커널**에서 실행 → `.sh`는 `SyntaxError`. 스크립트는 **`.py`**(셸은 `subprocess`).
> 2. **`--attention-backend` CLI 플래그는 vLLM 0.6.6에 없음**(`unrecognized arguments`). 어텐션 백엔드는 **env `VLLM_ATTENTION_BACKEND=XFORMERS`로만** 지정.
> 3. Colab 사전설치 **torchaudio/transformers가 vLLM 0.6.6과 충돌** → `torchaudio==2.5.1` + `transformers==4.47.1` 핀 필요.
> 4. 터널은 **`proc.wait()`로 살려둬야** 유지됨. 또 `exec(open("scripts/..."))`는 VM에 파일이 없어 FileNotFound → **인라인 heredoc**으로.

### Step 2 — vLLM 설치 (런타임 위에서) — `scripts/colab/setup.py`
```bash
colab exec -s vllm -f scripts/colab/setup.py
```
설치 내용(스크립트에 핀 반영됨): `vllm==0.6.6.post1` → 동반 `torch 2.5.1+cu124`, 그리고 충돌 회피용 `torchaudio==2.5.1`, `transformers==4.47.1`, `pyngrok`.
- **수동 torch 설치 금지**(과거 실패 ①). 끝에 `torch 2.5.1+cu124 ... device Tesla T4 / setup done` 출력되면 성공.
- 이어서 `TimeoutError: Timeout waiting for reply`가 떠도 **무해**(설치는 완료, CLI ack 타임아웃).

### Step 3 — 서버 기동 — `scripts/colab/serve.py`
```bash
colab exec -s vllm -f scripts/colab/serve.py
```
스크립트 핵심: env `VLLM_ATTENTION_BACKEND=XFORMERS` + `--dtype half --enforce-eager --max-model-len 4096 --gpu-memory-utilization 0.90`, `subprocess.Popen(..., start_new_session=True)`로 백그라운드 분리(exec 셀 종료 후에도 생존).
> 모델 다운로드+로딩 **수 분**. 바로 verify로 가면 `Connection refused` → 1~2분 뒤 재시도.

### Step 4 — ✅ 검증 — `scripts/colab/verify.py`
```bash
colab exec -s vllm -f scripts/colab/verify.py
```
확인 포인트:
- `Using XFormers backend` — **FlashInfer/FlashAttention이 잡히면 실패**(과거 ②). 그 경우 §6.
- `Application startup complete` / `Uvicorn running on ... 8000`.
- 헬스 체크 `OK: {...}` JSON(GPU 추론 성공, 항목7 핵심 증거).
- OOM(`No available memory for the cache blocks`)이면 **이전 vLLM 좀비가 VRAM 점유** → `pkill -9 -f vllm.entrypoints` 후 재기동(실측 발생).

### Step 5 — 외부 노출(터널) → 공개 base_url (인라인)
레포 파일은 Colab VM에 없으므로 `-f`나 `exec(open(...))`가 아니라 **인라인 heredoc**으로 보낸다:
```bash
colab exec -s vllm <<'PY'
import os
os.environ["NGROK_AUTHTOKEN"] = "<NGROK_TOKEN>"
from pyngrok import ngrok
ngrok.set_auth_token(os.environ["NGROK_AUTHTOKEN"])
url = ngrok.connect(8000, "http").public_url
print("PUBLIC_BASE_URL =", url + "/v1")
ngrok.get_ngrok_process().proc.wait()   # ← 이게 있어야 터널이 유지됨
PY
```
> 출력된 `https://<랜덤>.ngrok-free.dev/v1` 를 `ANALYSIS_GENERATION_BASE_URL`로. 이 exec 창은 **닫지 말 것**(닫으면 URL 무효).
> 세션/터널 재시작 시 URL 변동(Ollama plan §7 휘발성). cloudflared(`cloudflared tunnel --url http://localhost:8000`)도 가능(토큰 불요).

### Step 6 — 백엔드 `.env` 교체 (WSL, 앱 쪽)
```env
ANALYSIS_ENABLED=true
ANALYSIS_ASYNC_ENABLED=true
ANALYSIS_GENERATION_BACKEND=remote
ANALYSIS_GENERATION_BASE_URL=https://xxxx.ngrok-free.app/v1   # ← Step 5 출력
ANALYSIS_GENERATION_MODEL=Qwen/Qwen2.5-3B-Instruct           # ← vLLM은 HF 경로명 (Ollama의 qwen2.5:3b 아님)
ANALYSIS_GENERATION_API_KEY=EMPTY
```
> ⚠️ 모델명 규약(Ollama plan §7): **vLLM/HF = `Qwen/Qwen2.5-3B-Instruct`**, Ollama = `qwen2.5:3b`. backend 바꿀 때 `ANALYSIS_GENERATION_MODEL`도 같이 바꿀 것.

### Step 7 — E2E + 항목7 근거 캡처
1. 워커 기동(`analysis_async_enabled=true`).
2. 공시/뉴스 1건 인덱싱 → `analysis_jobs` 큐잉 → 워커가 **vLLM(T4)** 으로 구조화 분석.
3. `evidence_analysis` 행에 `sentiment/impact_score/summary/...` 저장 확인.
4. `GET /api/watchlists/{user_id}/feed` 에 감성 필드 노출 확인.
5. (선택) langfuse trace 1건 — remote는 `langfuse.openai` 자동계측(Ollama plan Stage 4 방법B와 동일).
6. **근거 캡처**: Step 4의 GPU 추론 응답 + `colab status -s vllm`(T4 표시) + vLLM 로그(XFormers backend) 를 항목7 결과 트랙 문서로 저장.

### Step 8 — 정리
```bash
colab download -s vllm /content/vllm.log ./vllm.log   # 로그 회수(근거)
colab stop -s vllm                                     # 시연 종료 후 런타임 종료(과금/세션 정리)
```

---

## 4. 시연 운영 수칙 (T4 무료의 진짜 리스크 = 성능 아님, 가용성/끊김)

1. **미리 띄운다**: 시연 30분~1시간 전 Step 1~5 완료. 시연 직전 재할당 금지(피크에 T4 안 잡힐 수 있음).
2. **워밍업**: Step 4 헬스 체크가 곧 워밍업. 첫 실제 요청 지연을 시연 중에 안 보이게.
3. **세션 유지**: CLI keep-alive가 유휴 종료를 막지만, 무료 최대 세션 상한은 별개 → 너무 일찍 띄워 12h 넘기지 말 것.
4. **URL 고정**: 한 번 잡은 ngrok URL을 `.env`에 넣고 끝까지 유지.
5. **폴백 즉시 전환 가능 상태로**: 로컬 Ollama(`ollama serve` + `qwen2.5:3b`)를 켜 두고, `.env` 2줄만 바꾸면 되게 준비(§6).

---

## 5. 모델 사이즈 가이드 (T4 15~16GB)

| 모델 | fp16 가중치 | T4 | 비고 |
|---|---|---|---|
| Qwen2.5-1.5B-Instruct | ~3GB | ◎ | OOM 거의 없음, 안전빵 |
| **Qwen2.5-3B-Instruct** | ~6GB | ○ | **1순위**(품질/여유 균형) |
| Qwen2.5-7B-Instruct (fp16) | ~14~15GB | ✕ | OOM 위험, 비권장 |
| Qwen2.5-7B-Instruct-AWQ | ~5GB | ○ | 품질 더 필요 시. `--quantization awq` |

OOM 나면: `--max-model-len 2048` → 그래도면 1.5B로.

---

## 6. 폴백 (vLLM이 또 깨지면)

**판단 기준**: Step 4에서 XFORMERS가 안 잡히거나 EngineCore 크래시/OOM이 반복되면 시연 위험. 시간 쓰지 말고 전환.

**폴백 A — 같은 T4에서 Ollama** (이미 동일 T4 동작 검증됨):
```bash
colab exec -s vllm <<'SH'
curl -fsSL https://ollama.com/install.sh | sh
nohup ollama serve > /content/ollama.log 2>&1 &
sleep 5 && ollama pull qwen2.5:3b
SH
```
→ ngrok로 `11434` 노출 → `.env`: `BASE_URL=<ngrok>/v1`, `MODEL=qwen2.5:3b`. (항목7은 Ollama도 인정 — Ollama plan §1.)

**폴백 B — 로컬 Ollama**: WSL `ollama serve` + `BASE_URL=http://localhost:11434/v1`. 네트워크/터널 리스크 0, 가장 안전.

---

## 7. 리스크 / 트레이드오프

- **무료 T4 가용성**: 피크에 미할당 가능 → 미리 잡기 + 폴백 필수.
- **버전 정합**: vLLM↔torch는 Colab 환경 기준으로 테스트에서 확정해야 함(과거 실패 ①). 본 문서 §8에 확정 버전 기록.
- **세션 휘발성**: 런타임/터널 재시작 시 URL 변동 → `BASE_URL` 갱신. 상시성은 NCP GPU([[infra_stage_policy]]).
- **품질**: 3B fp16은 7B 대비 약간 낮음. grounding은 프롬프트 + `_parse_json` + consistency guard로 방어(Ollama plan §7과 동일).
- **항목7 "vLLM 글자" vs 실측**: 채점 리포트는 Ollama/MLX/llama.cpp도 인정(Ollama plan §1). vLLM이 되면 더 강하지만, 안 되면 Ollama로도 항목 충족엔 문제 없음 → **시연 안정성 > vLLM 고집**.

---

## 8. 닫힘 기준 / 테스트 기록

### 1차 테스트 (2026-06-24 완료) — ✅ T4에서 vLLM 추론 성공
**확정 버전 조합(T4 sm_75에서 동작 검증됨):**
- `vllm==0.6.6.post1`, `torch 2.5.1+cu124`, `cuda 12.4`, `torchaudio==2.5.1`, `transformers==4.47.1`

**해결한 충돌 4건(setup.py/serve.py에 반영 완료):**
1. `pip install vllm` 한 줄로 torch 동반 설치(수동 torch 금지) — 과거 실패 ① 회피
2. torchaudio ABI(`undefined symbol: aoti_torch_abi_version`) → `torchaudio==2.5.1`로 torch와 정합
3. transformers 너무 최신(`Qwen2Tokenizer has no attribute all_special_tokens_extended`) → `transformers==4.47.1`
4. vLLM 0.6.6엔 `--attention-backend` CLI 플래그 없음 → env `VLLM_ATTENTION_BACKEND=XFORMERS`로만 지정

**검증 로그(증거):**
- `selector.py:129] Using XFormers backend.` (T4 우회 성공 — 과거 실패 ②③ 해소)
- `Application startup complete` / `Uvicorn running on http://0.0.0.0:8000`
- weights 5.79GB + KV cache 5.79GB / total 14.56GB, max concurrency 41x → OOM 없음, 3B 여유
- `/v1/chat/completions` → 한국어 정상 응답(GPU 추론 성공)

**1차 테스트 전 항목 완료(2026-06-24):**
- [x] ngrok URL로 WSL에서 `/v1/models` 접근 성공 (`Qwen/Qwen2.5-3B-Instruct` 응답)
- [x] 앱 `.env` remote+vLLM URL로 공시(000660) 1건 E2E → `evidence_analysis` 저장(`sentiment: positive | impact: 1`)
- [x] **항목3 langfuse trace 1건** — `LANGFUSE_TRACING_ENABLED=true` + `langfuse.openai` 드롭인으로 **같은 vLLM 실행에서 동시 [D]**. trace `8a8218...`(`evidence-enrich`, 2026-06-24T15:04:46Z), REST API 원본 `reports/.../11-langfuse-trace.json`.
- **증거 일체**: `reports/vllm-colab-t4-e2e-2026-06-24/`(README + 01~11). 항목7(vLLM POST 200) + 항목3(langfuse trace)을 한 번에 캡처 = 개선계획 P0+P1 동시 충족.

**실제로 겪은 운영 이슈(시연 대비):**
- 무료 T4 세션이 작업 중 `Session 'vllm' appears to be lost (404/401)`로 종료됨 → §4 "세션 휘발" 실증. **시연 직전에 setup하지 말고 미리 올려 유지** + Ollama 폴백 필수.
- 워커 httpx 로그 미표시 = `langfuse.openai` 드롭인 영향(고장 아님). 서버측 증거는 vLLM `/content/vllm.log`로 캡처.

### 시연용
- [ ] 시연 30~60분 전 런타임 미리 기동 + 워밍업
- [ ] ngrok URL `.env` 반영 후 feed 감성 필드 노출 확인
- [ ] 폴백(Ollama) 대기 상태 확보

### 근거(항목7)
- [ ] `colab status`(T4) + vLLM 로그(XFormers) + GPU 추론 응답 캡처 → 결과 트랙 문서화

---

## 9. 관련 문서
- 앱 연동/백엔드(이미 구현): [2026-06-13-ollama-qwen-serving-plan.md](/home/syt07203/TickerTaka-backend/memo/plans/2026-06-13-ollama-qwen-serving-plan.md:1) — `RemoteQwenEvidenceAnalyzer`, base_url 분기
- 평가 기준: [evaluation_criteria.md](/home/syt07203/TickerTaka-backend/memo/eval/evaluation_criteria.md:8) (항목7)
- 범위 합의 / 정책: [[eval-item3-7-scope-sentiment-qwen]] / [[infra_stage_policy]] / [[feedback_requirements_pinning]]
- Colab CLI: https://github.com/googlecolab/google-colab-cli , https://developers.googleblog.com/introducing-the-google-colab-cli/
