# 런북 — Colab CLI 무료 T4로 vLLM(Qwen) 감성분석 서빙 + 워커 E2E (항목7)

- 작성: 2026-06-24 / 대상 평가항목: **항목7 (vLLM 사용, GPU)**
- 계획: [2026-06-24-colab-cli-vllm-serving-plan.md](/home/syt07203/TickerTaka-backend/memo/plans/2026-06-24-colab-cli-vllm-serving-plan.md)
- 앱 연동(기존 구현): [2026-06-13-ollama-qwen-serving-plan.md](/home/syt07203/TickerTaka-backend/memo/plans/2026-06-13-ollama-qwen-serving-plan.md) — `RemoteQwenEvidenceAnalyzer`(OpenAI 호환, base_url 분기)
- **이 문서는 실제로 성공한 명령(2026-06-24)으로 작성**했다. Part A~C(서빙)는 검증 완료, Part D(워커 E2E)는 절차 제공(사용자 환경에서 실행).

> 핵심: vLLM·Ollama 둘 다 OpenAI 호환이라 **앱은 `base_url`만 바꾸면 됨**. 서버를 Colab T4 vLLM 으로 띄우는 것이 이 런북의 전부다.

---

## 0. 확정 환경 (T4 sm_75에서 동작 검증됨)

| 구성 | 버전/값 |
|---|---|
| vLLM | `0.6.6.post1` |
| torch / cuda | `2.5.1+cu124` / `12.4` |
| torchaudio | `2.5.1` (torch 와 정합 필수) |
| transformers | `4.47.1` (vLLM 0.6.6 호환) |
| 모델 | `Qwen/Qwen2.5-3B-Instruct` (fp16, `--dtype half`) |
| 어텐션 백엔드 | `XFORMERS` (env `VLLM_ATTENTION_BACKEND`) |
| GPU 메모리 | weights 5.79GB + KV 5.79GB / total 14.56GB, 동시성 41x |

스크립트: `scripts/colab/{setup.py, serve.py, verify.py, tunnel.py}`, `scripts/e2e_enqueue_filing.py`.

---

## 1. 전체 순서 한눈에

```
[로컬 WSL]  install colab-cli → auth
            │
[Colab]     colab new --gpu T4 → setup.py(설치) → serve.py(기동) → verify.py(검증)
            │
[Colab]     tunnel(ngrok) → PUBLIC_BASE_URL 획득  (이 창 유지)
            │
[로컬 WSL]  .env(remote+URL) → pip install -r → 워커 기동 → enqueue 1건 → evidence_analysis 확인
            │
[정리]      colab stop  +  ngrok 토큰 재발급
```

---

## Part A — 로컬 준비 (1회, WSL)

```bash
# 1) Colab CLI 설치 (uv 권장). Linux/macOS만 지원 → WSL에서.
uv tool install google-colab-cli

# 2) PATH 등록 (설치 시 'not on PATH' 경고가 뜸)
export PATH="$HOME/.local/bin:$PATH"          # 이번 셸 즉시
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc   # 영구

# 3) 인증 (무료 Colab 쓰는 구글 계정)
colab auth

# 확인
which colab && colab --help
```

> ⚠️ 새 WSL 창을 열 때마다 PATH가 빠져 있으면 `colab: command not found` → 2)의 export 한 줄 다시.

---

## Part B — GPU 할당 + vLLM 기동 (Colab)

> ⚠️ Colab 런타임은 **세션마다 초기화**된다. `colab new` 후엔 항상 `setup.py`부터. 세션이 끊겨 다시 만들면 setup 재실행 필수.

```bash
# 1) T4 런타임 생성
colab new -s vllm --gpu T4
colab status -s vllm                 # Hardware: T4 | Status: IDLE 확인

# 2) 의존성 설치 (vllm + torchaudio==2.5.1 + transformers==4.47.1 + pyngrok)
colab exec -s vllm -f scripts/colab/setup.py
#  → 끝에 "torch 2.5.1+cu124 cuda 12.4 device Tesla T4 / setup done" 출력되면 성공.
#  → 이어서 'TimeoutError: Timeout waiting for reply' 가 떠도 무시(설치는 완료됨, CLI ack 타임아웃).

# 3) 서버 기동 (백그라운드, 즉시 반환)
colab exec -s vllm -f scripts/colab/serve.py

# 4) 검증 (모델 로딩에 수 분 → 1~2분 뒤 실행. 'Connection refused'면 더 기다렸다 재실행)
colab exec -s vllm -f scripts/colab/verify.py
```

