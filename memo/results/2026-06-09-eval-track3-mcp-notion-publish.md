# 평가 대응 3차 완료 보고 — MCP 기반 Notion 발행 1차 구현 (2026-06-09)

> 평가 대응 후속 계획(`memo/process/2026-06-06-eval-followup-plan.md`)의 **우선순위 #3(MCP 도입)** 1차 구현 기록.
> 목표는 "Notion 연동" 그 자체가 아니라, **토론 완료 결과를 버튼 기반 온디맨드 경로로 MCP 프로토콜을 통해 외부 협업 시스템(Notion)에 mirror** 하는 것이었다.

## 1. 맥락

- `#1` 설계문서, `#2` 에러핸들링이 종료된 뒤 남은 평가 항목 중 다음 우선순위는 **항목 6: MCP or A2A**였다.
- 계획서에서 정한 방향은 다음과 같았다.
  - 자동 저장이 아니라 **사용자 버튼 기반 온디맨드 저장**
  - Notion은 임의 페이지 append가 아니라 **데이터베이스 row(page)** 구조
  - PostgreSQL은 계속 SOT, Notion은 **2차 mirror 저장소**
  - 본체 토론 성공 경로와 완전히 분리된 **부가 기능**

## 2. 이번에 구현한 범위

대상 파일:
- `app/api/debate.py`
- `app/integrations/notion_mcp.py`
- `app/integrations/__init__.py`
- `app/models/debate.py`
- `app/schemas/debate.py`
- `app/config.py`
- `.env.example`
- `alembic/versions/20260609_add_debate_notion_publish_columns.py`

핵심 변경:

1. **debate_session 발행 상태 컬럼 추가**
- `notion_page_id`
- `notion_page_url`
- `notion_published_at`

2. **전용 publish API 추가**
- `POST /api/debates/{session_id}/publish/notion`
- 완료(`completed`) 상태 토론만 발행 가능
- 이미 발행된 세션이면 **기존 URL 그대로 반환**하는 멱등 경로

3. **MCP stdio client 구현**
- 현재 환경엔 Python `mcp` SDK가 설치되어 있지 않아, 1차 구현은 **stdio JSON-RPC 기반 최소 MCP 클라이언트**를 직접 작성
- `initialize` → `notifications/initialized` → `tools/call` 흐름으로 MCP server와 통신
- Notion REST 직접 호출이 아니라 **실제 MCP 요청 경로가 코드에 존재**하도록 구현

4. **실제 E2E 계약으로 payload 정렬**
- 서버는 로컬 설치한 **Notion MCP 서버 바이너리**
- 실제 성공한 tool: `API-post-page`
- 인자 형태:
  - `parent: {database_id: ...}`
  - `properties: {Name:title, Session ID:rich_text, Symbol:rich_text, Category:select, Created At:date, Published At:date}`
  - `children: [paragraph | bulleted_list_item]`

5. **Notion row(page) payload 구성**
- DB property:
  - `Name`
  - `Session ID`
  - `Symbol`
  - `Category`
  - `Created At`
  - `Published At`
- 본문 blocks:
  - summary
  - key points
  - highlights(주요 발언)
  - evidence 요약 라인

6. **timeout / 응답 파싱 보강**
- `NOTION_MCP_TIMEOUT_SECONDS`를 실제 stdout read 타임아웃으로 적용
- timeout 시 MCP subprocess 종료 후 실패 반환
- `content[].text` 안의 JSON 문자열 또는 Notion URL에서도 `page_id` / `page_url` 추출 가능하도록 보강
 - MCP stdio transport를 **newline-delimited JSON** 규격으로 수정
 - `tools/call`의 `isError=true` 결과를 일반 502가 아니라 tool-level 에러로 표면화

## 3. API / 동작 방식

### 요청

- 경로: `POST /api/debates/{session_id}/publish/notion`
- body 없음

### 응답

```json
{
  "session_id": "uuid",
  "notion_page_id": "xxxx",
  "notion_page_url": "https://www.notion.so/....",
  "notion_published_at": "2026-06-09T..."
}
```

### 멱등성

- 세션 row를 `SELECT ... FOR UPDATE`로 잠금
- 이미 `notion_page_id` / `notion_page_url` / `notion_published_at`가 있으면
  - 새 page 생성 없이
  - **기존 저장 결과 반환**

### 실패 처리

- MCP / Notion publish 실패는 `502 Bad Gateway`
- 실패 시 `db.rollback()` 후 종료
- 토론 세션 본체 데이터는 그대로 유지

## 4. 현재 닫힌 범위

