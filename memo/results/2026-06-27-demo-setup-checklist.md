# 시연 세팅 체크리스트 (2026-06-27 / 내일 시연용)

구성: **앱 = 노트북**(시연장), **vLLM = 집 데스크톱 GPU**, 둘을 **Tailscale**로 연결.

---

## 🖥️ 데스크톱(집, RTX 3060 Ti) — 먼저, 켜두고 집에 둠

```bash
# WSL에서
# 1) vLLM 서버
source ~/vllm-venv/bin/activate
python -m vllm.entrypoints.openai.api_server \
  --model Qwen/Qwen2.5-1.5B-Instruct --host 0.0.0.0 --port 8000 \
  --gpu-memory-utilization 0.90 --max-model-len 4096
# 2) Tailscale (다른 창)
sudo tailscaled &          # 이미 떠 있으면 생략
sudo tailscale up          # 이미 로그인돼 있으면 'tailscale status'만
tailscale ip -4            # 100.105.127.81 확인
```
> 전원·인터넷 유지. 재부팅하면 vLLM·tailscaled 둘 다 다시 켜야 함.

## 💻 노트북(시연장) — 순서대로

```bash
cd ~/TickerTaka-backend && source venv/bin/activate

# 1) Tailscale 연결 확인
tailscale status                                         # hyc09(데스크톱) 보이는지
curl -s http://100.105.127.81:8000/v1/models             # ★Qwen 응답 와야 함(제일 중요)

# 2) .env.local (이미 설정됨 — 확인만)
#    ANALYSIS_GENERATION_BACKEND=remote
#    ANALYSIS_GENERATION_BASE_URL=http://100.105.127.81:8000/v1
#    ANALYSIS_GENERATION_MODEL=Qwen/Qwen2.5-1.5B-Instruct

# 3) Redis / Chroma
docker compose up -d redis chroma

# 4) 백엔드(uvicorn)  ← 프론트 호출 포트와 일치(보통 8000), 시연 땐 --reload 빼기
uvicorn app.main:app --host 0.0.0.0 --port 8000

# 5) 워커(다른 창)
python -m app.workers.analysis_worker                    # 첫 줄 model=Qwen/Qwen2.5-1.5B-Instruct 확인

# 6) 프론트(다른 창)
#    run dev
```

## ✅ 시연 직전 최종 점검
```bash
curl -s http://100.105.127.81:8000/v1/models   # Qwen ✔ (tailscale+vLLM 살아있음)
curl -s localhost:8000/health                  # {"status":"ok"} ✔ (백엔드)
```
- 프론트에서 관심종목 **감성/점수 표시** ✔ (피드에 분석 데이터 이미 많음 → 바로 보임)
- 라이브 데모: 종목 새로고침 → 워커가 vLLM 호출 → 감성 갱신

## 🛟 폴백 (꼭 준비 — 집 네트워크/데스크톱 죽으면)
```bash
# 노트북 .env.local 한 줄만 덮고 백엔드·워커 재시작
ANALYSIS_GENERATION_BACKEND=transformers
# → vLLM·네트워크 없이 노트북 혼자 동작(느려도 안전)
```

## Tailscale 한 줄 설명
집 데스크톱 GPU는 공유기(iptime+모뎀) 뒤에 숨어 밖에서 직접 접근 불가 → **Tailscale = 노트북·데스크톱을 잇는 사설 VPN**. 같은 계정 로그인 시 둘이 같은 가상 LAN처럼 `100.x.x.x` 고정 주소로 직결. 시연장 노트북이 집 vLLM을 그대로 호출. 포트포워딩 불필요.

## 관련 문서
- 데스크톱 셋업 상세: [2026-06-26-desktop-wsl-vllm-tailscale-runbook.md](/home/syt07203/TickerTaka-backend/memo/results/2026-06-26-desktop-wsl-vllm-tailscale-runbook.md)
- 로컬 GPU 증거: [reports/vllm-local-3060ti-e2e-2026-06-27/](/home/syt07203/TickerTaka-backend/reports/vllm-local-3060ti-e2e-2026-06-27/README.md)
