# #5 스트리밍 & 비동기 처리 1차 구현 보고서 (2026-06-10)

## 1. 목적

평가 항목 10(스트리밍 & 비동기 처리) 대응을 위해, 기존의 **완료 후 일괄 반환형 토론 API**와 별도로 **SSE(Server-Sent Events) 기반 점진 스트리밍 경로**를 추가한다.

이번 1차 구현의 목표는 다음과 같다.

- 기존 `POST /api/debates` 동기 응답 경로는 유지
- 새로운 세션 준비 + 스트리밍 실행 경로를 추가
- LangGraph `astream()` 청크를 HTTP SSE 이벤트로 forward
- 프론트가 아직 미수정이어도 기존 동작에 영향이 없도록 하위호환 유지

## 2. 기존 방식의 한계

기존 토론 API는 아래 흐름이었다.

1. `POST /api/debates`
2. 서버가 전체 토론 실행
3. 최종 summary / statements 완성 후 한 번에 JSON 반환

한계:
- 토론 도중 사용자에게 진행 상태가 보이지 않음
- 수초~수십초 동안 요청이 멈춘 것처럼 보일 수 있음
- 시연 시 “AI 토론이 순차적으로 생성된다”는 인상을 주기 어렵다

## 3. 설계 원칙

이번 구현은 **기존 API 계약을 깨지 않는 추가형 설계**를 원칙으로 한다.

- 유지:
  - `POST /api/debates`
  - `GET /api/debates/{session_id}`
- 추가:
  - `POST /api/debates/sessions`
  - `GET /api/debates/{session_id}/stream`

즉:
- 기존 프론트는 그대로 써도 문제 없음
- 새 프론트는 준비 endpoint + SSE endpoint를 붙이면 됨

## 4. 구현 범위

### 4-1. 세션 준비 endpoint 추가

파일:
- `app/api/debate.py`
- `app/schemas/debate.py`

추가 endpoint:

```http
POST /api/debates/sessions
```

역할:
- 실제 토론 실행 전 `debate_session`을 `pending` 상태로 생성
- 프론트/클라이언트가 `session_id`를 먼저 확보할 수 있게 함

응답:
- `DebatePrepareResponse`
  - `session_id`
  - `user_id`
  - `symbol`
  - `category`
  - `status`
  - `started_at`

### 4-2. SSE 스트림 endpoint 추가

파일:
- `app/api/debate.py`

추가 endpoint:

```http
GET /api/debates/{session_id}/stream
```

역할:
- 해당 세션의 토론을 SSE로 실행/전송
- 이미 완료된 세션은 DB의 saved statements/summary를 replay
- 실패 세션은 `error` 이벤트 반환

응답 형식:
- `text/event-stream`

### 4-3. 서비스 레이어 스트림 실행 추가

파일:
- `app/domain/debate_service.py`

추가 메서드:
- `DebateExecutionService.stream_session(...)`

역할:
- 기존 `run_session()`과 동일한 실행 흐름을 유지하되
- 내부 `graph_runner.astream(...)` 청크를 이벤트 단위로 변환해서 `yield`

처리 흐름:
- runtime guard 획득
- checkpoint 로드 또는 초기 state 생성
- `astream()` 순회
- `merge_state()` / `save_checkpoint()` 유지
- 청크를 SSE 이벤트로 변환
- 완료 시 `done` 이벤트 송신

## 5. 이벤트 모델

현재 1차 구현에서 정의한 이벤트 타입:

- `session_started`
- `stage`
- `agenda`
- `statement`
- `summary`
- `done`
- `error`

### 예시

```text
event: session_started
data: {"session_id":"...","symbol":"005380","category":"financial","status":"running"}
```

```text
event: statement
data: {"agent_role":"bull","round":"claim","round_order":1,"content":"..."}
```

```text
event: done
data: {"session_id":"...","status":"completed","summary_content":"...","key_points":["..."]}
```

참고:
- `: ping ...` 라인은 `sse-starlette` keep-alive 주석 라인으로 정상 동작이다.

## 6. Replay / 실패 처리 정책

### 완료 세션 replay

이미 `completed` 상태인 세션에 대해 `/stream`을 호출하면:
- DB에서 `AgentStatement`, `ModeratorSummary`를 읽어
- `statement` / `summary` / `done` 이벤트를 다시 생성해 반환

의미:
- 새 프론트가 기존 완료 토론도 동일 UI 경로로 렌더링 가능