**Part B 성공 기준 (verify.py 로그) — 실제 캡처:**
```
INFO ... selector.py:129] Using XFormers backend.          ← T4 우회 성공(가장 중요)
INFO ... model_runner.py] Loading model weights took 5.7915 GB
INFO ... gpu_executor.py] # GPU blocks: 10533 ... Maximum concurrency ... 41.14x
INFO:     Application startup complete.
INFO:     Uvicorn running on http://0.0.0.0:8000 (Press CTRL+C to quit)

===== GPU 추론 헬스 체크 =====
OK: {"id":"chatcmpl-...","model":"Qwen/Qwen2.5-3B-Instruct",
     "choices":[{"message":{"role":"assistant","content":"안녕하세요 ... Qwen ..."}}],
     "usage":{"prompt_tokens":35,"total_tokens":67,"completion_tokens":32}}
```
→ `Using XFormers backend` + `OK: {...}` 한국어 응답 = **GPU 추론 성공(항목7 핵심 증거)**.

---

## Part C — 외부 노출 (터널) → PUBLIC_BASE_URL

vLLM 포트(8000)를 WSL/백엔드가 접근하도록 ngrok로 노출. 파일이 아니라 **인라인**으로 보낸다
(레포 파일은 Colab VM에 없으므로 `exec(open("scripts/..."))` 는 FileNotFound).

```bash
colab exec -s vllm <<'PY'
import os
os.environ["NGROK_AUTHTOKEN"] = "<NGROK_TOKEN>"     # dashboard.ngrok.com 발급
from pyngrok import ngrok
ngrok.set_auth_token(os.environ["NGROK_AUTHTOKEN"])
url = ngrok.connect(8000, "http").public_url
print("PUBLIC_BASE_URL =", url + "/v1")
print("터널 유지 중... 이 창을 닫지 마세요 (닫으면 URL 무효)")
ngrok.get_ngrok_process().proc.wait()
PY
```
- 출력 예: `PUBLIC_BASE_URL = https://<랜덤>.ngrok-free.dev/v1`
- `proc.wait()` 가 블록하며 터널을 살려둔다 → **이 colab exec 창은 그대로 두고**, 다른 WSL 창에서 작업.
- 무료 ngrok은 재시작 때마다 URL이 바뀐다. 끊기면 이 블록 재실행 → 새 URL로 `.env` 갱신.

**연결 확인 (다른 WSL 창) — 실제 성공 응답:**
```bash
curl -s https://<랜덤>.ngrok-free.dev/v1/models
# {"object":"list","data":[{"id":"Qwen/Qwen2.5-3B-Instruct","max_model_len":4096,"owned_by":"vllm",...}]}
```
> ngrok HTML 경고가 오면: `curl -s -H "ngrok-skip-browser-warning: 1" .../v1/models`
> (openai 클라이언트 실호출은 경고에 안 걸림)

---

## Part D — 앱 연동 + 워커 E2E (WSL)

> 절차 제공(서빙은 Part B/C에서 검증 완료). 앱 DB(NCP PostgreSQL) + Redis/Chroma 기동 전제.

### D-1. `.env` (원격 vLLM 활성)
```env
ANALYSIS_ENABLED=true
ANALYSIS_ASYNC_ENABLED=true
ANALYSIS_GENERATION_BACKEND=remote
ANALYSIS_GENERATION_BASE_URL=https://<랜덤>.ngrok-free.dev/v1   # Part C 출력
ANALYSIS_GENERATION_MODEL=Qwen/Qwen2.5-3B-Instruct             # vLLM은 HF 경로명 (Ollama의 qwen2.5:3b 아님)
ANALYSIS_GENERATION_API_KEY=EMPTY
```

### D-2. 의존성 동기화
```bash
source venv/bin/activate
pip install -r requirements.txt
```

### D-3. 워커 기동 (별도 창)
```bash
python -m app.workers.analysis_worker
# 로그: "analysis worker started (model=Qwen/Qwen2.5-3B-Instruct, batch=..., poll=2.0s)"
# 큐가 비면 2초 폴링 대기. enqueue 되면 자동 처리.
```

