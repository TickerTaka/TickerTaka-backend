# vLLM(Qwen) on Colab CLI 무료 T4 — 항목7 + 항목3 E2E 증거 (2026-06-24)

평가 항목 **7(vLLM 사용, GPU)** + **3(sLLM + langfuse, ×2)** 의 실증 캡처. Colab CLI로 무료 **T4**
GPU를 할당받아 vLLM OpenAI 호환 서버를 띄우고, 백엔드 감성분석 워커가 그 서버로 공시 1건을 처리한
전 구간 기록. **한 번의 E2E 실행에서 항목7(vLLM raw 로그) + 항목3(langfuse trace)을 동시 [D] 확보**
(개선계획 P0+P1 "같은 1회 실행" 의도대로).

- 런북: [memo/results/2026-06-24-eval-track7-vllm-colab-t4-runbook.md](/home/syt07203/TickerTaka-backend/memo/results/2026-06-24-eval-track7-vllm-colab-t4-runbook.md)
- 계획: [memo/plans/2026-06-24-colab-cli-vllm-serving-plan.md](/home/syt07203/TickerTaka-backend/memo/plans/2026-06-24-colab-cli-vllm-serving-plan.md)
- 스크립트: `scripts/colab/{setup,serve,verify,tunnel}.py`, `scripts/e2e_enqueue_filing.py`

## 확정 환경 (T4 sm_75에서 동작)
`vllm==0.6.6.post1` · `torch 2.5.1+cu124` · `torchaudio==2.5.1` · `transformers==4.47.1`
· 모델 `Qwen/Qwen2.5-3B-Instruct` (fp16, `--dtype half`) · 어텐션 `XFORMERS` (env)

## 증거 체인 (4단계 모두 통과)

| # | 단계 | 파일 | 핵심 |
|---|---|---|---|
| 1 | 서버 기동 + GPU 추론 | `04-verify.txt` | `Using XFormers backend` / `Application startup complete` / `Uvicorn :8000` / `OK:{...}` 한국어 응답 |
| 2 | 외부 연결 (ngrok) | `05-models.json` | `GET /v1/models` → `Qwen/Qwen2.5-3B-Instruct` |
| 3 | 앱 워커 → vLLM 실호출 (항목7) | `09-vllm-server.log` | `"POST /v1/chat/completions HTTP/1.1" 200 OK` (서버측) |
| 4 | 결과 DB 저장 | `08-evidence.txt` | 공시 000660 → `sentiment: positive / impact: 1`, `updated_at 2026-06-25 00:04:48` |
| 5 | **langfuse trace (항목3, ×2)** | `11-langfuse-trace.json` | `2026-06-24T15:04:46Z` · `evidence-enrich` · id `8a8218...` (REST API 원본 덤프) |

보조: `01-status.txt`(T4 할당), `02-setup.txt`(설치), `03-serve.txt`(기동), `06-worker.txt`(run_once PROCESSED:1 + FLUSHED),
`07-enqueue.txt`(작업 투입), `10-tunnel.txt`(ngrok URL).

> **항목3·7 동시성 증거**: langfuse trace `15:04:46Z` ↔ evidence_analysis `updated_at 15:04:48Z(=00:04:48 KST)` ↔ vLLM `POST 200` 이 **같은 실행**. `LANGFUSE_TRACING_ENABLED=true` + `langfuse.openai` 드롭인으로 vLLM 호출의 token/latency가 자동 trace됨.

## 핵심 발췌

**1) GPU 추론 (`04-verify.txt`)**
```
INFO ... selector.py:129] Using XFormers backend.
INFO ... model_runner.py] Loading model weights took 5.7915 GB
INFO ... gpu_executor.py] # GPU blocks: 10533 ...
INFO:     Application startup complete.
INFO:     Uvicorn running on http://0.0.0.0:8000
OK: {"model":"Qwen/Qwen2.5-3B-Instruct","choices":[{"message":{"content":"안녕하세요 ... Qwen ..."}}], "usage":{"total_tokens":67}}
```

**3) 워커 → vLLM 실호출 (`09-vllm-server.log`)**
```
INFO:     127.0.0.1:56244 - "POST /v1/chat/completions HTTP/1.1" 200 OK
```

**4) 결과 (`08-evidence.txt`)**
```
sentiment: positive | impact: 1 | updated_at: 2026-06-25 00:04:48.228005+09:00
```

**5) langfuse trace (`11-langfuse-trace.json`, REST API 원본)**
```
newest: 2026-06-24T15:04:46.969Z | evidence-enrich | 8a8218586ad81c2d8d32f5d6ec35ee7e
url: /project/cmqjflaln007fad0cix5x0uv2/traces/8a8218586ad81c2d8d32f5d6ec35ee7e
```

## 메모
- 워커 콘솔에 httpx `POST 200`이 안 보이는 건 `langfuse.openai` 드롭인 영향(고장 아님). 서버측 증거(`09`)가 동일 호출을 기록.
- 무료 T4는 작업 중에도 세션이 종료될 수 있음(`Session lost 404/401` 실측) → 시연은 미리 기동·유지 + Ollama 폴백. 재현은 런북 Part A~D 순서.
- 기동 시 `No available memory for the cache blocks`가 나면 이전 vLLM 좀비가 VRAM 점유 중 — `pkill -9 -f vllm.entrypoints` 후 재기동.
