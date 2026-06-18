# 구현 결과 — Dockerfile 멀티스테이지 빌드 (항목5)

- 작성: 2026-06-19 / 브랜치: `uc`
- 대상 평가항목: **항목5 Dockerise** (×1)
- 직전 상태(재평가 리포트 `a134b5b`): 4/5 — "단일스테이지(이미지 10GB)"가 5점 미달 사유

---

## 1. 무엇을 했나

`Dockerfile`을 **단일 스테이지 → 2-스테이지(builder/runtime)**로 전환:
- **builder**: `build-essential`로 의존성을 격리 venv(`/opt/venv`)에 설치.
- **runtime**: builder의 `/opt/venv`만 복사 + 실행 라이브러리(`curl`, `libgomp1`)만 설치. **빌드 도구는 최종 이미지에서 제외.**

## 2. 검증 (실측, 동적)

| 항목 | 결과 |
|---|---|
| `docker build -t tickertaka-multistage:test .` | ✅ exit 0 |
| 이미지 크기 | **9.99GB → 9.58GB** (약 0.41GB↓) |
| 컨테이너 기동 | ✅ `Application startup complete` / `Uvicorn running` |
| `/health` | ✅ `{"status":"ok"}` (15s 후 — torch import로 startup이 느림) |
| 런타임 라이브러리 | ✅ `libgomp1` 추가로 torch import 정상(누락 .so 없음) |

## 3. ⚠️ 크기 — 정직한 한계

개선계획/리포트는 "10GB→2~3GB"를 기대했으나 **멀티스테이지만으론 9.58GB**에 그친다. 이유:
- 9.58GB의 대부분은 **ML 휠**(torch + 그에 딸린 NVIDIA CUDA 라이브러리 휠 + transformers + sentence-transformers)이며, 이는 **런타임에도 필요**해 멀티스테이지로 못 뺀다.
- 멀티스테이지가 제거한 건 `build-essential`(~수백 MB) + apt 캐시 + 빌드 잔여물 → 약 0.41GB.
- **2~3GB로 줄이려면 CPU 전용 torch**(`pip install torch --index-url https://download.pytorch.org/whl/cpu`)로 교체해 CUDA 휠(수 GB)을 빼야 한다. 컨테이너는 GPU를 쓰지 않으므로 동작상 안전하나, requirements 변경이 필요한 **별도 트랙**으로 둔다.

## 4. 평가 영향 (예상, 확정 아님)

- 항목5가 요구하는 **멀티스테이지 빌드 구조**는 충족(빌드/런타임 분리, 빌드 도구 최종 이미지 제거) + 동적 검증(빌드·기동·/health) 통과 → **4→5 예상**.
- 단 "이미지 크기" 자체를 중시하는 채점이라면 CPU-torch 후속이 필요. 정식 확정은 신규 SHA 재평가 후.

## 5. 후속 (선택)

- **CPU 전용 torch 전환** → 9.58GB를 2~3GB대로. 가장 큰 추가 절감(별도 커밋/검토).
- `.dockerignore` 점검(빌드 컨텍스트 축소 — 빌드 속도, 이미지엔 영향 적음).

## 6. 관련 문서

- 재평가 리포트 보완 #6(항목5): [BDAI_Pocat_Team2-a134b5b-rerun-2026-06-13.md](/home/syt07203/TickerTaka-backend/memo/eval/BDAI_Pocat_Team2-a134b5b-rerun-2026-06-13.md:1)
- 개선 계획 P3-1: [2026-06-13-eval-rerun-c-improvement-plan.md](/home/syt07203/TickerTaka-backend/memo/process/2026-06-13-eval-rerun-c-improvement-plan.md:1)
- 대상: `Dockerfile`