| 항목 | 상태 | 설명 |
|---|---|---|
| 버튼 기반 온디맨드 publish API | ✅ | 별도 엔드포인트 추가 완료 |
| Notion DB row(page) 구조 반영 | ✅ | property + paragraph/bulleted block 매핑 구현 |
| debate_session 멱등성 컬럼 | ✅ | 모델 + 스키마 + Alembic 추가 |
| MCP client → server 호출 경로 | ✅ | stdio JSON-RPC 구현 완료 |
| publish 실패 비전파 | ✅ | 본체 토론 경로와 분리, rollback 처리 |
| 실제 E2E tool 계약 정렬 | ✅ | `API-post-page` + REST typed payload 사용 |
| timeout / text-wrapped 응답 파싱 | ✅ | V-1 / V-3 보강 반영 |
| stdio transport 규격 정렬 | ✅ | newline-delimited JSON으로 수정 |
| tool-level 에러 표면화 | ✅ | `result.isError` 감지 추가 |

## 5. 환경 / 설정

추가된 env:

- `NOTION_TOKEN`
- `NOTION_DATABASE_ID`
- `NOTION_MCP_SERVER_COMMAND`
- `NOTION_MCP_SERVER_ARGS`
- `NOTION_MCP_TOOL_NAME`
- `NOTION_MCP_TIMEOUT_SECONDS`

의미:
- `NOTION_TOKEN`: Notion integration 토큰
- `NOTION_DATABASE_ID`: "토론 기록" 데이터베이스 ID
- `NOTION_MCP_SERVER_COMMAND`: MCP server 실행 커맨드
- `NOTION_MCP_SERVER_ARGS`: MCP server 인자
- `NOTION_MCP_TOOL_NAME`: row 생성에 사용할 MCP tool 이름 (`API-post-page`)
- 이 환경에선 `npx` 대신 **로컬 설치 바이너리 직접 실행**이 실제 E2E에 성공했다

## 6. 검증

실행 검증:

- `python3 -m py_compile app/api/debate.py app/integrations/notion_mcp.py app/schemas/debate.py app/models/debate.py alembic/versions/20260609_add_debate_notion_publish_columns.py`
  - 통과

코드상 확인 포인트:

- `DebateSession`에 Notion 발행 상태 3컬럼 추가
- publish 엔드포인트에서 `completed` 상태 검사
- `with_for_update()` 기반 멱등성 분기
- MCP stdio 클라이언트의 `initialize` / `tools/call`
- REST typed payload(`parent/properties/children`)
- stdio transport가 **newline-delimited JSON** 규격을 따름
- read timeout 강제 종료
- text-wrapped JSON / URL 응답 파싱
- `result.isError` 감지 시 원인 메시지 보존
- MCP 실패 시 `db.rollback()` + `HTTP 502`

## 7. 아직 남은 것 / 운영 전제

1. **실제 E2E는 Notion MCP server와 토큰이 있어야 가능**
- 이번 구현은 런타임 경로까지 붙였지만,
- 실제 row 생성까지 확인하려면
  - Notion DB 수동 생성
  - integration 공유
  - MCP server 실행
  이 선행돼야 한다.

2. **프론트 버튼은 아직 미착수**
- 이번 범위는 백엔드 publish API까지
- 토론 상세 UI의 `노션에 저장` 버튼 연결은 후속 작업

3. **Notion property 이름과 타입은 현재 코드 전제와 맞아야 함**
- `Name`, `Session ID`, `Symbol`, `Category`, `Created At`, `Published At`
- 실제 Notion DB 컬럼명이나 타입이 다르면 row 생성이 실패할 수 있음

4. **현재 1차 구현은 로컬 MCP 바이너리 + stdio 최소 client**
- `.env` 권장값:
  - `NOTION_MCP_SERVER_COMMAND=/abs/path/to/.notion-mcp/node_modules/.bin/notion-mcp-server`
  - `NOTION_MCP_SERVER_ARGS=`
  - `NOTION_MCP_TOOL_NAME=API-post-page`
- 이 환경에서는 `mkdir -p .notion-mcp && npm install --prefix .notion-mcp @notionhq/notion-mcp-server` 후 **설치된 바이너리 직접 실행**이 가장 안정적이었다

## 8. 결론

- **#3 MCP 도입은 1차 구현 + V-1/V-2/V-3 보강 + 실제 E2E 성공까지 확인된 상태**로 볼 수 있다.
- 이번 변경으로
  - 토론 본체는 PostgreSQL에 유지하고
  - 사용자가 원할 때만
  - MCP 경유로 Notion DB에 외부 mirror 저장
하는 구조가 실제 코드에 생겼다.
- 다음은
  - 프론트 버튼 연결
  - 필요 시 보고서/발표 시연 흐름 정리
정도다.

---

## 9. 검증 결과 (Claude, 2026-06-09)

> 보고서 본문(§1–8) 작성 이후, 변경 파일 전체를 코드 기준으로 재검증한 기록. 이어붙이는 형식.

### V-0. 검증 방법

