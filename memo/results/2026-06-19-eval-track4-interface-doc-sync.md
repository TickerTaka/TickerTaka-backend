# 구현 결과 — 인터페이스 정의서 동기화 (항목4)

- 작성: 2026-06-19 / 브랜치: `uc`
- 대상 평가항목: **항목4 5대 설계문서** (×1)
- 직전 상태(재평가 리포트 `a134b5b`): 4/5 — 보완 #9 "구현완료인데 '추후 예정'으로 격하 표기된 엔드포인트 정식 기재" (문서가 코드보다 뒤처짐)

---

## 1. 무엇을 했나

`docs/design/interface-definition.md`를 **실제 구현된 `app/api/debate.py` 라우트와 1:1 동기화**했다. 코드엔 있으나 문서가 "추후 확장 예정"(§6)으로 격하 표기하던 2개 엔드포인트를 §3 정식 섹션으로 옮겨 기재. **코드 변경 0 (문서만).**

## 2. 격차 (동기화 전) — 코드가 문서보다 앞서 있었음

`app/api/debate.py`의 라우터(`prefix=/api/debates`)는 7개 엔드포인트를 구현했으나, 문서 §3엔 5개만 있었다:

| 엔드포인트 | 코드 | 동기화 전 문서 |
|---|---|---|
| `POST /api/debates` | `debate.py:30` | §3 ✅ |
| `POST /api/debates/sessions` | `debate.py:64` | ❌ 누락 |
| `GET /api/debates` | `debate.py:82` | §3 ✅ |
| `GET /api/debates/{id}` | `debate.py:122` | §3 ✅ |
| `GET /api/debates/{id}/stream` | `debate.py:130` | ❌ §6 "추후 예정"으로 격하 |
| `POST /api/debates/{id}/publish/notion` | `debate.py:270` | §3 ✅ |
| `DELETE /api/debates/{id}` | `debate.py:339` | §3 ✅ |

> 핵심: 문서가 **없는 기능을 허위 주장한 게 아니라**, 구현된 기능을 "예정"으로 **과소 표기**한 방향(평가 리포트 교차검증 노트와 동일 진단). 그래서 항목4가 4점에 묶였다.

## 3. 동기화 내용

- **§3에 `POST /api/debates/sessions` 추가** — 스트리밍 토론용 세션 사전생성(`pending` row 반환). 응답 `DebatePrepareResponse`(session_id/user_id/symbol/category/status/started_at) 표 기재. (`debate.py:64-79` 기준)
- **§3에 `GET /api/debates/{session_id}/stream` 추가** — SSE(`text/event-stream`) 실시간 스트리밍. 코드 기준 정확히 기재:
  - 쿼리 `decision_agent: moderator|judge`
  - 이벤트 5종 표(`session_started`/`statement`/`summary`/`done`/`error`)
  - 상태별 분기(pending=실행, completed=replay, failed=error, running=409)
  - disconnect 시 `fail_session_if_running` 정리 (`debate.py:130-267` 기준)
- **§6에서 제거** — "SSE debate stream endpoint" 줄을 삭제하고, Notion 발행과 동일하게 "§3 정식 구현 완료(추후 확장에서 제외)" 주석으로 대체. RAGAS artifact 경로도 `reports/ragas-<sha>.json`으로 정정.

## 4. 검증

- `app/api/debate.py`의 7개 라우트 ↔ 문서 §3 **7개 전부 일치** 확인(누락/과소표기 0).
- 응답 스키마(`DebatePrepareResponse`)·SSE 이벤트·상태분기·오류코드를 코드 원문과 대조해 기재.

## 5. 평가 영향 (예상, 확정 아님)

- 보완 #9 사유("구현완료 엔드포인트 추후예정 표기") 해소 → **항목4 4→5 예상**(×1, +1). 정식 확정은 신규 SHA 재평가 후.

## 6. 관련 문서

- 재평가 리포트 보완 #9 / 교차검증 노트: [BDAI_Pocat_Team2-a134b5b-rerun-2026-06-13.md](/home/syt07203/TickerTaka-backend/memo/eval/BDAI_Pocat_Team2-a134b5b-rerun-2026-06-13.md:1)
- 개선 계획 P3-3: [2026-06-13-eval-rerun-c-improvement-plan.md](/home/syt07203/TickerTaka-backend/memo/process/2026-06-13-eval-rerun-c-improvement-plan.md:1)
- 동기화 대상: `docs/design/interface-definition.md` §3