### 실패 세션

`failed` 상태 세션은:
- 즉시 `error` 이벤트를 1회 반환

### 실행 중 세션

`running` 상태 세션은:
- `409 conflict`
- 중복 실행 방지

## 7. 구현 중 수정한 문제

### 7-1. Detached ORM 문제

초기 구현에서 SSE generator가 `TickerMetadata` / `DebateSession` ORM 인스턴스를 늦게 참조하면서:

```text
Instance <TickerMetadata ...> is not bound to a Session
```

예외가 발생했다.

조치:
- generator 진입 전
  - `session_id`
  - `symbol`
  - `symbol_name`
  - `category`
  - replay용 statement payload
  - summary payload
를 **primitive/dict**로 미리 추출하도록 수정

결과:
- detached instance 오류 해소

## 8. 검증

### 8-1. 정적 검증

실행:

```bash
python3 -m py_compile \
  app/api/debate.py \
  app/domain/debate_service.py \
  app/schemas/debate.py
```

결과:
- 통과

### 8-2. OpenAPI 확인

확인:
- `/api/debates/sessions`
- `/api/debates/{session_id}/stream`

두 경로가 `openapi.json`에 정상 노출됨

### 8-3. 실스트림 검증

절차:

1. 세션 생성

```bash
curl -X POST "http://127.0.0.1:8000/api/debates/sessions" \
  -H "Content-Type: application/json" \
  -d '{"user_id":"92042f2b-9950-457c-8092-b43d79dda768","symbol":"005380","category":"financial"}'
```

2. 스트림 연결

```bash
curl -N "http://127.0.0.1:8000/api/debates/<SESSION_ID>/stream"
```

실제 확인된 이벤트:
- `session_started`
- `stage`
- `agenda`
- `statement`(bull / moderator / bear ...)
- `summary`
- `done`

판정:
- **SSE 백엔드 1차 구현 성공**

## 9. 현재 남은 보완

이번 구현은 스트리밍 경로 자체는 성공했지만, 아래 보완이 남아 있다.

### 9-1. `avg_price` / `user_portfolio` 잔존 제거 완료

현재 서비스 방향은:
- 관심종목 기반 데이터/뉴스/공시/지표를 종합해 투자 판단 보조

즉 평균단가/포트폴리오 입력은 더 이상 핵심 요구사항이 아니다.  
초기 SSE 구현 시점엔 과거 설계 잔존으로 아래가 남아 있었으나, 이후 정리로 제거했다.

- `DebateCreateRequest.avg_price` 제거
- `DebateState.user_portfolio` 제거
- moderator summary prompt의 `portfolio_context` 제거
- `/stream` query의 `avg_price` 제거

이 변경은 **DB 컬럼과 무관한 순수 코드 정리**라 마이그레이션 없이 반영 가능했다.

### 9-2. 프론트 연동 미완

현재는 백엔드 SSE 경로만 구현됨.

프론트는 아직:
- `POST /api/debates` 결과 일괄 반환형 경로를 사용

향후 프론트는:
- `POST /api/debates/sessions`
- `GET /api/debates/{session_id}/stream`

조합으로 붙여야 점진 렌더링 UX를 얻을 수 있다.

## 10. 판정

이번 1차 구현으로 다음을 달성했다.

- 기존 토론 API 유지
- 세션 준비 endpoint 추가
- SSE 스트림 endpoint 추가
- LangGraph `astream()` 청크의 HTTP 스트리밍 전송 성공
- 완료 세션 replay 지원
- detached ORM 문제 해결

따라서 **#5 스트리밍 & 비동기 처리 트랙은 “백엔드 SSE 1차 구현 완료” 상태**로 볼 수 있다.  
다음 단계는:

1. 프론트 EventSource 연동
2. 필요 시 `data_node` 병렬화 보강
3. 운영 프록시 환경에서 SSE 버퍼링 방지 헤더 검토

---

## 11. 검증 (Claude, 2026-06-10)

> SSE 1차 구현(`api/debate.py` stream/sessions, `debate_service.stream_session`, 스키마)을 코드 기준으로 재검증. 이어붙이는 형식.

