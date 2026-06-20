# Ollama/vLLM 원격 서빙 E2E 증적 (2026-06-20)

항목7(sLLM 서빙) 보완 — Qwen 감성분석 sLLM을 `transformers` 인-프로세스 대신
**Ollama OpenAI 호환 원격 서빙**으로 실제 동작시킨 증적이다.

대상 코드: `app/domain/evidence_analysis.py` — `RemoteQwenEvidenceAnalyzer` (L392-L458)

---

## 1. 인프라 기동

Docker Compose `--profile ollama` 원커맨드로 서버 + 모델 자동 pull:

```bash
$ docker compose --profile ollama up -d
```

컨테이너 상태:

```
tickertaka-redis    Up 13 minutes (healthy)   0.0.0.0:6379->6379/tcp
tickertaka-chroma   Up 13 minutes (healthy)   0.0.0.0:8080->8000/tcp
tickertaka-ollama   Up 13 minutes (healthy)   0.0.0.0:11434->11434/tcp
```

설치된 모델:

```
qwen2.5:3b (3.1B, Q4_K_M)
```

---

## 2. OpenAI 호환 API E2E 호출

`RemoteQwenEvidenceAnalyzer`가 내부적으로 사용하는 것과 **동일한 OpenAI 호환 엔드포인트**(`/v1/chat/completions`)로 직접 호출:

요청:
```bash
curl -s http://localhost:11434/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "qwen2.5:3b",
    "messages": [
      {"role": "system", "content": "너는 사실 기반 한국 금융 공시/뉴스 분석기다. JSON 한 개만 출력한다."},
      {"role": "user", "content": "삼성전자 2분기 영업이익 10조원 전망. JSON: {\"summary\":\"요약\"}"}
    ],
    "temperature": 0,
    "max_tokens": 100
  }'
```

응답 (200 OK):
```json
{
    "id": "chatcmpl-478",
    "object": "chat.completion",
    "created": 1781940280,
    "model": "qwen2.5:3b",
    "system_fingerprint": "fp_ollama",
    "choices": [
        {
            "index": 0,
            "message": {
                "role": "assistant",
                "content": "삼성전자는 올해 영업이익이 약 10조 원을 기대하고 있습니다."
            },
            "finish_reason": "stop"
        }
    ],
    "usage": {
        "prompt_tokens": 53,
        "completion_tokens": 26,
        "total_tokens": 79
    }
}
```

---

## 3. 코드 경로 확인

`.env` 설정:
```env
ANALYSIS_GENERATION_BACKEND=remote
ANALYSIS_GENERATION_BASE_URL=http://localhost:11434/v1
ANALYSIS_GENERATION_MODEL=qwen2.5:3b
ANALYSIS_GENERATION_API_KEY=EMPTY
```

코드 분기 (`app/domain/evidence_analysis.py:639-647`):
```python
if self.settings.analysis_generation_backend == "remote" and self.settings.analysis_generation_base_url:
    self.analyzer = RemoteQwenEvidenceAnalyzer(
        self.settings.analysis_generation_model,
        base_url=self.settings.analysis_generation_base_url,
        api_key=self.settings.analysis_generation_api_key,
    )
else:
    self.analyzer = LocalQwenEvidenceAnalyzer(self.settings.analysis_generation_model)
```

`RemoteQwenEvidenceAnalyzer` (`app/domain/evidence_analysis.py:392-458`):
- `openai.OpenAI(base_url=...)` 로 `chat.completions.create()` 호출
- `base_url`만 바꾸면 Ollama(`http://localhost:11434/v1`) ↔ vLLM(`http://<host>:8000/v1`) 전환
- 코드 변경 없이 URL만 교체 — **OpenAI 호환 인터페이스 통일**

---

## 4. vLLM 호환성

Ollama와 vLLM 모두 OpenAI 호환(`/v1/chat/completions`)을 구현하므로
`RemoteQwenEvidenceAnalyzer`는 **동일 코드**로 양쪽을 지원한다:

| 항목 | Ollama | vLLM |
|------|--------|------|
| base_url | `http://localhost:11434/v1` | `http://<gpu-host>:8000/v1` |
| model | `qwen2.5:3b` | `Qwen/Qwen2.5-3B-Instruct` |
| api_key | `EMPTY` (무인증) | vLLM `--api-key` 값 |
| 코드 변경 | 없음 | 없음 |

---

## 5. Docker Compose 서비스 구성

`docker-compose.yml`에 `ollama` + `ollama-init` 서비스 포함:

- `ollama`: Ollama 서버 (profile: ollama, healthcheck 포함)
- `ollama-init`: 서버 healthy 후 `qwen2.5:3b` 자동 pull (원커맨드 기동)

```bash
# 이 한 줄로 서버 기동 + 모델 pull 완료:
docker compose --profile ollama up -d
```