- **재컴파일(WSL venv)**: `python -m py_compile app/api/debate.py app/integrations/notion_mcp.py app/integrations/__init__.py app/models/debate.py app/schemas/debate.py app/config.py alembic/versions/20260609_*.py` → `OK_COMPILE`. (참고: 보고서 §6의 `python3`는 WSL에선 유효하나, 이 저장소를 Windows 셸에서 다룰 땐 `python3`가 Store stub이라 무의미 — WSL에서 돌린 것으로 해석.)
- **마이그레이션 체인**: `a8a60fcd0ed2(init) → b1c2d3e4f5a6(eval) → c2d3e4f5a6b7(notion)` 선형 단일 head 확인. `down_revision` 정확.
- **모델/스키마 정합**: `DebateSession`에 `notion_page_id(255)/notion_page_url(2048)/notion_published_at(tz)` 추가가 마이그레이션 컬럼 타입과 일치. `DebateNotionPublishResponse`(non-null 3필드)와 엔드포인트 반환부의 `or ""`/`or now()` 가드 정합.
- **한계**: `py_compile`은 import·라우트 등록·런타임 동작을 보장하지 않는다. `import app.api.debate` 스모크는 이 로컬 WSL venv의 OpenSSL 링크 문제(`OPENSSL_3.3.0`)로 실패했으며, 이번 MCP 변경 자체와는 무관한 환경 이슈로 분리했다.

### V-1. [중대] `NOTION_MCP_TIMEOUT_SECONDS`가 실제로 동작하지 않음

`_StdioJsonRpcClient`는 `timeout_seconds`를 생성자에서 받아 `self._timeout_seconds`에 저장만 하고, **어디서도 사용하지 않는다.** `request()`의 응답 대기 루프와 `_read()`의 `readline()`/`read(content_length)`는 **무한 블로킹**이다. 즉 MCP 서버가 멈추면 요청은 영원히 안 끝난다.

- 더 나쁜 점: 엔드포인트가 `SELECT ... FOR UPDATE`(`debate.py:124`)로 **세션 row 락을 MCP 호출 내내 점유**한다. MCP 서버가 hang하면 HTTP 워커 스레드 + DB row 락 + 커넥션이 **무기한 묶인다**.
- 권장: `_read`에 deadline 적용(예: 별도 스레드+`proc.kill()` 워치독, 또는 stdout을 `select`/poll로 감시하다 초과 시 `NotionMcpError`). 최소한 `subprocess`에 전체 deadline을 걸고 초과 시 강제 종료해야 광고된 timeout이 의미를 가진다.
- **조치**: subprocess stdout read를 별도 executor future로 감싸고, `NOTION_MCP_TIMEOUT_SECONDS` 초과 시 프로세스를 종료하도록 수정. 현재는 설정값이 실동작한다.
- **추가 운영 권고**: `npx` 첫 호출 패키지 다운로드가 timeout을 잡아먹을 수 있으므로, 실데모 전엔 global install 후 캐시를 워밍업하는 절차를 필수로 둔다.

### V-2. [높음] tool 인자 스키마가 Notion REST 형태 — 서버 호환이 제한적

초기 구현의 `publish_debate`는 `{"parent":{"database_id":...}, "properties":..., "children":...}`로 **Notion REST API의 page 생성 바디 그대로**를 보냈다. 이 형태가 통하는 건 **REST를 1:1 프록시하는 MCP 서버**(예: `makenotion/notion-mcp-server`의 OpenAPI 변형, 보통 tool 이름이 `API-post-page` 류)뿐이었다.

- 기본값 `NOTION_MCP_TOOL_NAME="create_page"`는 실제 서버 어느 쪽과도 잘 안 맞았다.
- **최종 조치**: 이 환경의 실제 E2E는 로컬 설치 MCP 바이너리 + `API-post-page` 계약으로 확정했다. 따라서 tool 이름과 payload는 REST typed 형태를 기준으로 본다.
- **추가 조치**: `tools/call` 결과의 `isError=true`를 감지해 property 불일치/권한 오류가 `"did not include page id/url"` 같은 일반 메시지로 묻히지 않도록 보강했다.

### V-3. [높음] 응답 파싱이 text-wrapped JSON을 처리하지 못함

`_extract_page_fields`는 결과를 순회하며 `id`/`page_id` + `url`/`page_url`을 **dict 키**로 찾는다. 그러나 다수 MCP 서버는 tool 결과를 `content:[{"type":"text","text":"<JSON 문자열>"}]` 형태로 돌려준다. 이 경우 순회기는 **문자열을 만나 그냥 스킵**하고, 결국 `"MCP response did not include Notion page id/url"`로 **502를 던진다 — Notion에 페이지가 실제로 생성됐더라도.**

- 이게 **가장 가능성 높은 E2E 실패 모드**다(쓰기는 됐는데 응답 파싱 실패 → 502 → 멱등 컬럼 미기록 → 재시도 시 중복 생성).
- **조치**: `content[].text` / 임의 문자열에서 `json.loads`를 시도하고, 실패해도 Notion URL/UUID 패턴을 추출해 `page_id` / `page_url`를 복원하도록 보강했다.

