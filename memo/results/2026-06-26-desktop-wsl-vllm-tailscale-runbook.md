# 런북 — 데스크톱 WSL(RTX 3060 Ti)에서 vLLM(Qwen) 서빙 + Tailscale로 노트북 앱 연결 (2026-06-26)

- 목적: **자가 GPU(RTX 3060 Ti)** 로 vLLM 상시 서빙 → 노트북 앱이 **다른 네트워크(시연장)** 에서도 접근.
  집 네트워크가 이중 NAT(모뎀→iptime→WSL)라 **포트포워딩 대신 Tailscale 메시 VPN으로 우회**.
- 구성: **앱 = 노트북**, **vLLM = 데스크톱 WSL**. 둘 다 같은 Tailscale 계정(tailnet).
- 관련: vLLM 앱 연동은 [2026-06-13-ollama-qwen-serving-plan.md](/home/syt07203/TickerTaka-backend/memo/plans/2026-06-13-ollama-qwen-serving-plan.md)의 `RemoteQwenEvidenceAnalyzer`(OpenAI 호환, base_url 분기). Colab T4 버전: [2026-06-24-eval-track7-vllm-colab-t4-runbook.md](/home/syt07203/TickerTaka-backend/memo/results/2026-06-24-eval-track7-vllm-colab-t4-runbook.md).

> 핵심: 앱은 `ANALYSIS_GENERATION_BASE_URL` 한 줄만 바꾸면 Colab/Ollama/이 데스크톱 vLLM 모두 동일 코드로 호출.

---

## 0. Tailscale은 어디에 까나 (윈도우 vs WSL)

- **노트북(앱/클라이언트)**: **윈도우용 설치로 충분.** 앱이 *나가는* 연결만 하므로 WSL 앱도 윈도우 거쳐 tailnet 접근 가능. 추가 설정 0.
- **데스크톱(vLLM 서버)**: **WSL 안에 설치 권장.** vLLM이 WSL 안에 있는데 윈도우용으로 깔면, 윈도우→WSL `netsh portproxy`가 필요하고 WSL IP가 재부팅마다 바뀌어 번거롭다. WSL 안에 깔면 그 WSL이 직접 `100.x.x.x` 노드가 돼 portproxy 없이 `:8000` 노출.
  - (대안) 정 WSL 데몬이 말썽이면 **윈도우 Tailscale + portproxy**: `netsh interface portproxy add v4tov4 listenport=8000 listenaddress=0.0.0.0 connectport=8000 connectaddress=$(wsl hostname -I)` — 단 재부팅 시 WSL IP 갱신 필요.

---

## 1. 사전 확인 (데스크톱)

- Windows에 **NVIDIA 드라이버** 설치 + WSL2 Ubuntu 존재(이미 됨).
- WSL에서 GPU 패스스루 확인:
```bash
nvidia-smi          # 'NVIDIA GeForce RTX 3060 Ti' 보이면 OK
```

## 2. vLLM 설치 (데스크톱 WSL)

> vLLM pip 휠이 CUDA 런타임을 동봉 → 보통 **Windows 드라이버만 있으면 됨**(별도 CUDA toolkit 불필요). `nvidia-smi`만 되면 진행.
> 3060 Ti = **Ampere(sm_86)** 라 bf16·FlashAttention 네이티브 → Colab T4의 `XFORMERS`/`--enforce-eager` 우회 **불필요**.

### ⚠️ CUDA 버전 정합 (2026-06-26 실측 이슈)
`pip install vllm`(무핀)은 **최신 vLLM(예: 0.23.0)** 을 끌어오고, 그게 **torch(cu13)** 를 설치한다.
드라이버 566.36은 **CUDA 12.7까지**만 지원 → cu13 torch는 런타임에서 `CUDA error`. 따라서 **cu124용 vLLM으로 핀**한다.