### S-0. 검증 방법 / 정상 확인 (green)
- **엔드포인트 정합**: `POST /api/debates/sessions`(→`DebatePrepareResponse`), `GET /api/debates/{id}/stream`(→`EventSourceResponse`) 코드 확인. 기존 `POST /api/debates`·`GET /{id}`는 **무변경** → 하위호환 ✓.
- **스키마 정합**: `DebatePrepareResponse`(session_id/user_id/symbol/category/status/started_at) 보고서와 일치 ✓.
- **의존성 핀**: `sse-starlette==2.1.3`([[feedback_requirements_pinning]] 충족) ✓.
- **상태별 분기**: completed→replay(저장 statements/summary), failed→`error` 1회, running→`409` 확인 ✓.
- **Detached ORM 대응**: completed replay 경로는 `statement_payloads`/`summary_payload`를 **dict로 미리 추출**해 generator가 ORM을 늦게 참조하지 않음 ✓.
- **복원력 재사용**: stream 경로도 `merge_state`/`save_checkpoint`/RuntimeGuard(`try_start_session`/`end_session`)를 run 경로와 동일하게 사용 ✓.

### S-1. [해소] 클라이언트 중도 끊김 → `debate_session.status` 고아화
초기 구현에서는 클라이언트가 스트림을 중간에 끊으면 RuntimeGuard만 해제되고 DB status가 `running`으로 남을 수 있었다.

현재는:
- `event_generator`가 `asyncio.CancelledError`를 별도로 처리
- `fail_session_if_running(session_id, "client disconnected during stream")`를 호출
- **아직 완료되지 않은 세션만 조건부로 `failed` 정리**

따라서 **중도 끊김 후 영구 `409` / replay 불가 고아 상태는 해소**됐다.

### S-2. [해소] 그래프 실패 시 `error` 이벤트 중복 전송
초기 구현에서는 `stream_session`이 예외 시 `error`를 emit한 뒤 다시 raise했고, 엔드포인트도 `except Exception`에서 `error`를 emit해 **동일 실패가 2번 전송**될 수 있었다.

현재는:
- `stream_session`은 예외 시 **상태만 `failed`로 정리하고 raise**
- 실제 `error` SSE emit은 **엔드포인트 한 곳만 담당**

따라서 **실패당 `error` 이벤트 1회**로 정리됐다.

### S-3. [낮] `GET /stream`이 부작용을 가짐 (GET 안전성 위반)
`GET`인데 `status=RUNNING` 커밋 + 전체 토론 실행 + DB 기록을 수행한다. EventSource가 GET만 지원하는 제약상 불가피하나, **프리패치/리트라이/크롤러가 URL을 건드리면 토론이 실제로 시작**될 수 있다. 졸프 범위에선 수용 가능하나, 인지하고 (가능하면) "이미 시작된 세션만 재생/거절" 가드를 견고히 둘 것. (현재 running→409가 일부 방어.)

### S-4. [낮] status 점검→설정이 원자적이지 않음 (Redis 가드가 실질 방어선)
`/stream`은 `status==RUNNING` 체크와 `status=RUNNING` 커밋 사이에 락이 없다(`SELECT ... FOR UPDATE` 아님). 동시 두 요청이 모두 통과해 RUNNING을 쓸 수 있고, 진짜 단일비행은 내부 **Redis `try_start_session`** 이 보장(둘째는 `DebateStartRejectedError`→error). 기능상 안전하나, DB 상태 자체는 race가 있음을 알아둘 것.

### S-5. [해소] running 경로 generator의 ORM 스칼라 참조
`event_generator`가 generator 내부에서 `session_row.user_id`를 읽던 부분은 `user_id_str` primitive 사전추출로 정리했다. completed replay 경로와 동일한 원칙으로 맞췄다.

### S-6. [낮·운영] 프록시 버퍼링
운영에서 nginx/NCP 프록시 뒤에 두면 SSE가 버퍼링돼 점진 전송이 깨질 수 있다. 필요 시 응답에 `X-Accel-Buffering: no` / `Cache-Control: no-cache` 부여 검토(로컬 검증엔 무관).

### S-7. [해소] 잔존 `avg_price`/`user_portfolio`
초기 구현에서 SSE 경로까지 `avg_price`와 `user_portfolio`가 pass-through로 남아 있었지만, 이후 정리로 제거했다.

현재는:
- `DebateCreateRequest`에 `avg_price` 없음
- `/api/debates/{session_id}/stream` query에 `avg_price` 없음
- `DebateState.user_portfolio` 없음
- moderator summary prompt에 `portfolio_context` 없음