### D-4. 분석 작업 1건 큐잉 (또 다른 창)
```bash
python -m scripts.e2e_enqueue_filing
# enqueued filing id=<uuid> symbol=005380 receipt=<14자리>
```
> 또는 앱 경로로: 관심종목 추가/새로고침 API(`POST /api/watchlists/{user_id}/refresh`)가
> 공시·뉴스 인덱싱 → 게이트 통과 시 자동으로 analysis_jobs 에 enqueue.

### D-5. 워커가 vLLM 호출 → 성공 로그 (기대값)
워커 창에:
```
INFO:httpx:HTTP Request: POST https://<랜덤>.ngrok-free.dev/v1/chat/completions "HTTP/1.1 200 OK"
processed: 1
```
> langfuse ON이면 `langfuse.openai` 드롭인이 같은 호출을 자동 trace 하며 httpx 로그가 가려질 수 있다.
> 명시적 `POST 200` 라인을 캡처하려면 `LANGFUSE_TRACING_ENABLED=false`로 1회 실행(동일 경로, 가시성 목적).

### D-6. 결과 확인 (evidence_analysis 저장) — ✅ 실측 성공 (2026-06-24)
```bash
python - <<'PY'
from sqlalchemy import select
from app.core.db import session_scope
from app.models import EvidenceAnalysis
with session_scope() as s:
    r = s.scalars(select(EvidenceAnalysis)
                  .where(EvidenceAnalysis.source_id == "720dbcaf-a66f-4102-b773-5bdf92a2a74f")
                  .limit(1)).first()
    print("sentiment:", r.sentiment, "| impact:", r.impact_score)
PY
# 실측 출력:  sentiment: positive | impact: 1   (공시 000660, receipt 20260616800847)
```
- `analysis_jobs` 전수 `status=done` 확인(공시 본문 DART 수신 → vLLM 추론 → 저장 완료).
- 프론트 노출: `GET /api/watchlists/{user_id}/feed` 응답의 해당 항목에 감성 필드가 실린다.

> **워커 콘솔에 httpx `POST .../v1/chat/completions 200` 가 안 보이는 이유**: `RemoteQwenEvidenceAnalyzer`가
> `langfuse.openai` 드롭인을 쓰며 평범한 httpx INFO 라인을 내지 않는다(고장 아님). 서버측 `POST 200`은
> vLLM 로그(`/content/vllm.log`)를 본다.

### D-7. ✅ 항목3 langfuse trace 동시 캡처 (2026-06-24)
같은 vLLM E2E 실행에서 항목3(sLLM+langfuse)도 [D]로 닫는다. `.env`에 langfuse 키 + `LANGFUSE_TRACING_ENABLED=true`.
```bash
# 워커가 죽으며 flush를 못하는 경우가 있어, run_once()를 동기 실행해 flush까지 보장
python -m scripts.e2e_enqueue_filing
python - <<'PY'
from app.workers.analysis_worker import run_once
from app.core.tracing import get_langfuse
print("PROCESSED:", run_once())
lf=get_langfuse();  lf and lf.flush(); print("FLUSHED")
PY
# trace를 REST API 원본으로 회수(스크린샷 대체, [D])
PK=$(grep ^LANGFUSE_PUBLIC_KEY .env|cut -d= -f2); SK=$(grep ^LANGFUSE_SECRET_KEY .env|cut -d= -f2); HOST=$(grep ^LANGFUSE_BASE_URL .env|cut -d= -f2)
curl -s -u "$PK:$SK" "$HOST/api/public/traces?limit=2" | python3 -m json.tool > reports/.../11-langfuse-trace.json
```
- 실측: trace `8a8218...`(`evidence-enrich`, `2026-06-24T15:04:46Z`) — evidence_analysis `updated_at 00:04:48 KST` 및 vLLM `POST 200`과 동일 실행.
- 주의: 데몬 워커는 SIGKILL(exit 9) 시 flush 전에 죽어 trace 유실 가능 → **`run_once()` 동기 실행 + 명시적 `flush()`** 가 안전.
- 주의: `e2e_enqueue_filing`은 "최신 공시 1건"을 고르므로, 특정 공시를 노릴 땐 source_id를 직접 enqueue(같은 프로세스에서 enqueue→run_once 권장, 프로세스 경계 race 방지).

