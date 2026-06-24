"""Colab T4 런타임 위에서 vLLM 포트(8000)를 외부에 노출한다.

사용:
    colab exec -s vllm -f scripts/colab/tunnel.py

준비:
    - ngrok 계정의 authtoken 필요. 아래 둘 중 하나로 주입:
        (권장) Colab 런타임 환경변수 NGROK_AUTHTOKEN
               예: colab exec -s vllm <<'SH'
                     export NGROK_AUTHTOKEN=xxxx
                     python scripts/colab/tunnel.py
                   SH
        또는 이 파일의 FALLBACK_TOKEN에 직접 기입(레포 커밋 주의 — 토큰 노출 금지).

출력의 PUBLIC_BASE_URL 을 그대로 앱 .env 의 ANALYSIS_GENERATION_BASE_URL 로 사용.
세션/터널 재시작 시 URL이 바뀌므로 시연 동안 유지할 것.
cloudflared 를 쓰면 토큰 없이:  cloudflared tunnel --url http://localhost:8000
"""
import os

from pyngrok import ngrok

FALLBACK_TOKEN = ""  # 필요 시 여기에 임시 기입(커밋하지 말 것)

token = os.environ.get("NGROK_AUTHTOKEN") or FALLBACK_TOKEN
if not token:
    raise SystemExit(
        "ngrok authtoken 없음. NGROK_AUTHTOKEN 환경변수를 설정하거나 "
        "FALLBACK_TOKEN에 기입하세요. (또는 cloudflared 사용)"
    )

ngrok.set_auth_token(token)
public_url = ngrok.connect(8000, "http").public_url
print("PUBLIC_BASE_URL =", public_url + "/v1")
print("→ 앱 .env: ANALYSIS_GENERATION_BASE_URL=" + public_url + "/v1")

# 터널은 프로세스가 살아 있는 동안만 유지된다. colab exec는 스크립트 종료 시
# 끊길 수 있으므로, 터널을 유지하려면 이 프로세스를 붙잡아 둔다.
print("터널 유지 중... (이 exec 세션을 끊지 마세요. Ctrl-C/세션 종료 시 URL 무효)")
ngrok_process = ngrok.get_ngrok_process()
try:
    ngrok_process.proc.wait()
except KeyboardInterrupt:
    print("터널 종료")
    ngrok.kill()