### N-1. [중대] MCP stdio 프레이밍 규격 불일치

추가 검증에서 초기 구현이 LSP식 `Content-Length` 헤더 프레이밍을 사용하고 있음을 확인했다. 공식 MCP stdio는 **newline-delimited JSON** 이므로, 이 상태에선 `initialize`부터 양방향 파싱이 깨진다.

- **조치**: `_send()`는 `json.dumps(payload) + "\n"`, `_read()`는 `readline() -> json.loads(line)`로 수정했다.
- 결과적으로 현재 로컬 설치 MCP 바이너리와 transport 계층 정합이 맞는다.

### N-2. [중] tool-level `isError` 미표면화

`tools/call`이 transport는 성공했지만 결과 payload에서 `isError: true`를 반환하는 경우, 기존 코드는 이를 놓치고 이후 `page_id/url` 미존재 에러로 일반화했다.

- **조치**: `request()`에서 `result.isError`를 감지해 즉시 `NotionMcpError`로 올리도록 수정했다.

### N-5. [낮음] `npx` 콜드스타트 운영 리스크

첫 호출에서 `npx -y @notionhq/notion-mcp-server`가 패키지 다운로드를 포함하면 30초 timeout을 초과할 수 있다.

- **조치**: 코드가 아니라 운영 절차로 분리했다.
- 권장:
  - `npm install -g @notionhq/notion-mcp-server`
  - 또는 시연 전 `npx -y @notionhq/notion-mcp-server --help` 등으로 캐시 워밍업

### V-4. [중] row 락이 외부 호출 구간 전체를 점유

`with_for_update()` 락이 수 초짜리 MCP 서브프로세스 호출 동안 유지된다. **동일 세션 중복 발행 방지**라는 의도엔 부합하지만, 느린 Notion 호출이 PG row 락 + 커넥션을 점유한다. 졸프 규모에선 수용 가능하나 V-1(무한 대기)과 겹치면 실제 hang 위험. 최소한 V-1을 고쳐 락 점유 시간에 상한을 둘 것.

### V-5. [중] Notion 성공 후 commit 실패 시 중복 위험

`client.publish_debate` 성공 → 컬럼 대입 → `db.commit()` 순서인데, commit이 실패하면 **Notion 페이지는 생겼지만 DB엔 미기록** → 다음 재시도가 새 페이지를 또 만든다. Notion 측엔 멱등키가 없다. 엣지 케이스라 졸프 범위에선 허용 가능하나, 보고서 "멱등성" 항목에 *"DB 커밋까지 성공해야 멱등 보장, 그 사이 크래시는 중복 가능"* 단서를 달아두는 게 정확하다.

### V-6. [중] property "타입"까지 Notion DB와 일치해야 함

현재 payload는 property를 **Notion REST 타입 객체**로 보내며, 실제 Notion DB 쪽에선 각 컬럼의 **실제 Notion 타입**이 여기에 맞아야 한다. 예를 들어 `Name`은 title, `Session ID`/`Symbol`은 rich_text, `Category`는 select, `Created At`/`Published At`는 date여야 한다. 즉 이름만이 아니라 **이름 + DB 타입 정합**이 필요하다.

### V-7. [낮음] 운영 가시성

- `stderr=PIPE`로 바꿔 timeout/종료 시 tail을 에러 메시지에 포함하도록 보강했다. 브링업 단계 디버깅 가시성은 전보다 좋아졌다.
- `initialize` 응답과 `tools/list`를 검증하지 않아, tool 이름이 틀리면 일반 오류만 난다. (선택) 발행 전 `tools/list`로 tool 존재 확인하면 진단이 쉬워진다.

### V-8. 보고서 자체 보정 사항

- §6 검증 표기를 "WSL `python -m py_compile`"로 명확히(Windows 셸 `python3`와 구분). 그리고 **import 스모크 테스트**(`python -c "import app.api.debate"`) 1줄을 추가하면 라우트 등록/순환 import까지 한 번에 잡힌다.
- 부수 효과로 `DebateSessionResponse`에 `notion_page_id/url/published_at`가 추가돼, **`GET /api/debates/{id}` 응답이 발행 상태를 함께 노출**한다 → 프론트의 "노션에 저장 ↔ 노션에서 보기" 버튼 토글에 그대로 쓸 수 있다(긍정적 부수효과, 보고서에 명시 가치 있음).

### V-9. 종합 판정

