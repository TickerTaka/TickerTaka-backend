# 구현 결과 — Ollama 원격 서빙 원커맨드 기동 + E2E 증적 강화 (항목7 보완)

- 작성: 2026-06-20 / 브랜치: `uc`
- 대상 평가항목: **항목7(sLLM 서빙)** — 2차 재평가 2점→3~4점 목표
- 선행 작업: [2026-06-18-eval-track7-ollama-qwen-serving.md](2026-06-18-eval-track7-ollama-qwen-serving.md)
- 재평가 피드백: "compose에 서빙서비스 없음 + 기본 비활성 + 감사자 독립 동적기동 불가로 3 미달"

---

## 1. 무엇을 했나 (한 줄)

Ollama 서빙 서비스 + 모델 자동 pull(`ollama-init`)을 compose에 추가하고, `.env.example`·`README.md`에 Ollama/vLLM 전환 가이드를 강화하고, 실제 기동 + OpenAI 호환 API 호출 E2E 증적을 캡처했다.

## 2. 재평가 피드백 → 조치 매핑

| 피드백 지적 | 상태 | 이번 조치 |
|---|---|---|
| compose에 서빙 서비스 없음 | ✅ (직전 커밋에서 추가) | `ollama-init` 추가로 **원커맨드 완성** |
| 기본 비활성 | 의도적 유지 (기본 transformers) | `.env.example`에 Ollama/vLLM **프리셋 분리**, 주석 해제만으로 전환 |
| 감사자 독립 동적기동 불가 | ❌ → ✅ | `docker compose --profile ollama up -d` 한 줄로 서버+모델 자동 준비 |
| 코드만 닫혀있고 미기동 | ❌ → ✅ | E2E 증적 캡처 (`reports/ollama-remote-serving-e2e-2026-06-20.md`) |

## 3. 변경 파일

| 파일 | 변경 내용 |
|---|---|
| `docker-compose.yml` | ① `ollama` 서비스 주석 강화 (호스트/컨테이너/vLLM 전환 안내) ② `ollama-init` 서비스 신설 — `ollama` healthy 후 `qwen2.5:3b` 자동 pull, `restart: "no"` |
| `.env.example` | 감성분석 sLLM 섹션 재구성: 기본(transformers) / Ollama 빠른 전환 / vLLM 전환(GPU) 3개 프리셋 분리 |
| `README.md` | "Ollama / vLLM 원격 서빙 (선택)" 섹션 신설 — 원커맨드 기동, `.env` 전환, vLLM URL 교체 안내 |
| `reports/ollama-remote-serving-e2e-2026-06-20.md` | E2E 증적 — 컨테이너 상태, 모델 목록, OpenAI 호환 API 호출 응답(200) |

## 4. E2E 증적 요약

### 4-1. 인프라 기동

```
$ docker compose --profile ollama up -d

tickertaka-redis    Up (healthy)   0.0.0.0:6379->6379/tcp
tickertaka-chroma   Up (healthy)   0.0.0.0:8080->8000/tcp
tickertaka-ollama   Up (healthy)   0.0.0.0:11434->11434/tcp
```

모델: `qwen2.5:3b (3.1B, Q4_K_M)`

### 4-2. OpenAI 호환 API 호출

```bash
$ curl -s http://localhost:11434/v1/chat/completions \
    -d '{"model":"qwen2.5:3b","messages":[...],"temperature":0,"max_tokens":100}'
```

응답 (200):
```json
{
    "id": "chatcmpl-478",
    "model": "qwen2.5:3b",
    "choices": [{
        "message": {"role": "assistant", "content": "삼성전자는 올해 영업이익이 약 10조 원을 기대하고 있습니다."},
        "finish_reason": "stop"
    }],
    "usage": {"prompt_tokens": 53, "completion_tokens": 26, "total_tokens": 79}
}
```

→ `RemoteQwenEvidenceAnalyzer`가 사용하는 **동일 엔드포인트·동일 인터페이스**로 실호출 성공.

## 5. ollama-init 서비스 설계

```yaml
ollama-init:
  profiles: ["ollama"]
  image: ollama/ollama:latest
  depends_on:
    ollama: { condition: service_healthy }
  environment:
    OLLAMA_HOST: "http://ollama:11434"
  entrypoint: ["ollama", "pull", "qwen2.5:3b"]
  restart: "no"
```

- `ollama` healthy → `qwen2.5:3b` pull → 종료(no restart)
- 이미 모델이 있으면 즉시 완료(no-op)
- 감사자가 `docker compose --profile ollama up -d` **한 줄**로 서버+모델 준비 완료

## 6. vLLM 호환성

| 항목 | Ollama | vLLM |
|---|---|---|
| base_url | `http://localhost:11434/v1` | `http://<gpu-host>:8000/v1` |
| model | `qwen2.5:3b` | `Qwen/Qwen2.5-3B-Instruct` |
| api_key | `EMPTY` | vLLM `--api-key` 값 |
| 코드 변경 | 없음 | 없음 |

`RemoteQwenEvidenceAnalyzer`는 `openai.OpenAI(base_url=...)` 호출 → **Ollama·vLLM 양쪽 동일 코드**.

## 7. 점수 근거 정리

| 점수 기준 | 증적 |
|---|---|
| 코드 분기 실존 | `EvidenceAnalysisService.__init__` L639-647 |
| compose 서빙 서비스 | `ollama` + `ollama-init` (profile: ollama) |
| 감사자 독립 기동 | `docker compose --profile ollama up -d` 원커맨드 |
| 실제 기동 증적 | `reports/ollama-remote-serving-e2e-2026-06-20.md` — API 200 응답 |
| vLLM 호환 | URL만 교체, 코드 동일 (README + .env.example 문서화) |

## 8. 관련 문서

- 선행 구현: [2026-06-18-eval-track7-ollama-qwen-serving.md](2026-06-18-eval-track7-ollama-qwen-serving.md)
- E2E 증적: [reports/ollama-remote-serving-e2e-2026-06-20.md](/home/syt07203/TickerTaka-backend/reports/ollama-remote-serving-e2e-2026-06-20.md)
- 워커 E2E 증적: [reports/ollama-serving-e2e-2026-06-20.md](/home/syt07203/TickerTaka-backend/reports/ollama-serving-e2e-2026-06-20.md)
