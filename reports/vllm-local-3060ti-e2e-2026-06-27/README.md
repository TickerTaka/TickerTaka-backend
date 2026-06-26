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

- **시연 데이터 경로**: 데모 유저 watchlist 10종목 → `evidence_analysis` **1309행 전부 sentiment 채워짐** → `GET /api/watchlists/{user_id}/feed`로 노출. (앱 import 정상, 27 routes)

## Colab T4 대비 (요약)
| | 로컬 3060 Ti | Colab T4 |
|---|---|---|
| 추론 속도(1.5B) | 단문 ~0.5s | 비슷 |
| 안정성 | ★ 본인 머신·상시·Tailscale **고정 IP** | 세션 휘발·ngrok URL 변동 |
| VRAM | 8GB → 1.5B/3B-AWQ | 15GB → 3B fp16 |

→ **시연 상시 서빙 = 로컬 GPU(이 경로)**, Colab T4 = 초기 GPU 실증(증거 유지). 둘 다 항목7 "vLLM(GPU)" 충족.

## 폴백
데스크톱/집 네트워크 의존 → 끊기면 노트북 `.env.local`에 `ANALYSIS_GENERATION_BACKEND=transformers` 한 줄로 즉시 전환(인-프로세스 Qwen). 앱 코드 무변경.
