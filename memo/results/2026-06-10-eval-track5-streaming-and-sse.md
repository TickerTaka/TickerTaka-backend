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

### 9-1. 잔존 `avg_price` / `user_portfolio`

현재 서비스 방향은:
- 관심종목 기반 데이터/뉴스/공시/지표를 종합해 투자 판단 보조

즉 평균단가/포트폴리오 입력은 더 이상 핵심 요구사항이 아니다.

하지만 현재 코드엔 과거 설계의 잔존으로 아래가 남아 있다.
- `DebateCreateRequest.avg_price`
- `DebateState.user_portfolio`
- moderator summary prompt의 `portfolio_context`
- `/stream` query의 `avg_price`

따라서 다음 정리 단계에서 제거하는 것이 맞다.

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

1. `avg_price/user_portfolio` 잔존 코드 제거
2. 프론트 EventSource 연동
3. 필요 시 `data_node` 병렬화 보강