```bash
# 새 venv 권장(기존에 cu13 torch/triton 잔재 섞이면 또 충돌)
python3 -m venv ~/vllm-venv && source ~/vllm-venv/bin/activate
pip install -U pip
pip install vllm==0.8.5.post1        # torch 2.6 cu124 동반. (대안: 0.6.6.post1 = Colab 검증본, torch 2.5.1 cu124)
```
**설치 후 반드시 검증** — torch가 cu124이고 GPU를 잡는지:
```bash
python -c "import torch; print(torch.__version__, torch.version.cuda, torch.cuda.is_available())"
# 기대: 2.6.x+cu124  12.4  True   ← cu13/False면 실패 → 버전 더 내리거나 새 venv 재설치
python -c "import torch; print(torch.cuda.get_device_name(0))"   # RTX 3060 Ti
```
> **transformers 버전(2026-06-26 실측)**: 기존 venv를 재활용했다면 이전 설치의 **transformers 5.x**가 남아 vLLM 0.8.5와 충돌(`Qwen2Tokenizer ...` 류). vLLM 0.8.5엔 **`transformers==4.51.3`** 으로 맞춘다(0.6.6.post1이면 `4.47.1`).
> ```bash
> pip install transformers==4.51.3
> python -c "import transformers,vllm; print(transformers.__version__, vllm.__version__)"  # 4.51.3 / 0.8.5.post1
> ```
> **근본 해결**: uninstall→reinstall은 torch/transformers/tokenizers 잔재로 두더지잡기가 됨 → **새 venv**가 한 방에 깔끔.
> 핀 대신 **Windows NVIDIA 드라이버를 최신으로 업데이트**(CUDA 13 지원)하면 최신 vLLM/torch(cu13) 그대로 사용 가능 — 다운그레이드 핀이 싫으면 이 길도 OK. cu124(12.4)는 드라이버 12.7과 하위호환이라 핀 쪽이 더 간단·안전.

## 3. 모델 사이즈 — VRAM 8GB 기준

| 모델 | VRAM | 3060 Ti(8GB) |
|---|---|---|
| **Qwen2.5-1.5B-Instruct** fp16 | ~3GB | ◎ 1순위(감성분석 충분) |
| Qwen2.5-3B-Instruct-**AWQ**(4bit) | ~2.5GB | ○ 3B 품질 + 여유 (`--quantization awq`) |
| Qwen2.5-3B fp16 | ~6.2GB | △ 경계(OOM 위험), `--max-model-len 1024` |
| Qwen2.5-7B | — | ✕ 안 됨 |

## 4. vLLM 서버 기동 (데스크톱 WSL)

```bash
python -m vllm.entrypoints.openai.api_server \
  --model Qwen/Qwen2.5-1.5B-Instruct \
  --host 0.0.0.0 --port 8000 \
  --gpu-memory-utilization 0.90 \
  --max-model-len 4096
```
- **`--host 0.0.0.0` 필수**(외부 인터페이스 바인딩 → tailscale 접근 가능). 빠뜨리면 localhost만 들어 접근 불가.
- 3B-AWQ 쓰려면: `--model Qwen/Qwen2.5-3B-Instruct-AWQ --quantization awq`
- 로컬 확인(다른 WSL 창): `curl -s localhost:8000/v1/models` → Qwen 응답.
- 재부팅 후 자동기동 원하면 systemd 서비스나 `nohup ... &`로.

## 5. Tailscale 설치 (데스크톱 WSL)

```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscaled &                 # 데몬 기동 (※ 아래 TUN 참고)
sudo tailscale up                 # 출력 URL로 브라우저 로그인(노트북과 같은 계정)
tailscale ip -4                   # 이 데스크톱의 100.x.x.x → 메모
```
- **※ TUN 이슈**: `/dev/net/tun` 없다고 하면 userspace 모드:
  ```bash
  sudo tailscaled --tun=userspace-networking --socks5-server=localhost:1055 &
  sudo tailscale up
  ```
- **※ systemd 활성 WSL**(최신): `sudo systemctl enable --now tailscaled` 로 깔끔하게.

## 6. 노트북 (앱/클라이언트)

