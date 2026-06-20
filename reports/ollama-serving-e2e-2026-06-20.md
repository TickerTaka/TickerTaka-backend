# Ollama 원격 서빙 E2E 증적 (2026-06-20)

평가 항목7(vLLM/로컬 서빙) 보완 — 2차 리포트가 "Ollama 경로가 코드로는 닫혀 있으나 기본 비활성+compose 미포함이라 [S] 2점, `POST /v1/chat/completions 200` 기동 로그 artifact 커밋하면 2→3"으로 안내한 것에 대한 **실 기동 증적**.

대상: `app/domain/evidence_analysis.py` `RemoteQwenEvidenceAnalyzer`(OpenAI 호환 `chat.completions.create`), `app/workers/analysis_worker.py`.

---

## 설정 (원격 서빙 활성)
```
ANALYSIS_GENERATION_BACKEND=remote
ANALYSIS_GENERATION_BASE_URL=http://localhost:11434/v1
ANALYSIS_GENERATION_MODEL=qwen2.5:3b
```
- 서버: 로컬 `ollama serve` + `qwen2.5:3b`(q4) — `curl http://localhost:11434/api/tags` UP 확인.

## 실행 (감성분석 워커가 Ollama로 공시 1건 처리)
`analysis_jobs`에 공시(005380) 재큐잉 → `run_once()` (backend=remote → `RemoteQwenEvidenceAnalyzer` → Ollama OpenAI 호환 호출):

```
INFO:httpx:HTTP Request: POST http://localhost:11434/v1/chat/completions "HTTP/1.1 200 OK"
processed: 1
```

→ **워커가 Ollama의 OpenAI 호환 엔드포인트(`/v1/chat/completions`)를 실제 호출해 200 수신**, 공시 1건 분석 완료(`evidence_analysis` 갱신). 보고서-only가 아니라 **실 기동 로그**.

> 참고: langfuse 활성 시에는 `langfuse.openai` 드롭인이 같은 호출을 자동 trace하며 httpx 로그가 가려진다. 위 명시적 `POST 200` 라인은 langfuse OFF로 1회 캡처한 것(동일 경로, 가시성 목적). langfuse ON에서도 `processed: 1`로 동일 동작 확인됨.

## 재현 가능 (docker-compose)
`docker-compose.yml`에 **ollama 서비스 추가**(profile `ollama`로 격리):
```bash
docker compose --profile ollama up -d ollama
docker exec tickertaka-ollama ollama pull qwen2.5:3b      # 최초 1회
# .env(worker): ANALYSIS_GENERATION_BACKEND=remote, _BASE_URL=http://ollama:11434/v1, _MODEL=qwen2.5:3b
```

## 요약
| 항목 | 증적 |
|---|---|
| OpenAI 호환 서빙 호출 | `POST /v1/chat/completions 200` (httpx 로그) |
| 분석 완료 | `processed: 1` (공시 1건 → evidence_analysis) |
| 재현 | compose `ollama` 서비스(profile) + 모델 pull |

→ 항목7 "서빙 기동 증적" 확보(코드 연결 + 실 호출 200 + compose 서비스). 재평가 시 2→3 근거.