- **구조·정합성은 양호**: 멱등 컬럼·마이그레이션·스키마·fail-soft(502 + rollback)·실제 E2E에 성공한 `API-post-page` 계약까지 계획서 닫힘 기준을 코드로 충족했고, 컴파일/체인 검증 통과.
- **단, 실서버 E2E 성공은 여전히 Notion DB property 정합과 실제 MCP 서버 실행 상태에 달려 있다.** 이번 라운드로 V-1(timeout)·V-2(tool 계약)·V-3(응답 파싱)와 N-1(transport)·N-2(tool-level 에러) 보강은 반영됐다. 남은 리스크는 주로 운영 전제(DB 구조, 토큰/공유, 실제 server availability) 쪽이다.
- 나머지(V-4~V-7)는 디리스크/폴리시 항목으로, 졸프 시연 범위에선 후순위로 둬도 된다.

---

## 10. 추가 검증 (Claude, 2026-06-09 · 중간 재작업 기록)

> MCP 통신 경로를 정리하던 중간 라운드 기록. 현재 최종 구현과 E2E 성공 기준은 §11을 우선으로 읽는 것이 맞다.

### N-0. 재검증 방법

- **재컴파일(WSL venv)**: `python -m py_compile app/integrations/notion_mcp.py app/api/debate.py app/config.py` → `OK_COMPILE`.
- **V-1 조치 확인**: `_read_with_timeout`가 `_read`를 단일 워커 executor future로 감싸고 `future.result(timeout=...)` 초과 시 `_terminate_process` 후 `NotionMcpError`. → timeout 실동작 ✅ (단 N-4 단서).
- **당시 중간 상태 확인**: payload/transport 수정 방향은 코드에 반영되어 있었고, 이후 최종 E2E에서는 `API-post-page` 계약으로 확정됐다.
- **V-3 조치 확인**: `_extract_page_fields`가 dict/list뿐 아니라 `text` 필드와 임의 문자열에 대해 `json.loads` 시도 + Notion URL/UUID 정규식 추출 ✅.
- **후속 보정**: 계획서와 `.env.example` / `config.py`는 이후 실제 성공 계약(`API-post-page`, 로컬 바이너리 직접 실행) 기준으로 다시 정렬됐다.

### N-1. [중대] MCP stdio 프레임 형식 불일치 — `Content-Length` 헤더 vs newline-delimited JSON (E2E 차단)

`_StdioJsonRpcClient._send`/`_read`는 **LSP 스타일 `Content-Length: N\r\n\r\n<body>` 프레이밍**을 쓴다. 그러나 **MCP stdio 전송 규격은 "줄바꿈으로 구분되는 JSON(newline-delimited JSON)"** 이다 — 각 JSON-RPC 메시지를 한 줄로 보내고 줄바꿈으로 구분하며, **Content-Length 헤더를 쓰지 않는다.** (Content-Length는 HTTP/Streamable HTTP 전송의 개념이고 stdio가 아니다.)

- 결과: 공식 `@notionhq/notion-mcp-server`(표준 SDK 기반)는
  - **수신**: 우리가 보낸 `Content-Length:` 헤더 줄을 JSON으로 파싱하려다 실패 → `initialize`부터 안 먹힌다.
  - **송신**: 서버는 응답을 **헤더 없는 JSON 한 줄**로 보내는데, 우리 `_read`는 그 줄을 "헤더"로 읽고 `content-length` 키를 못 찾아 `"Invalid MCP response headers"`로 죽는다.
- 즉 **양방향 모두 깨진다.** 이건 property 정합 이전에, 핸드셰이크(`initialize`) 단계에서부터 E2E가 막히는 문제다. **현재 가장 우선순위 높은 차단 결함.**
- **수정(권장, `notion_mcp.py` 안에서 끝)**:
  - `_send`: `Content-Length` 헤더 제거 → `self._proc.stdin.write(json.dumps(payload).encode("utf-8") + b"\n")` 후 flush. (`json.dumps`는 기본적으로 본문 내 줄바꿈을 `\\n`으로 이스케이프하므로 메시지에 실제 개행이 끼지 않는다 — 규격 요건 충족.)
  - `_read`: 헤더 파싱 제거 → `line = stdout.readline()`; 빈 줄/EOF 처리 후 `json.loads(line)`. (서버가 가끔 빈 줄을 흘리면 skip.)
- ⚠️ 이 한 건이 **§7·§9에서 "남은 건 운영 전제뿐"이라는 결론을 약화**시킨다. 운영 전제(토큰/공유/DB 구조) 이전에 **전송 프레이밍부터 고쳐야 실제로 한 번이라도 통신이 된다.**

### N-2. [중] tool-level 에러(`isError: true`)를 감지하지 못함 — 실패가 일반 메시지로 묻힘

`request()`는 **JSON-RPC `error`** 만 검사한다. 그러나 MCP `tools/call`은 **성공 응답(JSON-RPC result) 안에 `isError: true` + `content:[{text:"...에러..."}]`** 형태로 tool 실행 실패(권한 부족, property 이름/타입 불일치 등)를 담아 보낸다. 현재 코드는 이걸 정상 result로 받아 `_extract_page_fields`가 id/url을 못 찾고 → `"MCP response did not include Notion page id/url"`라는 **일반 메시지로 502**를 낸다.

