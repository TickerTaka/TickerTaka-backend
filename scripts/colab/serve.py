"""Colab T4 런타임에서 vLLM OpenAI 호환 서버를 백그라운드로 기동.

사용:
    colab exec -s vllm -f scripts/colab/serve.py
    # 모델 변경 시:  colab exec -s vllm <<'PY'
    #                 import os; os.environ["MODEL"]="Qwen/Qwen2.5-7B-Instruct-AWQ"
    #                 exec(open("scripts/colab/serve.py").read())
    #               PY

start_new_session=True 로 분리 기동 → 이 exec 셀이 끝나도 서버는 VM 위에서 계속 산다.
모델 다운로드+로딩에 수 분 소요. 이후 verify.py 로 확인.
"""
import os
import subprocess

# ── T4(sm_75) 우회 ──────────────────────────────────────────────
# 과거 실패 ②: T4는 FlashInfer/FlashAttention 미지원 → XFORMERS 강제.
# vLLM 0.6.6 에는 --attention-backend CLI 플래그가 없다(unrecognized arguments).
# 이 버전은 env VLLM_ATTENTION_BACKEND 로만 지정하며 env 를 정상 반영한다.
env = dict(os.environ, VLLM_ATTENTION_BACKEND="XFORMERS")

# vLLM/HF 경로명 (Ollama의 qwen2.5:3b 아님). T4 1순위 = 3B fp16.
model = os.environ.get("MODEL", "Qwen/Qwen2.5-3B-Instruct")

log = open("/content/vllm.log", "wb")
subprocess.Popen(
    [
        "python", "-m", "vllm.entrypoints.openai.api_server",
        "--model", model,
        "--dtype", "half",
        "--enforce-eager",
        "--max-model-len", "4096",
        "--gpu-memory-utilization", "0.90",
        "--port", "8000",
    ],
    stdout=log,
    stderr=subprocess.STDOUT,
    env=env,
    start_new_session=True,
)

print("vllm starting (model=%s) ... log: /content/vllm.log" % model)
print("수 분 후 'colab exec -s vllm -f scripts/colab/verify.py' 로 확인하세요.")