---

## 2. 트러블슈팅 (이번에 실제로 만난 충돌 4건)

| 증상 | 원인 | 해결 (스크립트 반영됨) |
|---|---|---|
| `.sh` 실행 시 `SyntaxError: invalid syntax` (`pip -q install ...`) | `colab exec`는 파일을 **Python 커널**에서 실행(bash 아님) | 스크립트를 `.py`로, 셸 명령은 `subprocess` 사용 |
| `OSError: _torchaudio.abi3.so: undefined symbol: aoti_torch_abi_version` | Colab 사전설치 torchaudio가 torch 2.5.1보다 최신(ABI 불일치) | `pip install torchaudio==2.5.1` (setup.py) |
| `api_server.py: error: unrecognized arguments: --attention-backend XFORMERS` | vLLM 0.6.6엔 해당 CLI 플래그 없음 | env `VLLM_ATTENTION_BACKEND=XFORMERS`로만 지정 (serve.py) |
| `AttributeError: Qwen2Tokenizer has no attribute all_special_tokens_extended` | Colab transformers 너무 최신, vLLM 0.6.6과 비호환 | `pip install transformers==4.47.1` (setup.py) |
| `colab` 명령 없음 | `~/.local/bin` PATH 누락 | `export PATH="$HOME/.local/bin:$PATH"` |
| `exec(open("scripts/..."))` FileNotFound | 레포 파일은 Colab VM에 없음 | 터널 코드를 인라인 heredoc으로 전송(Part C) |
| `TimeoutError: Timeout waiting for reply` (setup 후) | pip 설치가 길어 CLI가 커널 ack 대기 타임아웃 | **무해** — 설치 자체는 완료됨. 무시하고 다음 단계 |
| `ERR_NGROK_3200 ... is offline` | placeholder URL(`xxxx`)을 그대로 사용 / 터널 창 종료됨 | 실제 `PUBLIC_BASE_URL` 사용 + 터널 창 유지 |

---

## 3. 시연 운영 수칙

- **미리 띄운다**: 시연 30~60분 전에 Part B~C까지 완료 + 워밍업(verify.py가 워밍업 역할). 무료 T4는 피크에 미할당 가능 → 직전 재할당 금지.
- **세션 유지**: CLI keep-alive가 유휴 종료를 막지만 무료 최대 세션 상한은 별개. 너무 일찍 띄워 장시간 방치 금지.
- **URL 고정**: 한 번 받은 ngrok URL을 `.env`에 넣고 끝까지. 터널 창 유지.
- **폴백 준비**: vLLM이 흔들리면 즉시 Ollama로 전환(`base_url`만 교체). 항목7은 Ollama도 인정.
  - 로컬: `ollama serve` + `ollama pull qwen2.5:3b` → `.env`: `BASE_URL=http://localhost:11434/v1`, `MODEL=qwen2.5:3b`
  - compose: `docker compose --profile ollama up -d` (track7 ollama 문서)

## 4. 종료/정리

```bash
colab download -s vllm /content/vllm.log ./reports/vllm-t4-serve.log   # 근거 회수(선택)
colab stop -s vllm                                                     # VM 종료
```
- 종료 후 **ngrok 토큰 재발급**(평문 노출 시).
- `colab restart-kernel`은 VM 유지/커널만 재시작(설치 유지) — 끄는 게 아님.

## 5. 관련 문서
- 계획: [2026-06-24-colab-cli-vllm-serving-plan.md](/home/syt07203/TickerTaka-backend/memo/plans/2026-06-24-colab-cli-vllm-serving-plan.md)
- 앱 연동: [2026-06-13-ollama-qwen-serving-plan.md](/home/syt07203/TickerTaka-backend/memo/plans/2026-06-13-ollama-qwen-serving-plan.md)
- Ollama 워커 E2E(동일 경로 선례): [reports/ollama-serving-e2e-2026-06-20.md](/home/syt07203/TickerTaka-backend/reports/ollama-serving-e2e-2026-06-20.md)
- 범위 합의: [[eval-item3-7-scope-sentiment-qwen]] / [[infra_stage_policy]]