- 부작용: §7-3에서 경고한 **property 이름/타입 불일치가 발생했을 때 정작 원인 메시지가 사라진다**(디버깅 난도 급상승).
- 권장: `result.get("isError")`가 truthy면 `content[].text`를 모아 `NotionMcpError`로 표면화. N-1 다음으로 중요(E2E 1차 통신 후 진단을 좌우).

### N-3. [중] stderr PIPE 미배수 → verbose 로깅 시 타임아웃까지 hang

`stderr=subprocess.PIPE`인데 **`_collect_stderr`는 종료(terminate) 시점에만** 읽는다. 서버가 동작 중 stderr로 많이 쓰면 OS 파이프 버퍼(~64KB)가 차고, 서버는 stderr write에서 블록 → stdout 응답을 못 보냄 → 우리는 timeout으로만 풀린다(= 멀쩡한 호출이 spurious timeout). 

- 완화: stderr를 **별도 스레드로 동시 배수**하거나, 브링업 동안 임시파일로 리다이렉트. 현재 구조는 "조용한 서버" 전제에서만 안전.

### N-4. [낮음] timeout이 메시지당(per-read)이지 전체 deadline이 아님

`request()`는 req_id가 맞을 때까지 루프하며 매번 `_read_with_timeout`를 새로 건다. 서버가 비매칭 메시지(로그/알림)를 timeout 직전 간격으로 계속 흘리면 **총 소요시간이 `NOTION_MCP_TIMEOUT_SECONDS`를 초과**할 수 있다. 실무상 Notion 서버가 그러진 않으나, 정확히는 "메시지당 상한"이지 "호출 전체 상한"이 아님을 알아둘 것. (전체 deadline을 쓰려면 시작 시각 기준 잔여시간을 매 read에 전달.)

### N-5. [낮음] npx 콜드스타트 ↔ 이제 동작하는 timeout의 상호작용

timeout이 실동작하게 되면서, **첫 호출에서 `npx`가 패키지를 받는 시간이 `NOTION_MCP_TIMEOUT_SECONDS`(기본 30s)를 넘으면 첫 발행이 timeout으로 실패**할 수 있다(망 상태에 따라). §7-4의 "사전 설치" 권장을 **E2E 전 필수 절차로 격상**하는 게 안전: `npm i -g @notionhq/notion-mcp-server` 후 설치 바이너리를 `NOTION_MCP_SERVER_COMMAND`로 직접 지정(콜드스타트 제거).

### N-6. 보고서/플랜 표기 보정

- §2-3·§3·§6은 여전히 "stdio JSON-RPC 최소 클라이언트"라고만 적고 **프레이밍 방식을 명시하지 않는다.** N-1을 고친 뒤 "**newline-delimited JSON**(MCP stdio 규격)"이라고 못박아 두면, 다음에 누가 봐도 Content-Length로 회귀하지 않는다.
- §7-3에 N-2(`isError` 표면화)와 N-3(stderr 배수)를 "E2E 진단을 위해 함께 처리"로 한 줄 추가 권장.

### N-7. 종합 판정 (갱신)

- **양호**: timeout 실동작(V-1)·응답 파싱 보강(V-3)은 코드로 확인됐고, 이후 최종 라운드에서 `API-post-page` 실서버 E2E 성공까지 확인됐다.
- **그러나 "1차 구현 완료 + 보강 반영"이 곧 "E2E 통신 가능"은 아니다.** N-1(stdio 프레이밍) 때문에 **현재 코드로는 공식 서버와 핸드셰이크조차 성립하지 않을 가능성이 매우 높다.** E2E 전에 **반드시** 고칠 순서: ① N-1(newline 프레이밍) → ② N-2(`isError` 표면화) → ③ N-5(npx 사전설치). 셋 다 `notion_mcp.py`/운영절차 안에서 끝나며 새 파일·공유 파일 변경 없음.
- N-3/N-4는 안정화 항목으로 그다음. 평가 항목6(MCP 프로토콜 경로 실재)이라는 **점수 논리 자체는 이미 충족**(코드에 MCP client↔server 경로 존재)이라, N-1은 "실데모 성공"을 위한 수정이지 "항목6 인정"을 위한 전제는 아니라는 점은 분리해 둔다.

---

## 11. 최종 검증 (Claude, 2026-06-09 · N-1/N-2 반영 후)

> N-1(프레이밍)·N-2(`isError`) 수정 반영본을 **코드 일치 확인 + 파싱 로직 실제 실행**으로 검증한 라운드. 이번엔 컴파일에 더해 핵심 로직을 직접 돌렸다.

### N-1 / N-2 반영 확인 (코드 일치) ✅

