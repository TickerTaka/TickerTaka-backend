"""Colab T4 런타임에서 vLLM 기동 결과 검증.

사용:
    colab exec -s vllm -f scripts/colab/verify.py

통과 기준(시연 후보 조건):
  1) 로그에 "XFormers" backend  (FlashInfer/FlashAttention 이면 실패 → 폴백)
  2) "Application startup complete" / Uvicorn :8000
  3) OOM("CUDA out of memory") 없음
  4) 아래 /v1/chat 호출에 정상 JSON 응답(= GPU 추론 성공, 항목7 핵심 증거)
"""
import json
import urllib.error
import urllib.request

MODEL = "Qwen/Qwen2.5-3B-Instruct"  # serve.py 의 model 과 일치시킬 것

print("===== vLLM 로그 (마지막 부분) =====")
try:
    with open("/content/vllm.log", encoding="utf-8", errors="replace") as fh:
        lines = fh.readlines()
    print("".join(lines[-60:]))
except FileNotFoundError:
    print("(로그 없음 — 아직 기동 전이거나 serve.py 미실행)")

print("\n===== GPU 추론 헬스 체크 =====")
payload = {"model": MODEL, "messages": [{"role": "user", "content": "한 문장 자기소개"}]}
req = urllib.request.Request(
    "http://127.0.0.1:8000/v1/chat/completions",
    data=json.dumps(payload).encode(),
    headers={"Content-Type": "application/json"},
)
try:
    body = urllib.request.urlopen(req, timeout=120).read().decode()
    print("OK:", body[:600])
except (urllib.error.URLError, TimeoutError) as exc:
    print("아직 미기동/실패:", exc)