- **Tailscale 윈도우용 설치** + 데스크톱과 **같은 계정** 로그인.
- `.env`에서 vLLM을 기본 경로로:
```env
ANALYSIS_GENERATION_BACKEND=remote
ANALYSIS_GENERATION_BASE_URL=http://<데스크톱-tailscale-IP>:8000/v1
ANALYSIS_GENERATION_MODEL=Qwen/Qwen2.5-1.5B-Instruct
ANALYSIS_GENERATION_API_KEY=EMPTY
```
- 노트북 WSL에서 연결 확인:
```bash
curl -s http://<데스크톱-tailscale-IP>:8000/v1/models   # Qwen 응답이면 성공
```

## 7. E2E + 폴백

- 워커 기동 → `python -m scripts.e2e_enqueue_filing` → `evidence_analysis` 채워지면 완료(Colab 런북과 동일 흐름).
- **시연 폴백(필수)**: 집 인터넷/전원/데스크톱 상태에 의존하므로, 끊기면 노트북 로컬로 즉시 전환:
  - `ANALYSIS_GENERATION_BACKEND=transformers` (인-프로세스 Qwen) 또는 노트북 로컬 Ollama.
  - 앱은 backend 플래그만 바꾸면 되므로 무중단 전환 가능.
- **레이턴시**: 감성분석은 비동기 워커라 시연 UI에 즉답 불필요 → 시연장↔집 왕복 수백 ms는 안 보임.

---

## 8. 자주 막히는 곳

| 증상 | 원인/해결 |
|---|---|
| tailscale IP로 접근 안 됨 | vLLM `--host 0.0.0.0` 빠뜨림 → localhost만 들음. 0.0.0.0로 재기동 |
| 처음 8000 인바운드 차단 | Windows Defender 방화벽 프롬프트 **허용**(Tailscale 인터페이스) |
| `tailscale up` TUN 오류 | userspace 모드(§5 ※) 또는 systemd 활성화 |
| 재부팅 후 끊김 | vLLM·tailscaled 둘 다 재기동 필요 → systemd 서비스/자동기동 권장 |
| OOM(`No available memory for the cache blocks`) | 8GB 초과 → 모델 1.5B로 낮추거나 `--max-model-len` 축소, `--quantization awq` |
| 이전 vLLM 좀비가 VRAM 점유 | `pkill -9 -f vllm.entrypoints` 후 재기동 |
| `CUDA error` / `torch.cuda.is_available()=False` | 무핀 설치가 cu13 torch를 끌어왔는데 드라이버는 12.7까지 → §2처럼 cu124용 vLLM(`0.8.5.post1`/`0.6.6.post1`)으로 핀, 또는 드라이버 최신화 |
| transformers 버전 충돌(`Qwen2Tokenizer ...` 등) | 이전 설치의 transformers 5.x 잔재 → `pip install transformers==4.51.3`(vLLM 0.8.5) / `4.47.1`(0.6.6). 반복되면 새 venv |

## 9. 왜 이 구성인가 (요약)

- **이중 NAT 무관**: Tailscale은 기기가 바깥으로 먼저 연결하는 메시 VPN → 모뎀/iptime/WSL NAT를 포워딩 없이 우회. 고정 사설 IP라 ngrok처럼 URL이 안 바뀜.
- **3060 Ti > Colab T4(호환성)**: Ampere라 우회 플래그 불필요 + 상시 가동(휘발성 0). 단 VRAM 8GB라 모델만 1.5B/3B-AWQ.
- **항목7 충족**: 자가 GPU vLLM 서빙도 "vLLM(GPU)" 그대로 충족(증거는 Colab T4 raw 로그로 이미 확보).

## 10. 관련 문서
- Colab T4 vLLM 런북/증거: [2026-06-24-eval-track7-vllm-colab-t4-runbook.md](/home/syt07203/TickerTaka-backend/memo/results/2026-06-24-eval-track7-vllm-colab-t4-runbook.md), `reports/vllm-colab-t4-e2e-2026-06-24/`
- 앱 연동(remote 백엔드): [2026-06-13-ollama-qwen-serving-plan.md](/home/syt07203/TickerTaka-backend/memo/plans/2026-06-13-ollama-qwen-serving-plan.md)
- 인프라 정책: [[infra_stage_policy]] (졸프=로컬/단발, 운영=셀프호스트)