- **N-1 (transport)**: `_send`가 `json.dumps(payload).encode() + b"\n"`로 바뀌고 Content-Length 헤더가 제거됨(`notion_mcp.py:105–107`). `_read`는 `readline()` 후 `json.loads(line.strip())`로 newline-delimited JSON을 읽음(`:122–131`). → MCP stdio 규격 정합. **N-1 해소.**
- **N-2 (tool-level 에러)**: `request()`가 JSON-RPC `error`뿐 아니라 `result.get("isError") is True`를 검사해 `NotionMcpError("MCP tool error: …")`로 표면화(`:94–97`). → property 불일치/권한 오류 시 원인 메시지 보존. **N-2 해소.**

### 실행 검증 (라이브 Notion 없이 로직 자체를 구동) ✅

`notion_mcp` 모듈을 단독 import 한 뒤, 임시 스크립트로 빌더·파서를 실제 실행했다(검증 후 스크립트 삭제):

- **모듈 단독 import 성공** → §9-V-0에서 막혔던 `import app.api.debate`의 `OPENSSL_3.3.0` 링크 오류는 **이 MCP 모듈과 무관**(무거운 FastAPI/모델 체인에서만 발생)함이 확정. notion_mcp 자체는 깨끗이 로드됨.
- **`_build_properties`**: 키가 정확히 `{Name, Session ID, Symbol, Category, Created At, Published At}`, `Name`은 `[삼성전자] financial debate` 형태, `Category="financial"` 확인.
- **`_build_markdown_content`**: `## Summary` / `## Key Points` / `### bull · opening` 섹션 생성 확인.
- **`_extract_page_fields` — 4가지 응답 형태 모두 추출 성공**:
  - `content[].text`에 **JSON 문자열**(`{"id":…,"url":…}`) → ✅
  - `content[].text`에 **산문 + Notion URL**(`Created page: https://www.notion.so/…`) → URL/UUID 정규식으로 ✅
  - `structuredContent.pages[]`의 **중첩 dict** → ✅
  - **bare dict**(`{id,url}`) → ✅
  - **음성 케이스**(링크 없음) → `(None, None)` 반환 확인(잘못된 추출 안 함).
- **newline 프레이밍 sanity**: 본문에 `\n`이 포함된 payload도 직렬화 결과에 **개행은 끝 1개뿐**(본문 개행은 `\\n`으로 이스케이프) → MCP "메시지 내 개행 금지" 요건 충족 확인.

> 즉 §10에서 "가장 가능성 높은 E2E 실패 모드"로 지목했던 **응답 파싱(V-3/N-1)이 실제 구동으로 통과**했다. 이건 컴파일이 아니라 동작 검증이다.

### 남은 항목 (업데이트)

- **N-3** stderr 미배수(verbose 서버에서 hang) — 미반영. 공식 서버는 로그를 stderr로 보내되 양이 많진 않아 졸프 시연에선 저위험. 여유 되면 stderr 동시 배수 스레드 1개 추가 권장.
- **N-4** 메시지당 timeout(전체 deadline 아님) — 미반영, 저위험.
- **N-8 (신규, 낮음)** `_read`의 **빈 줄 취약성은 해소**. 현재는 `readline` 루프에서 빈 줄을 skip하고 첫 비어 있지 않은 JSON 라인만 파싱한다. 다만 **비-JSON 일반 텍스트 stdout 라인**은 여전히 실패로 본다. 공식 서버는 stdout을 깨끗이 유지하므로 졸프 시연 리스크는 낮다.
- **V-4/V-5/V-6** — 설계상 운영 전제(락 점유·commit-후-크래시 중복·property 타입 정합), 졸프 범위 수용.

### 최종 판정

- **코드 레벨 E2E 준비도는 이번 라운드로 실질적으로 올라갔다.** N-1/N-2가 해소돼 공식 서버와 **핸드셰이크가 성립할 형식 요건**을 갖췄고, 응답 파싱은 **실행으로 검증**됐다.
- **이제 발행 성공과 코드 사이에 남은 것은 전적으로 운영 전제뿐**: ① Notion DB를 정확한 property 이름·타입으로 생성, ② 그 DB를 integration에 **연결(공유)**, ③ `@notionhq/notion-mcp-server` 사전 설치, ④ `.env` 주입. → **E2E 시도 green-light.**
- 평가 항목6(MCP 경유)은 그대로 충족. 첫 실서버 호출에서 만약 502가 나면, 이제는 N-2 덕분에 **Notion이 준 실제 오류 메시지가 그대로 올라오므로**(예: property 이름/타입 mismatch) 원인 추적이 쉬워졌다.

---

## 12. 실서버 E2E 성공 + 계약 정정 (Claude, 2026-06-09)

> **이 절이 §2·§4·§5·§6·§10의 "native `notion-create-pages` / `pages[].properties` / markdown `content`" 서술을 대체한다.** 실서버로 tool 목록을 직접 확인한 결과 그 계약이 틀렸고, 아래가 실제로 동작한 최종 계약이다.

