"""Colab T4 런타임에서 vLLM + 터널 의존성 설치.

  colab exec 는 파일을 Colab의 Python 커널에서 실행한다(bash 아님).
  → 셸 명령은 subprocess 로 호출한다.

사용:
    colab exec -s vllm -f scripts/colab/setup.py

수동 torch 설치 금지 — vLLM 휠이 호환 torch 를 끌어온다(과거 실패 ① 회피).
설치/추론 실패 시 vllm 버전을 조정하고
memo/plans/2026-06-24-colab-cli-vllm-serving-plan.md §8 에 확정 버전 기록.
"""
import subprocess
import sys

# T4(sm_75)에서 동작 확인 계열로 핀. 필요 시 조정.
subprocess.run([sys.executable, "-m", "pip", "-q", "install", "vllm==0.6.6.post1"], check=True)
# Colab 사전설치 torchaudio가 vLLM이 끌어온 torch 2.5.1보다 최신이라 ABI 충돌
# (undefined symbol: aoti_torch_abi_version) → torch와 동일 버전으로 맞춘다.
subprocess.run([sys.executable, "-m", "pip", "-q", "install", "torchaudio==2.5.1"], check=True)
# Colab 사전설치 transformers 가 너무 최신이라 vLLM 0.6.6 과 비호환
# (Qwen2Tokenizer has no attribute all_special_tokens_extended) → 0.6.6 시절 버전으로 내림.
subprocess.run([sys.executable, "-m", "pip", "-q", "install", "transformers==4.47.1"], check=True)
subprocess.run([sys.executable, "-m", "pip", "-q", "install", "pyngrok"], check=True)

import torch  # vLLM 설치 후 import

print("torch", torch.__version__, "cuda", torch.version.cuda, "device", torch.cuda.get_device_name(0))
print("setup done")
