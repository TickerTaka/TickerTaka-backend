# vLLM(Qwen) on 로컬 데스크톱 RTX 3060 Ti — 항목7+3 E2E 증거 (2026-06-27)

**시연 상시 서빙 경로**의 실증. 집 데스크톱(RTX 3060 Ti, WSL)에서 vLLM 서빙 → **Tailscale 메시 VPN**으로
노트북 앱이 접근 → 감성분석 워커가 OpenAI 호환 호출로 공시 1건 처리. Colab T4(휘발성)를 대체하는 **상시·고정 IP** 경로.

- 런북: [memo/results/2026-06-26-desktop-wsl-vllm-tailscale-runbook.md](/home/syt07203/TickerTaka-backend/memo/results/2026-06-26-desktop-wsl-vllm-tailscale-runbook.md)
- 초기 GPU 실증(유지): [reports/vllm-colab-t4-e2e-2026-06-24/](/home/syt07203/TickerTaka-backend/reports/vllm-colab-t4-e2e-2026-06-24/README.md)

## 구성
- **vLLM 서버**: 데스크톱 WSL, RTX 3060 Ti(8GB), `Qwen/Qwen2.5-1.5B-Instruct`, vLLM(cu124). Ampere라 T4의 XFORMERS 우회 불필요.
- **연결**: Tailscale — 데스크톱 WSL 노드 `hyc09`(100.105.127.81) ↔ 노트북 WSL 노드. 집 이중 NAT(모뎀→iptime)를 포워딩 없이 우회.
- **앱**: `.env.local`에서 `ANALYSIS_GENERATION_BASE_URL=http://100.105.127.81:8000/v1`, `MODEL=Qwen/Qwen2.5-1.5B-Instruct`.

## 증거 / 측정값

| # | 항목 | 파일 | 값 |
|---|---|---|---|
| 1 | 모델 노출 | `01-models.json` | `Qwen/Qwen2.5-1.5B-Instruct` (HTTP 200) |
| 2 | 레이턴시 | `02-latency.txt` | models **0.14s**, 단문 chat **~0.48s** |
| 3 | 워커 E2E (항목7) | `03-worker-e2e.txt` | `PROCESSED:1`, 풀 E2E **5.64s**(DART fetch+파싱+추론), `sentiment: positive / impact 1`, `updated_at 2026-06-27 00:20:19` |
| 4 | langfuse trace (항목3) | `04-langfuse-trace.json` | `evidence-enrich` `c06a4a6f...` (`2026-06-26T15:20:20Z` = 같은 실행) |
| 5 | **raw HTTP 요청·응답** | `05-vllm-chat-raw.log` | `GET /v1/models` + `POST /v1/chat/completions HTTP/1.1 200` 원본(요청헤더+응답바디 `content:"positive"`), tailscale 경로 `100.100.252.3→100.105.127.81` |

- **시연 데이터 경로**: 데모 유저 watchlist 10종목 → `evidence_analysis` **1309행 전부 sentiment 채워짐** → `GET /api/watchlists/{user_id}/feed`로 노출. (앱 import 정상, 27 routes)

## Colab T4 대비 (요약)
| | 로컬 3060 Ti | Colab T4 |
|---|---|---|
| 추론 속도(1.5B) | 단문 ~0.5s | 비슷 |
| 안정성 | ★ 본인 머신·상시·Tailscale **고정 IP** | 세션 휘발·ngrok URL 변동 |
| VRAM | 8GB → 1.5B/3B-AWQ | 15GB → 3B fp16 |

→ **시연 상시 서빙 = 로컬 GPU(이 경로)**, Colab T4 = 초기 GPU 실증(증거 유지). 둘 다 항목7 "vLLM(GPU)" 충족.

## 항목7 만점(5) 기준 대조 (rerun3 개선계획 §2-2 8항목)

| # | 기준 | 충족 | 증거 |
|---|---|---|---|
| 1 | raw 터미널 `.log`(serve 배너 + `/v1/models` + chat 요청·응답) | ✅ | 서버 배너·access·`POST 200` = Colab `09-vllm-server.log`; chat 요청·응답 원본 = 본 `05-vllm-chat-raw.log` + `04-verify.txt`(Colab) |
| 2 | 서버 기동만이 아닌 **E2E 1회**(워커→vLLM→`evidence_analysis` row) | ✅ | `03-worker-e2e.txt`(PROCESSED:1, sentiment positive) + Colab `08`/`09` |
| 3 | vLLM을 앱 venv에 설치하지 않음 | ✅ | vLLM은 Colab/데스크톱 별도 머신, 앱은 `openai` HTTP 클라만 |
| 4 | compose `profiles:["vllm"]` **또는** standalone 명령 정확 문서화 | ✅(standalone) | 런북 2종에 정확한 기동/연결 명령(`memo/results/2026-06-24·26-...`) — 기준의 "또는 standalone 문서화" 충족 |
| 5 | SHA 연동·재현 가능(명령+raw log+결과 row 함께 커밋) | ✅ | 본 reports 폴더(날짜+커밋 SHA 연동), 정확 명령은 런북 |
| 6 | langfuse drop-in으로 항목3·7 **동시 [D]** | ✅ | `04-langfuse-trace.json`(같은 실행) + Colab `11` |
| 7 | 환경 GPU or macOS | ✅ | Colab T4 + 로컬 RTX 3060 Ti(둘 다 GPU) |
| 8 | Ollama `.md` 아닌 vLLM raw `.log`를 1차 증적 | ✅ | vLLM raw 로그(`05`, Colab `09`)가 1차, Ollama는 dev 폴백 |

→ 8항목 모두 충족(4번은 "standalone 문서화" 경로). **항목7 5점 기준 충족**(최종 점수는 평가자 판정).

## 폴백
데스크톱/집 네트워크 의존 → 끊기면 노트북 `.env.local`에 `ANALYSIS_GENERATION_BACKEND=transformers` 한 줄로 즉시 전환(인-프로세스 Qwen). 앱 코드 무변경.