### 12-1. 계약 정정 — npm 서버는 native가 아니라 OpenAPI(REST) 서버였다

로컬 설치한 `@notionhq/notion-mcp-server` **v2.2.1**에 `tools/list`를 직접 쳐 보니, 노출 tool이 `notion-create-pages`(native)가 아니라 **`API-*`(OpenAPI 생성형)** 였다:

- `serverInfo` = `{"name":"Notion API","version":"1.0.0"}`, tool 22종이 전부 `API-get-user` / `API-post-page` / `API-query-data-source` … 형태.
- 즉 **`notion-create-pages`는 호스티드(mcp.notion.com) 전용**이고, **self-host npm 서버는 Notion REST를 1:1 프록시**한다. (앞선 §10 N-0의 "native 정합" 판단은 오류였음 — 도구 문서가 호스티드 기준이었다.)

페이지 생성 tool의 실제 스키마(`API-post-page.inputSchema`)를 서버에서 직접 파싱해 확정한 계약:

| 요소 | 실제 값 |
|---|---|
| tool 이름 | **`API-post-page`** |
| `parent` | `{"database_id": "<uuid>"}` (스키마 `dataSourceIdParentRequest`의 필드명이 `database_id`, required) |
| `properties` | **Notion REST 타입드 객체** — `Name:{title:[…]}`, `Session ID/Symbol:{rich_text:[…]}`, `Category:{select:{name}}`, `Created At/Published At:{date:{start}}` |
| `children` | **block 객체 배열**, `blockObjectRequest = anyOf[paragraph, bulleted_list_item]` — **heading 불가**, `additionalProperties:false`라 `"object":"block"` 키 넣으면 거부 |
| rich text | `{"type":"text","text":{"content": ≤2000자}}`, 블록 ≤100개 |

### 12-2. 코드 정정 (모두 `notion_mcp.py` + `config.py` 안)

- `publish_debate.arguments` → `{parent:{database_id}, properties:<REST 타입드>, children:<블록>}` (native `pages[]`/`content` 제거).
- `_build_properties` → REST 타입드 객체 반환. `_build_markdown_content`(markdown 문자열) **삭제** → `_build_children`(paragraph/bulleted 블록, 2000자/100블록 청크) + `_rich_text`/`_paragraph`/`_bullet`/`_chunk_text` 헬퍼로 교체.
- `config.py` 기본 `notion_mcp_tool_name` → **`API-post-page`**.
- `.env`: `NOTION_MCP_SERVER_COMMAND`을 로컬 설치 바이너리 절대경로(`…/.notion-mcp/node_modules/.bin/notion-mcp-server`)로, `NOTION_MCP_SERVER_ARGS` 공백, `NOTION_MCP_TOOL_NAME=API-post-page`. (이 환경의 `npx`가 깨져 있어 바이너리 직접 실행으로 고정.)

검증: `OK_COMPILE` + 빌더 실행 테스트(properties 타입·children 블록 형태·`object` 키 부재·2000/100 한계·청크 동작 통과).

### 12-3. 실서버 E2E — 성공 ✅

`POST /api/debates/{session_id}/publish/notion` 실호출 결과:

- **1차 호출** → `200` + 실제 `notion_page_id` + `notion_page_url`(`app.notion.com/p/…`) + `notion_published_at` 반환. → **Notion에 실제 행(page) 생성 확인.**
- **2차 호출(동일 세션)** → `page_id`·`url`·`published_at`까지 **완전히 동일**하게 반환(타임스탬프 불변). → **멱등 경로(저장값 반환, 중복 생성 없음) 동작 확인.**

이 한 번의 성공으로 동시에 검증된 것:

| 검증 항목 | 근거 |
|---|---|
| stdio transport(N-1) | backend↔서버 newline-delimited 통신으로 page 생성 |
| `API-post-page` 계약(V-2 정정) | REST payload를 서버가 수락(`isError` 없음) |
| DB property 이름+타입 정합(V-6) | 6개 property 전부 통과 |
| children 블록 계약 | paragraph/bulleted 본문 수락 |
| 응답 파싱(V-3) | 실제 응답에서 `page_id`/`url` 추출 |
| 멱등성 + DB 영속화 | 2차 동일 반환, `notion_page_*` 저장 |

### 12-4. 최종 상태

- **#3 MCP 도입 = 실서버 E2E까지 성공.** 평가 항목6(실제 MCP 프로토콜 경로) 충족 + 실데모 가능 상태.
- 남은 저위험 항목은 변동 없음: **N-3**(stderr 동시 배수), **N-4**(전체 deadline), **V-4/V-5**(락 점유·commit-후-크래시 중복). 졸프 범위 수용.
- 후속(범위 밖): 프론트 "노션에 저장" 버튼 연결.