즉 현재 서비스 전략과 맞지 않던 포트폴리오/평단가 문맥은 코드 경로에서 정리 완료됐다.

### S-8. 종합 판정
- **SSE 1차 구현은 동작·구조 모두 양호**: 하위호환·replay·가드·체크포인트 재사용·의존성 핀까지 정합, 실스트림 이벤트도 확인됨.
- **런타임 보완 핵심 2건(S-1/S-2) 해소**: 중도 끊김 시 고아 status와 중복 `error` emit 문제를 코드로 정리했다. 종료 정리는 `except`뿐 아니라 `finally + asyncio.shield(fail_session_if_running(...))`로 보강해 취소/조기 종료 계열까지 더 넓게 덮는다.
- 현재 남은 것은 운영성 메모(S-3/S-4/S-6) 수준이다.
- 따라서 트랙 판정은 그대로 유지되며, **"백엔드 SSE 1차 구현 완료 + 프론트 연동 전 필수 런타임 결함 정리 완료"**로 보는 것이 정확하다.

---

## 12. 보완 반영 재검증 (Claude, 2026-06-10)

> S-1/S-2/S-5 수정분을 실제 코드로 재확인. 재컴파일 `OK_COMPILE`(`debate.py`/`debate_service.py`/`debate_repo.py`).

### 코드 정합 확인 ✓
- **S-2 해소** ✓ — `stream_session`의 `except`는 `fail_session_if_running(session_id, str(exc))` + `raise`만 수행, **`error` emit 없음**(`debate_service.py:132–136`). 실제 `error` SSE는 엔드포인트 한 곳에서만 emit → **실패당 1회**.
- **S-1 해소(구조)** ✓ — `event_generator`에 `except asyncio.CancelledError`가 추가돼 중도 끊김 시 `fail_session_if_running(..., "client disconnected during stream")` 후 `raise`(`debate.py:239–243`). `asyncio` import 존재(`:3`).
- **멱등·비파괴** ✓ — `fail_session_if_running`은 `UPDATE ... WHERE id=$1 AND status='running'`(`debate_repo.py:96–103`) → **running일 때만** 실패 처리. completed/failed를 덮어쓰지 않고, 중복 호출도 무해.
- **S-5 해소** ✓ — `user_id_str = str(session_row.user_id)`를 핸들러에서 사전추출(`:145`)해 generator 내부 ORM 참조 제거.
- **Redis 가드 finally 유지** ✓ — `stream_session`의 `finally: end_session`은 그대로라, CancelledError가 service의 `except Exception`에 안 걸려도(=BaseException) **가드는 항상 해제**.

### C-1. [확인 필요] disconnect 정리는 `CancelledError` 도달에 의존 — 런타임 테스트 권장
S-1의 정리 로직은 끊김 시 generator로 **`asyncio.CancelledError`가 던져진다는 전제**다. sse-starlette 2.1.3의 task-group 취소 모델에서는 보통 `anext()` 대기 지점으로 취소가 전파되어 generator의 `yield`에 `CancelledError`로 들어오므로 **대개 발화**한다. 다만:
- 초기 지적은 타당했고, 현재는 이를 반영해 **`finally + asyncio.shield(fail_session_if_running(...))`** 로 종료 정리를 보강했다.
- 따라서 `CancelledError` 외 종료 경로에서도 **미완 세션이면 조건부 `failed` 정리**가 한 번 더 시도된다.
- 이후 실제 끊김 테스트를 수행해,
  - 스트림 도중 `Ctrl+C`
  - 이후 `GET /api/debates/{id}`에서 `status=failed`
를 확인했다. 따라서 disconnect 경로의 `running` 고아 방지도 런타임에서 검증 완료됐다.

### M-1. [무해] 그래프 실패 시 `fail_session_if_running` 중복 호출
그래프 예외 시 service와 endpoint `finally` 양쪽에서 호출될 수 있지만 `WHERE status='running'` 조건이라 **멱등**이며, 완료/실패 세션을 덮어쓰지 않는다.

### 판정
- **S-1/S-2/S-5 코드 정합 + 컴파일 통과 + disconnect 런타임 확인 완료.**
- `avg_price`/`user_portfolio` 잔존 정리(S-7)도 반영 완료.
- 따라서 SSE 트랙은 **백엔드 1차 구현 + 런타임 보강 + 잔존 코드 정리까지 완료**로 닫아도 된다.
