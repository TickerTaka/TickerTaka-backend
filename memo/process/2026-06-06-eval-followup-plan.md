# 평가 대응 후속 계획 (2026-06-06)

## 목적

`memo/eval/evaluation_criteria.md`와 `memo/eval/BDAI_Pocat_Team2-fc3f2b7.md` 기준으로,  
이미 다른 팀원이 담당하기로 한 **RAGAS 정량평가**와 **sLLM + Langfuse**를 제외한 나머지 보완사항을 우선순위대로 정리한다.

이 문서는 **평가 점수 회복 + 시연 품질 개선 + 문서 완성도 확보**를 동시에 목표로 한다.

중요:
- **항목 8(RAGAS)** 는 가중치 ×2일 뿐 아니라 평가 리포트의 **등급 게이트**에 걸려 있다.
- 따라서 본 계획의 작업(문서/MCP/Docker/streaming/에러핸들링/RAG 고도화)만 완료해도 점수는 올라가지만, **RAGAS 트랙이 병합되지 않으면 등급 상한은 여전히 묶일 수 있다.**
- 즉 본 계획은 **단독 완료**가 아니라, 타 팀의 `RAGAS` 및 `sLLM(+vLLM/Ollama)+Langfuse` 트랙과 **동시 병합**을 전제로 한다.

## 전제

- **타 팀원 담당**
  - 항목 3: `sLLM 모델(≤300B) + 검증 Agent + langfuse`
  - 항목 8: `RAGAS 정량 평가 파이프라인`
  - 항목 7: `vLLM/Ollama/MLX 등 로컬 서빙`
- **현재 문서의 범위**
  - 항목 4: 5대 설계문서
  - 항목 6: MCP or A2A
  - 항목 5: Dockerise
  - 항목 10: 스트리밍 & 비동기 처리
  - 항목 9: RAG 고도화
  - 항목 2 일부: moderator SPOF / retry / fallback 보강

## 우선순위 요약

### P0. 5대 설계문서 완성

가장 먼저 닫아야 한다. 구현은 이미 상당 부분 진행되어 있어, **형식 있는 설계문서로 정리하는 작업**이 점수 대비 효율이 가장 높다.

대상 문서 5종:
- 유스케이스 명세서
- 컴포넌트 설계서
- 인터페이스 정의서
- 시퀀스 다이어그램
- ERD

권장 경로:
- `memo/design/` 신설 후 문서 5종 저장
- 필요 시 발표용 축약본은 `docs/` 또는 `memo/results/`에 별도 요약

기준 소스:
- ORM / Alembic: ERD 원천
- `app/api/*.py`: 인터페이스 정의 원천
- `app/agents/debate_graph.py`, `app/domain/*`: 시퀀스/컴포넌트 원천

닫힘 기준:
- 문서 5종 모두 존재
- 각 문서가 실제 코드 경로와 1:1로 대응
- 팀 외부 검토자가 문서만 읽고 전체 흐름을 설명할 수 있음

### P1. MCP 도입

평가 항목 6 대응용으로 **MCP를 가장 빨리 실증 가능한 방향**으로 붙인다.

추천 방향:
- **Notion MCP 도입**
- 저장 대상:
  - 토론 세션 요약
  - Bull/Bear 핵심 주장
  - moderator summary
  - 근거 링크
  - 평가/회고 메모

이유:
- 발표/시연에서 눈에 보이는 결과물이 좋음
- “토론 결과 외부 협업 툴 저장”이 명확해서 평가 설명이 쉬움
- **사용자가 원하는 토론만 저장**하게 만들 수 있어 데이터 위생이 좋음
- 버튼 클릭 = MCP 호출이라 시연 동선이 직관적임

권장 정책:
- **시스템 SOT는 계속 PostgreSQL**
- Notion은 **공유/아카이브/보고용 2차 저장소**

즉:
- `debate_sessions`, `statements`, `evidence`는 기존 DB 유지
- MCP는 **사용자 요청 시** 기존 토론 결과를 Notion database row(page)로 mirror

이유:
- Notion을 운영 DB처럼 쓰면 검색/정합성/트랜잭션이 약함
- 평가 대응과 시연 목적에는 “MCP를 통한 외부 협업 시스템 연동”이면 충분

최소 구현안:
- **토론 상세 화면의 `노션에 저장` 버튼**에서 발행
- 엔드포인트 예시: `POST /api/debates/{session_id}/publish/notion`
- 성공 시 응답: `notion_page_url`
- 저장 대상:
  - 속성(DB property): `session_id`, `symbol`, `category`, `created_at`, `notion_published_at`
  - 본문(block): `summary_content`, `key_points`, 주요 statements, 근거 링크

주의:
- **Notion REST API 직접 호출만 붙이면 평가 항목 6(MCP)로 인정받기 어렵다.**
- 따라서 이번 단계의 닫힘 기준은 단순 “외부 저장 성공”이 아니라, **실제 MCP 프로토콜이 호출 경로에 존재하는지**까지 포함해야 한다.
- 또한 이 대화에서 사용하는 MCP 도구는 개발 보조용일 뿐, **FastAPI가 직접 호출할 수 있는 런타임 경로가 아니다.**
- 실제 구현 본체는 **백엔드가 MCP client가 되어 Notion MCP server와 세션을 맺는 배선**이다.

권장 구현 2안:
1. **Notion MCP 서버 사용**
   - backend가 MCP client로 Notion MCP 서버를 호출
   - debate 결과를 **Notion 데이터베이스의 row(page)** 로 생성
   - 속성은 DB 컬럼으로, 요약/발언/근거는 페이지 본문 block으로 저장
2. **프로젝트 도구를 MCP로 노출**
   - `fetch_price_context`, `fetch_news_context`, `fetch_filing_context`, `publish_debate_result` 같은 도구를 MCP server로 노출
   - agent 또는 orchestration layer가 MCP client로 사용

이번 단계 권장 결론:
- **시연 가시성은 Notion MCP가 가장 좋다**
- 다만 구현 문서에 **“REST 직접연동이 아니라 MCP 프로토콜 경유”**를 명시해야 한다
- UX는 **자동 발행보다 버튼 기반 온디맨드 발행**이 더 적절하다

대안:
- Slack/Discord MCP: 시연은 쉬우나 구조 저장이 약함
- Google Sheets MCP: 관리성은 좋으나 협업 문맥이 약함
- A2A: 현재 범위 대비 구현량이 커서 MCP보다 비효율

결론:
- **이번 단계는 Notion MCP가 가장 적절**

닫힘 기준:
- MCP 서버 연결 문서화
- MCP client → MCP server 호출 경로가 실제 코드에 존재
- **토론 상세에서 사용자가 버튼을 눌렀을 때** Notion DB에 row(page) 생성
- `debate_session` 또는 별도 publish 레코드에 `notion_page_id` / `notion_page_url` / `notion_published_at` 저장
- 이미 발행된 세션은 **중복 생성 대신 기존 URL 반환 또는 update** (멱등성)
- 실패 시 본체 토론 API는 깨지지 않음 (fail-soft), 프론트는 재시도 가능

착수 전 운영 보완(누락 보강 — 실제 구현 시 막히는 지점):
1. **Notion 사전 프로비저닝**: "토론 기록" 데이터베이스를 1회 수동 생성 → Notion **integration에 공유**(권한 부여) → `database_id`(필요 시 `data_source_id`)와 통합 토큰을 **설정/env로 주입**(`NOTION_TOKEN`, `NOTION_DATABASE_ID`). 이 사전작업이 없으면 row 생성이 401/404로 실패한다. 코드가 DB를 자동 생성하지 않는다는 전제를 명시.
2. **MCP 전송/클라이언트 선택**: 백엔드(FastAPI, Python)는 `mcp` 파이썬 SDK의 **client**로 Notion MCP server에 접속한다. 전송은 **stdio(서버 프로세스 spawn)** 또는 **streamable-http** 중 택1 — 졸프 로컬 기준 stdio가 간단. 서버 기동/세션 수명(연결→`tools/list`→`tools/call`→종료) 관리 위치를 `app/integrations/notion_mcp.py`(가칭)로 단일화. 의존성은 `==` 핀([[feedback_requirements_pinning]]).
3. **스키마 변경 = Alembic 마이그레이션 필요**: `notion_page_id` / `notion_page_url` / `notion_published_at` 3컬럼 추가는 모델 수정만으로 끝나지 않고 **신규 Alembic revision**이 필요(기존 `alembic/versions/20260607_add_debate_eval_result.py` 패턴 따름). 닫힘 기준에 "마이그레이션 생성·적용"을 포함.
4. **Notion 블록 제약 → 청크 분할**: Notion API는 **rich text 블록당 약 2000자, 요청당 100블록** 제한이 있다. 긴 `summary_content`/`statements`는 블록 단위로 잘라 넣어야 하며, 그렇지 않으면 400. 본문 매핑 로직에 분할 처리 명시.
5. **멱등성 동시클릭 가드(선택)**: 더블클릭 동시요청 시 페이지 2개 생성 가능. 발행 직전 `notion_page_id` **재조회 후 분기**로 충분하나, 더 단단히 하려면 기존 **RuntimeGuard(SET NX EX) 단일비행 락 패턴 재사용** 가능. 졸프 범위에선 재조회 분기로 충분.

## P1. 에러 핸들링 보강

평가 리포트에서 지적된 **moderator SPOF**를 줄인다.

중요:
- `llm_factory` 레벨의 `max_retries` / `timeout`은 이미 일부 존재한다.
- 이번 보완의 핵심은 **retry 추가** 자체보다, `moderator_node`의 LLM 호출 예외가 그래프 전체를 바로 죽이지 않도록 **예외 격리 + fallback summary/graceful degradation**을 넣는 것이다.

작업:
- `moderator_node._call()`에 try/except 추가
- fallback 시
  - 최종 summary로 우회
  - 또는 검증 실패를 soft-fail 처리
- bull/bear/moderator 공통 LLM 호출 래퍼 정리
- MCP/Notion publish 실패는 API 실패로 전파하지 않음

닫힘 기준:
- moderator 호출 실패 시 토론 전체가 즉시 500/503으로 죽지 않음
- fallback 또는 graceful summary 반환
- MCP publish 실패가 debate success 경로를 깨지 않음
- watchdog/telemetry류 부가 실패는 warning 로그로만 남고 핵심 API 성공 경로를 깨지 않음

## P1. Dockerise 완성

현재 compose는 인프라 위주다. 평가 항목 5를 닫으려면 **앱 컨테이너 빌드/실행**까지 포함해야 한다.

필수 작업:
- `Dockerfile` 추가
- FastAPI app service를 `docker-compose.yml`에 추가
- `depends_on` + healthcheck 정리
- env 주입 경로 정리 (`.env.example` 기준)

권장 범위:
- backend app
- redis
- chroma

환경 주의:
- **PostgreSQL은 현재 NCP 원격 인스턴스가 단일 SOT**다.
- 따라서 Docker 트랙의 목표는 "모든 인프라를 compose 안에 넣기"가 아니라, **app 컨테이너가 NCP Postgres + 로컬 Redis/Chroma와 함께 정상 기동**하는 것이다.
- compose의 `postgres` 서비스는 로컬 대체용 참고 리소스로 둘 수는 있지만, **평가용 기본 경로에서는 app이 의존하지 않는다.**
- app 컨테이너 env는 다음을 기본으로 한다.
  - `DATABASE_URL=<NCP 그대로>`
  - `REDIS_URL=redis://redis:6379/0`
  - `CHROMA_URL=http://chroma:8000`
- `.env.example`의 localhost/포트 설명도 이 정책과 모순 없게 정리한다.

선택:
- frontend는 후순위

닫힘 기준:
- `docker compose up --build`로 backend 기동
- app service는 `depends_on`을 `redis`, `chroma`에만 둠
- Chroma healthcheck가 compose에 존재
- app 컨테이너에서 NCP Postgres 실연결 확인
- `/health` 응답 확인
- 토론 API / watchlist API 최소 smoke 확인

## P2. 스트리밍 & 비동기 처리

평가 항목 10 대응용. 지금은 LangGraph 내부 `astream()`이 있어도 HTTP 레벨 스트리밍이 없다.

작업:
- `/api/debates/stream` 또는 기존 debate API에 SSE 경로 추가
- LangGraph `.astream()` 결과를 `EventSourceResponse`로 연결
- 프론트는 토론 진행 중 statement를 순차적으로 표시

구현 메모:
- 현재 `debate_service`에는 이미 `astream` 루프가 존재한다.
- 따라서 이번 작업은 "astream 신규 구축"이 아니라 **기존 청크를 HTTP로 forward**하는 방향으로 간다.
- 다만 현재 `run_session()`은 내부 소비형이므로, **streaming 전용 generator 메서드 분리 또는 `run_session` 제너레이터화**가 필요하다.

병렬화:
- `data_node` 내부 외부 fetch를 `asyncio.gather`로 묶기
- 가능하면 quote/news/financial/filing retrieval을 병렬화

범위 메모:
- 이번 단계의 목표는 **statement 단위 점진 표시**다.
- bull/bear/moderator 일부 노드는 아직 sync `def` 기반이라 토큰 단위 스트리밍/완전 비동기화까지는 범위 밖으로 둔다.
- 진정한 동시성 고도화(`ainvoke` 전환)는 후속 품질 향상으로 분리 가능하다.
- 현재 `data_node`의 quote/news/financial/filing fetch는 async라 `gather` 대상이 될 수 있지만, **evidence retrieval은 sync 경로가 남아 있으므로 그대로 묶지 말고 `asyncio.to_thread`로 분리하거나 별도 단계로 유지**한다.
- 스트리밍 진입점은 **중복 토론 실행**이 나지 않도록 설계 결정을 먼저 고정한다.
  - 권장: `POST /api/debates`는 세션 생성/202만 담당하고, 실제 실행/청크 방출은 SSE 경로가 단일 책임
  - 대안: 기존 POST 실행 경로 유지 시 active-session lock으로 이중 실행 차단

주의:
- 이 단계는 “실제 사용자 체감”이 커서 시연 가치가 높다
- 다만 P0 문서/MCP보다 먼저 잡을 필요는 없음

닫힘 기준:
- 토론 시작 후 statement가 점진적으로 화면/CLI에 흘러옴
- `data_node` fetch 병렬화로 latency 감소
- 동일 `session_id`에 대해 create 경로와 stream 경로가 중복 실행되지 않음

## P2. RAG 고도화

평가 항목 9는 현재 “기반은 있으나 실연결 미흡” 상태다.

우선순위:
1. BM25 + vector hybrid
2. RRF fusion
3. reranker

권장 구현 순서:
- 1차: `rank-bm25` 실제 검색 경로 연결
- 2차: news/filing retrieval에서 hybrid top-k merge
- 3차: reranker 추가

참고:
- `sentence-transformers`가 이미 설치되어 있어, **reranker는 신규 의존성 추가 없이** `CrossEncoder` 계열로 연결 가능하다.
- 현재 vector retrieval score는 Chroma distance 기반이라 **낮을수록 더 유사**하다.
- 반면 BM25/reranker score는 **높을수록 더 관련성 높음**이므로, raw score를 그대로 더하는 방식은 금지한다.
- hybrid 융합은 **RRF(rank-based fusion)** 를 기본으로 하고, distance/score의 직접 합산은 하지 않는다.

주의:
- 이건 점수도 중요하지만, 시연 품질과 답변 신뢰도에도 직접 영향
- 다만 설계문서/MCP/Docker/streaming보다 후순위

닫힘 기준:
- retrieval path에 BM25가 실제 사용됨
- hybrid 검색 결과가 로그/검증 스크립트에서 확인됨
- hybrid 랭킹은 RRF 기반으로 동작하고, 기존 Chroma distance 정렬 의미를 깨지 않음

## 팀 분업 제안

### 트랙 A. 문서/아키텍처

담당:
- 5대 설계문서 5종 작성
- 코드 참조 링크 정리
- 발표용 도식 정리

산출물:
- `memo/design/*.md`
- 시퀀스/ERD mermaid

### 트랙 B. MCP / 외부 연동

담당:
- Notion MCP 연결
- debate result publish flow 구현
- 실패 시 fail-soft 처리
- MCP 프로토콜 경유 여부 검증

산출물:
- `app/integrations/` 또는 `app/domain/notion_publish.py`
- MCP 설정 가이드 문서
- `requirements.txt`에 MCP 관련 의존성 추가 (`==` 버전 핀)

### 트랙 C. 런타임 / 시연 품질

담당:
- moderator 예외격리 / fallback
- Dockerfile
- compose app service
- SSE streaming
- gather 기반 병렬화

산출물:
- `app/agents/nodes/moderator_node.py` 수정
- `Dockerfile`
- compose 수정
- debate streaming endpoint

### 별도 트랙 (타 팀원)

- RAGAS 정량평가 Agent
- sLLM + Langfuse
- vLLM/Ollama/MLX 로컬 서빙
- `model_used` 메타데이터를 실제 env 모델명과 일치시키는 수정

## 권장 일정

### Day 1

- 설계문서 구조 확정
- ERD / 인터페이스 정의서 / 컴포넌트 설계서 초안
- Notion MCP 연결 PoC
- moderator 예외격리/fallback 초안

### Day 2

- 유스케이스 명세서 / 시퀀스 다이어그램 마무리
- 토론 결과 Notion publish 연결
- Dockerfile 초안
- 문서 5종 모두 존재하도록 빈칸 없이 초안 완성

### Day 3

- compose app service 기동
- SSE streaming 도입
- MCP fail-soft / publish fallback 반영

### Day 4

- hybrid retrieval 1차 연결
- 문서 정리 / 시연 스크립트 / 평가 대응 체크리스트 작성
- 타 팀의 RAGAS / sLLM+vLLM 트랙 병합 체크

## 최종 우선순위 결정

이번 남은 작업의 우선순위는 아래 순서로 고정한다.

1. **5대 설계문서**
2. **에러 핸들링 보강**
3. **MCP 도입 (Notion 우선, 단 MCP 프로토콜 경유 필수)**
4. **Dockerise**
5. **스트리밍 & 비동기 처리**
6. **RAG 고도화**

주의:
- 아래 고정순서가 `P0/P1/P2` 라벨보다 우선한다.
- 즉 섹션 라벨은 묶음용이고, 실제 착수 순서는 위 1~6을 따른다.

RAGAS와 sLLM은 다른 팀원 트랙으로 진행하되,  
문서/MCP/Docker/streaming이 먼저 정리되어야 전체 프로젝트 완성도가 빠르게 올라간다.

## 메모

- MCP는 평가용 “도입 여부”보다 **실제 시연 가능성 + 실제 MCP 프로토콜 사용 여부**가 중요하다.
- Notion을 메인 저장소로 바꾸지 말고, **PostgreSQL -> Notion mirror** 구조를 유지한다.
- 5대 설계문서는 새로 설계하는 작업이 아니라, **이미 구현된 구조를 형식화하는 작업**으로 본다.
- 5대 설계문서는 **5종 전부** 있어야 의미가 있다. 일부만 작성하면 평가 리포트의 항목4 캡이 유지될 수 있다.
- 항목2는 ×2 배점이므로, 구현량 대비 득점 효율이 매우 높다. 문서 다음 우선순위로 올리는 것이 맞다.

---

# 검증 결과 (2026-06-06, 코드 대조)

리포트(`fc3f2b7`)와 본 계획의 전제를 실제 코드(`@fc3f2b7` 기준 작업 트리)와 직접 대조했다.
검증 도구: `grep`/`ls`/소스 직접 확인. 아래는 **계획을 그대로 실행해도 되는 부분 / 보정이 필요한 부분 / 누락**으로 나눈다.

## A. 전제 확인 — 계획대로 진행해도 되는 항목 (코드 일치)

| 항목 | 리포트 주장 | 코드 확인 | 결론 |
|---|---|---|---|
| 5 Docker | 앱 Dockerfile 부재, compose는 인프라뿐 | `Dockerfile` 없음, `docker-compose.yml` 서비스 = postgres/redis/chroma만, `build:` 없음 | ✅ 정확 |
| 6 MCP | 선언조차 없음 | `app/`·`requirements.txt`에 mcp/notion/anthropic 흔적 0 | ✅ 정확 (단 C-1 리스크 참조) |
| 10 스트리밍 | SSE 미연결, 비동기 병렬화 없음 | `sse-starlette==2.1.3` 설치됐으나 `EventSourceResponse`/`text/event-stream` 사용 0, `asyncio.gather` 0, `data_node.py:25–29` 5개 fetch 순차 `await` | ✅ 정확 |
| 4 설계문서 | 5종 전부 부재 | `memo/`·`docs/`에 `sequenceDiagram`/`erDiagram` = 평가 리포트 자신뿐 | ✅ 정확 |
| 9 RAG | BM25 설치만, 미연결 | `rank-bm25==0.2.2` 설치, `app/`에서 import 0 | ✅ 정확 |

→ 위 5개에 대한 계획의 **작업 방향(Dockerfile 추가, SSE+gather, 5종 문서화, BM25 실연결)은 유효**하다.

## B. 부정확·과장 지점 — 계획 문구 보정 필요

**B-1. "moderator retry 부재" → 부분적으로 틀림.**
`app/core/llm_factory.py`의 `ChatOpenAI(... max_retries=3, timeout=60)`로 **재시도·타임아웃은 이미 클라이언트 레벨에 존재**한다. 진짜 공백은 `moderator_node.py:24-26 _call()`이 `llm.invoke(...)`를 **try/except 없이 그대로 노출**한다는 점(3회 재시도 소진 후 예외가 그래프로 전파 → SPOF). 따라서 계획 P1의 "retry/timeout 추가"는 일부 **이미 있는 것의 중복**이다. 실제 작업은 **`_call()` 예외 격리 + graceful fallback summary 반환**으로 좁혀 적어야 한다. (참고: `moderator_node.py`의 try/except는 line 30 `_parse`(JSON), line 137 DB upsert용일 뿐, LLM 호출용이 아님 — 리포트의 SPOF 지적 자체는 맞음.)

**B-2. 모델은 하드코딩이 아니라 env 설정값.**
리포트는 "전 노드 gpt-4o-mini"라 했으나, `app/config.py:37-40`에서 `bull_model`/`bear_model`/`moderator_model`/`fallback_model`은 모두 `BULL_MODEL` 등 **env alias로 주입 가능**(default만 gpt-4o-mini)하고 `openrouter_base_url`도 잔존한다. 즉 항목3 sLLM 전환은 (타 팀 트랙이지만) "코드 대수술"이 아니라 **factory의 base_url 재배선 + env 교체** 수준. 계획 전제에 반영하면 타 팀과의 인터페이스가 명확해진다.
단, 함정: `moderator_node.py:94,178,193`·`bull_node.py:77`·`bear_node.py:82`에 DB/state로 기록되는 `"model_used": "gpt-4o-mini"`가 **문자열 하드코딩**돼 있다. env로 모델을 바꿔도 이 메타데이터는 여전히 "gpt-4o-mini"로 거짓 기록 → sLLM 사용 근거(항목3/langfuse 트레이스)와 모순. 타 팀 트랙에 "`model_used`를 `settings.*_model`로 치환"을 함께 넘길 것.

**B-3. reranker 추가 의존성 불필요.**
`requirements.txt`에 `sentence-transformers==3.2.1`이 **이미 설치**돼 있다 → 항목9의 CrossEncoder reranker는 신규 패키지 없이 가능. 계획 P2의 reranker 단계를 "의존성 추가 없음"으로 명시 가능.

**B-4. (사소) 리포트 경로 오기.**
리포트가 인용한 `config.py:37-40`/`llm_factory.py:38-44`의 실제 경로는 `app/config.py`, `app/core/llm_factory.py`다. 작업 지시 시 혼동 방지용으로만 기록.

## C. 누락·공백 — 계획에 추가해야 할 것

**C-1. [중대] MCP 정의 리스크 — Notion MCP가 "단순 API 연동"으로 변질될 위험.**
평가 항목6은 "**MCP** or A2A 사용"이다. 계획대로 백엔드가 Notion에 결과를 publish할 때, 구현이 **Notion REST API 직접 호출**로 빠지면 그것은 MCP가 아니라 일반 API 연동이며 **항목6 충족이 안 된다**. MCP로 인정받으려면 실제 MCP 프로토콜(서버↔클라이언트)이 루프에 있어야 한다. 
→ 더 방어적인 대안 검토 권고: **프로젝트 자체 data-fetch tool(`fetch_price_context`/`fetch_news_context`/`fetch_filing_context` 등)을 MCP 서버로 노출하고, agent가 MCP 클라이언트로 소비**하는 구조. 이 편이 (a) multi-agent 구조(항목1)에 통합되어 평가 설명이 강하고, (b) "선언≠실동작" 지적을 정면으로 반박한다. Notion mirror는 시연 가시성은 좋지만 "MCP를 정말 쓰는가"라는 질문에 약하다. 계획에 **"MCP 프로토콜이 실제 호출 경로에 있는지"를 닫힘 기준에 명시**해야 한다.

**C-2. 항목2(×2)가 우선순위 너무 낮음 — 배점 레버리지 역전.**
배점은 `1·2·3·8 = ×2`, 나머지 ×1이다. 항목2는 현재 3/5 ×2 → 만점 시 **+4 가중점**, 작업량은 B-1대로 `_call` 예외격리+fallback **1시간 미만**. 반면 5대 문서(항목4, ×1)는 1→5 = +4인데 작업량이 훨씬 크다. **항목2는 "가장 싼 ×2 득점"**인데 계획에서 P1의 사실상 마지막(5순위)에 놓였다. → **에러 핸들링을 P0~P1 상단으로 상향** 권고.

**C-3. 항목7(vLLM, ×1) 소유자 공백.**
계획의 "현재 문서 범위"(4·6·5·10·9·2)와 "타 팀 트랙"(3·8) 어디에도 **항목7이 없다**. 현재 0점(×1, 만점 시 +5). 항목7은 항목3(sLLM)과 천연 한 쌍이다 — **타 팀이 sLLM을 Ollama/vLLM 로컬 서빙으로 올리면 3·7 동시 충족**. 계획에 "항목7은 타 팀 sLLM 트랙과 묶어 Ollama/vLLM 서빙으로 커버"라고 **명시적 귀속**을 추가해야 그냥 드롭(=−5 손실)을 막는다.

**C-4. [중대] GATE 의존성 미강조 — 본 작업만으로는 등급 천장을 못 올린다.**
리포트의 ⚠GATE: **항목8 RAGAS=0 → 등급 상한 B**. RAGAS는 타 팀 트랙이라 본 계획 범위 밖이지만, 그 결과 **본 후속작업(4·5·6·9·10·2)을 전부 만점 내도 RAGAS가 0이면 등급은 여전히 상한에 묶인다**(percent는 오르지만). → 계획 서두/전제에 **"RAGAS 트랙 랜딩이 등급 상승의 필수 전제이며, 본 트랙과 동시 진행·병합되어야 함"**을 명시. 일정(Day1~4)에도 RAGAS 트랙과의 합류 시점을 표기 권고.

**C-5. 항목4 캡 해제 조건 재확인.**
리포트는 항목4를 "누락 상한 캡 1"로 처리했다 — **5종 중 일부만 작성하면 캡(1점)이 유지될 위험**. 계획이 "문서 5종 모두 존재"를 닫힘 기준에 둔 것은 옳으나, **"3/5만 완성 = 여전히 캡"**이라는 점을 닫힘 기준에 굵게 강조해, Day1~2에 5종을 동시 마감하도록 못박을 것.

## D. 배점 레버리지 기준 권장 우선순위 (재정렬안)

| 순위 | 항목 | 현재→만점 | 가중 | 가중점 이득 | 작업량 | 비고 |
|---|---|---|---|---|---|---|
| 1 | 4 설계문서 | 1→5 | ×1 | **+4** | 중 | 캡 해제, 코드 형식화라 확실 |
| 2 | 2 에러핸들링 | 3→5 | **×2** | **+4** | **소** | 가장 싼 ×2 (B-1) |
| 3 | 6 MCP | 0→5 | ×1 | +5 | 중 | 단 C-1 리스크 해소 전제 |
| 4 | 5 Docker | 2→5 | ×1 | +3 | 중 | |
| 5 | 10 스트리밍 | 2→5 | ×1 | +3 | 중 | 시연가치 높음 |
| 6 | 9 RAG | 2→5 | ×1 | +3 | 중 | 의존성 이미 있음(B-3) |
| (타 팀) | 8 RAGAS | 0→5 | ×2 | +10 + **게이트 해제** | — | C-4: 최우선 합류 대상 |
| (타 팀) | 3·7 | 2/0→5 | ×2/×1 | — | — | C-3: 3+7 묶음 |

> 기존 계획의 1·2순위(문서·MCP)는 유지하되, **항목2(에러핸들링)를 문서 직후(2순위)로 끌어올리는 것**이 배점 효율상 합리적이다. 나머지(Docker→streaming→RAG)는 기존 순서 유지 타당.

## E. 결론

- 계획의 **작업 방향·기술 선택은 전반적으로 타당하고, 리포트의 사실 주장도 대부분 코드와 일치**한다(섹션 A).
- 다만 **반드시 보정할 4가지**: ① MCP가 진짜 MCP인지 닫힘 기준 명시(C-1), ② 항목2 우선순위 상향(C-2), ③ 항목7 소유자 명시(C-3), ④ RAGAS GATE 의존성 강조(C-4).
- **문구 수정 2가지**: moderator는 "retry 추가"가 아니라 "예외격리+fallback"(B-1), reranker는 "의존성 추가 없음"(B-3).

---

# 2차 검증 결과 (2026-06-06, 수정본 대조)

1차 검증 후 수정된 계획 본문(L1–306)을 다시 코드와 대조했다. 결론부터: **1차 지적은 전부 반영됐고, 새로 추가된 닫힘 기준도 코드와 정합한다.** 남은 건 미세한 정합 이슈(H절)뿐이다.

## F. 1차 지적 반영 확인

| 1차 항목 | 반영 위치 | 상태 |
|---|---|---|
| C-1 MCP가 진짜 MCP인지 | L90–104 주의/2안, L116 닫힘기준 "MCP client→server 호출 경로가 실제 코드에 존재" | ✅ 반영 |
| C-2 항목2 우선순위 상향 | L120 P1 최상단 배치, L291 최종순서 #2, L306 메모 | ✅ 반영 |
| C-3 항목7 소유자 | L20 타 팀원 담당, L255 별도 트랙 | ✅ 반영 |
| C-4 RAGAS GATE | L10–13 "중요" 블록, L284 Day4 병합 체크 | ✅ 반영 |
| C-5 항목4 캡(5종 전부) | L272 Day2 "빈칸 없이", L305 메모 | ✅ 반영 |
| B-1 retry→예외격리 | L124–126, L129 작업 | ✅ 반영 |
| B-2 model_used 하드코딩 | L256 별도 트랙 | ✅ 반영 |
| B-3 reranker 의존성 없음 | L200–201 | ✅ 반영 |

## G. 신규 닫힘 기준 — 코드 검증 (새로 확인)

| 닫힘 기준 / 전제 | 코드 확인 | 결론 |
|---|---|---|
| Docker "`/health` 응답 확인"(L162) | `app/main.py:30` `@app.get("/health")` 실존 | ✅ 충족 가능 |
| 문서 원천 `app/domain/*`(L49) | `app/domain/` 실존(debate_service, evidence_retrieval, financial_ratios 등) | ✅ 유효 |
| MCP 도구 후보 `fetch_price_context` 등(L99) | `data_node.py` import에서 실존 확인 | ✅ 유효 |
| 스트리밍 "내부 astream 있어도 HTTP 미연결"(L167) | `debate_service.py:58` `async for chunk in _astream_with_config(...)` 로 **이미 astream 사용 중**, 단 청크를 DB 저장에만 소비하고 `create_debate`(`debate.py:37,61`)는 `run_session` 완료 후 일괄 return | ✅ 정확 (아래 G-주석) |

**G-주석 (스트리밍 트랙에 호재):** astream 루프가 이미 `debate_service.py:58`·`_astream_with_config()`(L113)에 **구축돼 있다**. 따라서 SSE 작업은 "astream 신규 구축"이 아니라 **기존 청크를 HTTP로 forward**하는 것 — 계획이 추정한 것보다 가볍다. 단 현재 `run_session`은 청크를 내부에서 소비+DB저장하는 구조이므로, 엔드포인트가 청크를 `yield`하려면 **`run_session`을 제너레이터화(또는 streaming 변형 메서드 분리)** 가 필요하다. "단순히 `EventSourceResponse`만 추가"보다 한 단계 더 있다는 점만 닫힘 기준에 반영하면 정확하다.

## H. 수정본에서 새로 보이는 미세 이슈 (선택 보정)

**H-1. P-라벨 vs 최종순서 불일치.**
섹션 라벨은 MCP=`P0`(L56), 에러핸들링=`P1`(L120)인데, 최종 고정순서(L288–295)는 **에러핸들링(#2) > MCP(#3)**. 라벨과 순서가 어긋난다. → 에러핸들링을 `P0`로 재라벨하거나, 최종순서 주석에 "P-라벨보다 이 고정순서가 우선"이라고 한 줄 달면 해소.

**H-2. MCP 신규 의존성 핀 누락.**
MCP를 실제로 쓰려면 MCP 클라이언트/서버 라이브러리(예: `mcp` 파이썬 패키지)를 `requirements.txt`에 추가해야 한다. 현재 requirements에 mcp 계열 0건. 팀 정책상 **`==`로 버전 핀**([[feedback_requirements_pinning]]) 필수 — 트랙 B 산출물에 "의존성 추가(핀 고정)"를 명시할 것.

**H-3. 스트리밍 노드 동기성.**
`bull_agent_node`/`bear_agent_node`/`moderator_pre_node`/`moderator_check_node`는 **sync `def`**(LLM 호출이 이벤트루프 블록), `data_agent_node`/`moderator_summary_node`만 `async def`. astream은 **노드 완료 단위로 청크를 방출**하므로 statement 단위 점진 표시는 현행으로도 가능하나, sync 노드는 LLM 호출 중 루프를 블록한다. 진정한 동시성/토큰단위 스트리밍을 원하면 bull/bear 노드의 `ainvoke` 전환이 필요(데모 목적이면 현행으로 충분 — 닫힘 기준 "statement 점진 표시"는 만족). 계획에 "동시성까지는 범위 밖"임을 한 줄 명시 권장.

## I. 결론

- **수정본은 1차 지적(C-1~5, B-1~3)을 전부 반영**했고(F절), **신규 닫힘 기준도 코드와 정합**(`/health`·`app/domain`·astream 실존, G절)하다.
- 스트리밍은 오히려 **이미 있는 astream 루프 재활용**이라 계획보다 쉬우나, `run_session` 제너레이터화 한 단계가 추가됨(G-주석).
- 남은 건 실행에 지장 없는 미세 정합 3건(H-1 P-라벨, H-2 MCP 의존성 핀, H-3 노드 동기성)뿐. **이 계획서는 그대로 착수 가능한 상태**로 판단한다.

---

# 3차 검증 결과 (2026-06-06, 설계 근거·트레이드오프·전체 코드 리뷰·리스크)

이번 라운드는 (1) 2차 지적(H-1~3) 반영 확인, (2) **기술 스택/설계 선택의 근거·트레이드오프·대안 비교**, (3) **전체 코드 정독 기반 계획↔코드 충돌·리스크** 도출에 집중한다. 정독 범위: `debate_graph.py`·`debate_service.py`·`evidence_retrieval.py`·`debate_runtime_guard.py`·`debate_checkpoint.py`·`bull_node.py`·`docker-compose.yml`·`config.py`·`.env.example`·`chroma_client.py`.

## J. 2차 지적(H절) 반영 확인

| 2차 항목 | 반영 위치 | 상태 |
|---|---|---|
| H-1 P-라벨 vs 최종순서 | L308–310 "고정순서가 P 라벨보다 우선" 명시 | ✅ |
| H-2 MCP 의존성 핀 | L245 "requirements.txt에 MCP 의존성 추가(`==` 핀)" | ✅ |
| H-3 노드 동기성 | L183–186 "statement 단위까지만, ainvoke 전환은 범위 밖" | ✅ |

→ 본문은 1·2차 지적을 모두 흡수했다. 이하 K~N은 **새 추가 내용**이다.

## K. 설계 근거 · 트레이드오프 · 대안 비교

### K-0. 기존 아키텍처가 이미 채택한 설계 (코드에서 역추론 — 문서화 시 그대로 근거로 쓸 것)

| 설계 결정 | 코드 근거 | 채택 이유 | 트레이드오프 / 대안 |
|---|---|---|---|
| **LangGraph `StateGraph` + 조건부 엣지** | `debate_graph.py:50–68`, `_router`(L22–47) | 토론의 분기(개입→재발언 / 환각2회→강제종료 / 주제완료→요약)를 **선언적 상태머신**으로 표현 | (+) 흐름 가시성·체크포인트 친화 (−) 러닝커브·디버깅 난이도. 대안: 수동 while-FSM(단순하나 분기 폭증 시 가독성↓), CrewAI/AutoGen(추상화↑·제어력↓) |
| **bull/bear = `create_react_agent`(ReAct+`search_evidence` 툴), moderator = 평문 LLM** | `bull_node.py:48–58`, `moderator_node.py:24–26` | 토론자는 **근거 탐색(tool)** 이 필요, 사회자는 판정·요약이라 도구 불필요 → 역할별 이질성 | (+) 에이전트 이질성(항목1 가점) (−) 동일 모델이라 "관점 다양성"은 프롬프트 의존. 대안: bull/bear에 서로 다른 모델(항목3 sLLM과 결합 시 진짜 이질성) |
| **moderator만 `cached=True`, bull/bear `cached=False`** | `bull_node.py:49`, `llm_factory.py` | ReAct는 다단계·비결정적이라 캐시 적중률 낮음 → 결정적 moderator 호출만 캐시 | (+) 비용 절감 (−) 동일 입력 토론 재현성 제한 |
| **Redis 체크포인트(매 노드, 24h TTL, fail-soft)** | `debate_service.py:62`, `debate_checkpoint.py:20–33` | 중단 지점 재개(`load_checkpoint`) → 복원력 | (+) 장애 복원 (−) 매 노드 `setex` I/O. 대안: LangGraph 내장 checkpointer(SqliteSaver 등)로 교체 시 표준화 가능 |
| **런타임 가드 fail-open + `SET NX EX` 단일비행 락** | `debate_runtime_guard.py:67–93` | Redis 장애 시 **토론 허용(가용성 우선)**, 동일 user/symbol 동시 토론은 분산락으로 차단 | (+) 졸프/시연 가용성 (−) Redis 다운 시 rate-limit 무력화, 락 TTL(1800s) 초과 토론은 중복 가능. 운영 전환 시 fail-closed 검토 |

> 5대 설계문서(트랙 A)는 위 표를 **"설계 의도"** 섹션으로 그대로 옮기면 "왜 이렇게 설계했나"에 답이 된다. 평가에서 "선언≠실동작" 반박 + 설계 성숙도 동시 어필.

### K-1. MCP — Notion mirror vs 자체 도구 노출 vs A2A

| 방식 | 구현량 | 시연 가시성 | "진짜 MCP" 방어력 | 비고 |
|---|---|---|---|---|
| **A. Notion MCP mirror** (계획 1안) | 중 | **높음**(외부 협업툴에 결과) | 중 — MCP client→server 경로가 코드에 실재해야 인정. REST 직접호출로 새면 탈락 | 발표 임팩트 최고 |
| **B. 자체 도구를 MCP 서버로 노출** (`fetch_*`/`publish_debate_result`) | 중 | 낮음(내부 호출) | **높음** — agent 루프에 MCP가 박혀 항목1과 결합 | 평가 논리 가장 단단 |
| C. A2A | 높음 | 중 | 높음 | 범위 대비 과투자 |

**권고:** A를 메인으로 가되, **방어력 보강용으로 B를 최소 1개 도구라도 병행**하면 "MCP를 실제 호출 경로에서 쓴다"는 증거가 코드에 남는다(평가 質疑 대비). 둘 다 어려우면 A 단독이되 닫힘 기준(L116)의 "MCP client→server 호출이 코드에 실재"를 **반드시 충족**해야 한다.

### K-2. 스트리밍 — SSE vs WebSocket vs 폴링

- **SSE 채택이 적절**(계획대로). 토론은 **서버→클라 단방향** 푸시이고, `sse-starlette`가 이미 설치돼 있으며, 기존 `astream` 청크 구조(`debate_service.py:58`)와 1:1로 맞는다. WebSocket은 양방향이 불필요하고 인프라(프록시·핸드셰이크) 부담만 늘린다. 폴링은 `GET /debates/{id}` 반복으로 가능하나 지연·부하 큼.
- **트레이드오프:** SSE는 HTTP/1.1 동시연결 6개 제한·프록시 버퍼링 이슈가 있으나 단건 토론 시연엔 무관.

### K-3. RAG — 하이브리드 융합·리랭커 방식 비교

- **현재:** `evidence_retrieval.py:87–92`가 이미 **news+filing 두 벡터 컬렉션을 질의→`merged.sort(key=score)`→top_k**. 즉 "이중 소스 벡터 머지"는 있고, **결핍은 (a) lexical(BM25) 부재, (b) 융합이 단순 score 정렬**.
- **융합 방식 비교:** 가중합(score 정규화 필요·튜닝 민감) vs **RRF(랭크 기반, 스케일 불문, 튜닝 거의 불요)**. → **RRF 권고**(계획과 일치).
- **리랭커:** `sentence-transformers` 기설치 → `CrossEncoder`(예: `ms-marco-MiniLM`) 무의존성 추가. 트레이드오프: 정확도↑ vs **CPU 추론 지연**(top-k 재점수, 시연 latency 주의). 한국어면 다국어 cross-encoder 필요.

### K-4. Docker — 단일 멀티스테이지 vs 분리

- **멀티스테이지 단일 Dockerfile 권고**: builder(deps 설치)+runtime(slim) → 이미지 경량. `sentence-transformers`/`torch`가 무거우므로 레이어 캐시·`.dockerignore` 중요(빌드 시간/이미지 크기 리스크).
- **compose:** app 서비스 추가 시 **서비스명 기반 내부 네트워킹**으로 전환 필수(아래 L-1).

### K-5. 에러 핸들링 — try/except+fallback vs tenacity vs 서킷브레이커

- llm_factory에 `max_retries=3`(langchain 내장)이 **이미 재시도**를 담당. 그 위에 `tenacity`를 또 씌우면 **재시도 중첩(3×N)**으로 지연 폭증 → 비권장.
- **권고:** moderator `_call`에 **얇은 try/except + graceful fallback summary**(bull/bear 노드가 쓰는 패턴 `bull_node.py:59–61`과 동일 컨벤션)만 추가. 서킷브레이커는 단일 LLM 공급자라 과설계.

### K-6. 문서 — repo 내 mermaid vs 외부 도구

- **mermaid in-repo 권고**(계획대로): 코드와 같은 PR에서 버전관리·diff 가능, GitHub 렌더. draw.io/Figma는 발표 미관은 좋으나 코드 동기화가 끊긴다(드리프트 리스크).

## L. 전체 코드 리뷰 — 계획 ↔ 코드 충돌 지점

**L-1. [충돌·심각 高] Docker 네트워킹: `localhost` 기본값 → 서비스명 전환 필수.**
`config.py:22–23` 기본값이 `redis://localhost:6379`, `chroma_url=http://localhost:8080`이고 `.env.example`도 전부 localhost다. **app이 compose 서비스가 되면 `localhost`는 app 컨테이너 자신**을 가리켜 DB/redis/chroma 연결이 전부 깨진다. → app 서비스의 env를 `postgres:5432`/`redis:6379`/**`chroma:8000`**(컨테이너 내부포트, 호스트매핑 8080 아님)로 줘야 한다. 추가로 **`.env.example` 자체 모순**: ChromaDB 주석은 `localhost:8081`인데 값은 `8080`, compose는 `8080:8000` 매핑 → 셋이 불일치. Docker 트랙 닫힘 기준에 **"app 컨테이너는 서비스명 URL을 쓴다"를 명시**하고 .env.example의 포트 표기를 정리할 것.

**L-2. [충돌·심각 中] chroma healthcheck 부재 → `depends_on: service_healthy` 불가.**
`docker-compose.yml`에서 postgres·redis만 healthcheck 보유, **chroma는 없음**(L41–54). 계획의 "depends_on + healthcheck 정리"(L148)를 충족하려면 chroma healthcheck를 **신규 추가**해야 한다. 주의: `chromadb/chroma:0.5.23`은 heartbeat 엔드포인트가 버전에 따라 `/api/v1/heartbeat`↔`/api/v2/heartbeat`로 갈리므로, 추가 시 해당 이미지에서 실제 동작하는 경로를 검증할 것(잘못 지정하면 영구 unhealthy로 app 기동 차단).

**L-3. [충돌·심각 中] RAG 점수 의미 충돌 — 기존 정렬은 "거리(낮을수록 좋음)" 가정.**
`evidence_retrieval.py:91` `merged.sort(key=lambda item: item.score)`는 **오름차순**이고 score는 chroma `distance`(낮을수록 유사). 그런데 BM25·CrossEncoder는 **높을수록 좋음**. 두 체계를 raw로 섞으면 랭킹이 뒤집힌다. → **RRF(랭크 기반 융합)로만 결합**하고, 기존 `score` 필드의 distance 의미를 건드리지 말 것(혼합 금지). 계획의 RRF 선택은 옳으나, "기존 정렬키와 BM25 점수를 직접 합산하지 않는다"를 구현 주의로 명시.

**L-4. [충돌·심각 中] 병렬화 대상에 동기 호출 혼재.**
`data_node.py`의 5개 `fetch_*`는 `await`(async)지만, 같은 노드의 `search_evidence_for_symbol`(`evidence_retrieval.py:217–225`)은 **동기**(`session_scope()` 블로킹). 계획의 `asyncio.gather`(L180)는 **async 5종에만** 적용 가능하고, evidence 검색은 `asyncio.to_thread`로 감싸야 이벤트루프를 막지 않는다. "5개 fetch만 gather, evidence는 to_thread 별도"로 구체화할 것.

**L-5. [충돌·설계결정 필요 中] 스트리밍 진입점 이중화 위험.**
현재 `create_debate`(`debate.py:37,61`)는 `run_session` **전체를 await 후 201 일괄 return**(장시간 블로킹 요청 — 토론 1~2분간 진행률 0). SSE 엔드포인트를 **추가**하면 `run_session`을 호출하는 진입점이 2개가 되어, 클라가 양쪽을 치면 **이중 토론 실행·이중 DB 쓰기·가드 중복**이 난다. → **(권고)** 기존 POST는 "생성+202 즉시반환"으로 바꾸고 실행은 SSE 스트림에서만 구동하거나, 최소한 동일 `session_id` 재실행을 가드(이미 `try_start_session` 단일락 있음 — 이걸 신뢰)로 막는 설계를 **명시적으로 결정**할 것. 현재 계획은 "추가"만 적혀 이 분기가 비어 있다.

**L-6. [정합 확인 ✅] 에러 핸들링 타겟 정확.**
`bull_node.py:48–61`·bear는 **이미 try/except로 graceful 처리**(`content="(오류:...)"`)인데 `moderator_node._call`(L24–26)만 무방비 → 계획이 moderator만 콕 집은 것은 정확. fallback 구현 시 bull/bear의 기존 컨벤션을 그대로 재사용하면 일관성↑.

## M. 예상 리스크 레지스터

| # | 리스크 | 영향 | 가능성 | 완화책 |
|---|---|---|---|---|
| R1 | Docker 서비스명 미전환(L-1) → app이 DB/chroma 연결 실패 | 高(기동 자체 실패) | 高 | app용 env 분리, 서비스명 URL, .env.example 포트 정리 |
| R2 | chroma healthcheck 오지정(L-2) → 영구 unhealthy로 app 차단 | 中 | 中 | 0.5.23 heartbeat 경로 실검증 후 지정 |
| R3 | RAG 점수 혼합(L-3) → 근거 랭킹 악화로 답변 신뢰도 하락 | 中 | 中 | RRF 랭크 융합, raw score 합산 금지 |
| R4 | 스트리밍 이중 진입점(L-5) → 중복 토론·DB 오염 | 中 | 中 | POST 202화 또는 단일락 신뢰, 진입점 단일화 결정 |
| R5 | MCP가 REST 연동으로 변질(C-1) → 항목6 미인정 | 高(항목 0점 유지) | 中 | MCP client→server 경로 코드 증거 확보, 트랙 B 자체검증 |
| R6 | reranker CPU 추론 지연(K-3) → 시연 latency 악화 | 低~中 | 中 | top-k 작게, 캐시, 시연 시 비활성 토글 |
| R7 | torch/sentence-transformers로 이미지 비대(K-4) → 빌드/배포 지연 | 中 | 高 | 멀티스테이지, .dockerignore, 레이어 캐시 |
| R8 | RAGAS 트랙 미병합(C-4) → 등급 상한 B 고정 | 高(등급) | — | 타 팀 트랙 동시 병합(이미 L10–13 명시) |
| R9 | 4일 일정에 3트랙 동시 + 타 팀 의존 → 지연 | 中 | 中 | P0(문서)·항목2를 먼저 확정, 스트리밍/ RAG는 슬립 가능 |

## N. 결론 (3차)

- **계획의 방향성·기술 선택은 코드 현실과 정합하며, 트레이드오프 판단(SSE·RRF·try/except·mermaid·멀티스테이지)도 타당**하다.
- 다만 **구현 착수 전 반드시 닫아야 할 코드 충돌 5건**(L-1 서비스명, L-2 chroma healthcheck, L-3 점수 융합, L-4 동기호출 to_thread, L-5 스트리밍 진입점)을 각 트랙 닫힘 기준에 **구체 작업으로 명시**할 것. 특히 **L-1/R1은 "기동 자체 실패"** 수준이라 Docker 트랙 1순위 체크 항목이다.
- 설계 근거표(K-0)는 5대 설계문서의 "설계 의도/대안 비교" 절로 그대로 전용 가능 — 문서 작성 효율과 평가 설득력을 동시에 올린다.
- 종합: **계획은 실행 가능 상태이며, 위 충돌 5건 + 리스크 레지스터(M)를 트랙별 체크리스트로 흡수하면 완성도가 충분**하다.

---

# 인프라 환경 확정 및 Docker 트랙 재정의 (2026-06-06 추가)

> 본 절은 사용자가 확정한 실제 인프라 토폴로지를 반영해, 위 **L-1/L-2/M-R1·R2**와 **Docker 트랙(#4)**을 보정한다. 별도 문서로 빼지 않고 여기 통합하는 이유: DB 연결·컨테이너 네트워킹은 이미 Docker 트랙·코드 충돌 절과 같은 주제이기 때문이다.

## O. 확정된 인프라 토폴로지 (전제)

[[infra-stage-policy]] 메모리 및 코드(`app/core/db.py`, `app/core/database.py`, `app/core/redis.py`)와 일치 확인:

| 구성요소 | 위치 | 앱 연결 경로 | 비고 |
|---|---|---|---|
| **PostgreSQL** | **NCP 매니지드(원격, `101.79.19.53`)** | `DATABASE_URL` → 외부 호스트. `db.py`(sync SQLAlchemy)+`database.py`(asyncpg pool) **2경로 모두 NCP** | 이미 운영 중, 단일 SOT |
| **Redis** | **개발자별 로컬 Docker** | `REDIS_URL`(default `localhost:6379`) | 팀 단위 lock 아님(개인 로컬) → PG unique/upsert가 최종 중복 방어선 |
| **ChromaDB** | **개발자별 로컬 Docker** | `CHROMA_URL`(default `localhost:8080`→내부 8000) | 본문 SOT 아님, **재생성 가능한 RAG 인덱스**(개발자마다 내용 다를 수 있음) |

**배포는 하지 않는다(졸프 단계).** 따라서 Docker화(항목5)는 **운영 배포 목적이 아니라 "평가 항목 충족 + 재현 가능한 실행"** 목적이다. 이 사실이 아래 재정의의 핵심 전제다.

## P. L-1/L-2/R1·R2 보정 (실제 토폴로지 반영)

**P-1. L-1 보정 — "postgres를 compose 서비스명으로" 한 부분은 틀렸다.**
앞 L-1은 app→postgres를 `postgres:5432` 서비스명으로 바꾸라고 했으나, **실제 postgres는 NCP 원격**이다. 정정:
- **`DATABASE_URL`은 NCP 호스트를 그대로 유지**(서비스명 아님). 컨테이너 egress로 외부 NCP에 접속 → **이미 동작하는 경로라 추가 위험 없음**.
- **compose의 `postgres` 서비스는 앱이 쓰지 않는다**(팀은 NCP 사용). 리포트의 "postgres 호스트 5432 충돌로 미기동"은 이래서 무해했던 것. → Docker 트랙에서 **app의 `depends_on`에 postgres를 넣지 말 것**. compose postgres 서비스는 (a) 순수 로컬 대체용으로 남기거나 (b) 혼동 방지로 제거. **권고: 주석으로 "미사용(NCP 사용)" 명시 후 존치.**
- **남는 진짜 이슈는 redis/chroma뿐**: 컨테이너화된 app이 로컬 redis/chroma에 닿으려면 연결 URL을 바꿔야 한다.

**P-2. 컨테이너화된 app의 redis/chroma 연결 — 두 가지 배치.**
- **배치 A (권고): app+redis+chroma를 같은 compose에 둔다.** app env를 `REDIS_URL=redis://redis:6379/0`, `CHROMA_URL=http://chroma:8000`(내부포트 8000, 호스트 8080 아님)으로. `depends_on: [redis, chroma]`(healthy).
- **배치 B: redis/chroma는 호스트에서 그냥 돌리고 app만 컨테이너.** 이때 app env는 `host.docker.internal`(`redis://host.docker.internal:6379`, `http://host.docker.internal:8080`). Linux에선 `extra_hosts: ["host.docker.internal:host-gateway"]` 필요.
- **결론:** 배치 A가 재현성·평가설명에 유리. **단 `DATABASE_URL`만은 두 배치 모두 NCP 외부 호스트 유지.**

**P-3. L-2 유지 — chroma healthcheck는 여전히 추가 필요.**
배치 A에서 `depends_on: chroma (service_healthy)`를 쓰려면 chroma healthcheck가 있어야 하는데 현재 없음(L-2 그대로 유효). `chromadb/chroma:0.5.23` heartbeat 경로 실검증 후 추가.

**P-4. R1 하향, R2 유지.**
- **R1(서비스명 미전환 → 기동 실패): 심각도 高→中.** postgres가 NCP라 "DB 연결 실패"의 절반(postgres)은 애초에 안 깨진다. 남은 건 redis/chroma URL 전환뿐이고 범위가 좁다.
- **R2(chroma healthcheck): 유지(中).**

## Q. 재정의된 Docker 트랙 닫힘 기준 (#4 대체)

기존 L160–163을 아래로 대체/보강한다:
- `Dockerfile`(멀티스테이지) 추가 + compose에 **app 서비스**.
- app env: `DATABASE_URL=<NCP 그대로>`, `REDIS_URL=redis://redis:6379/0`, `CHROMA_URL=http://chroma:8000`.
- `depends_on`: **redis, chroma만**(postgres 제외). chroma healthcheck 추가.
- `docker compose up --build`로 app 기동 → **`/health` 200**.
- app 컨테이너에서 **NCP postgres 실연결 확인**(토론 1건 생성), **로컬 redis/chroma 실연결 확인**(체크포인트 저장 + evidence 검색 동작).
- `.env.example` 포트 표기 정리(현재 8080/8081 혼재 → 통일).

## R. 이 토폴로지가 다른 트랙에 주는 영향 (교차 영향)

- **#6 RAG / 시연 재현성 [주의]:** ChromaDB가 **개발자별 로컬**이라 인덱스 내용이 머신마다 다르다. → **시연·평가는 chroma가 채워진 단일 머신에서** 하거나, **reindex 스크립트로 동일 코퍼스 재생성** 후 시연. BM25/하이브리드(#6) 작업·시연도 같은 머신 기준으로 검증할 것.
- **#1 문서(컴포넌트/배포뷰):** 위 O절 토폴로지 표를 **컴포넌트 설계서의 "배포/실행 환경" 절**로 그대로 수록 → 5대 문서 충실도↑. "운영 전환 시 NCP 셀프호스트로 이전"은 별도 `production-deployment-plan.md` 시점으로 미룬다고 명시([[infra-stage-policy]]와 일관).
- **항목2 에러핸들링:** redis 로컬·fail-open이므로 redis 부재에서도 토론은 돌아야 한다(이미 `try_start_session` fail-open, 체크포인트 fail-soft). moderator fallback 추가 시 이 가정 유지.

## S. 우선순위 — 그래서 무엇을 먼저?

**결론: 기존 고정순서(1.문서 → 2.에러핸들링 → 3.MCP → 4.Docker → 5.스트리밍 → 6.RAG)를 바꿀 필요 없다.** 이 인프라 확정은 **새 트랙을 추가하지 않고, #4 Docker를 "재정의 + 디리스크"** 했을 뿐이다.

- **#4 Docker는 오히려 쉬워졌다**(postgres는 NCP 그대로, redis/chroma URL 전환 + chroma healthcheck만). 순서를 당길 만큼 급하진 않다 — 문서/에러핸들링/ MCP가 점수·게이트 측면에서 여전히 앞선다.
- **단, 지금 당장 "공짜로" 해둘 것 1가지:** O절 토폴로지 표를 **#1 문서 작업 때 컴포넌트 설계서에 함께 박아넣기**. 어차피 #1을 먼저 하므로, 그 안에서 배포/실행 환경을 같이 문서화하면 #4 착수 시 설계가 이미 서 있다.
- **#6 RAG 착수 전 선결:** "어느 머신의 chroma를 기준으로 시연/검증할지" 먼저 합의(R절). 안 그러면 하이브리드 결과가 머신마다 달라 검증이 흔들린다.

**즉 실행 순서 권고:**
1. (#1 문서) — 그 안에 **O절 인프라 토폴로지 동시 수록**
2. (#2 에러핸들링)
3. (#3 MCP)
4. (#4 Docker) — **Q절 재정의 기준으로**(postgres=NCP 유지, redis/chroma만 서비스명, chroma healthcheck 추가)
5. (#5 스트리밍)
6. (#6 RAG) — **착수 전 시연 기준 chroma 머신 합의(R절)**

별도 `production-deployment-plan.md`는 **배포를 실제 결정할 때** 작성(지금은 불필요).

---

# 최종 검증 (2026-06-06, 본문 통합본 대조)

이번 라운드는 사용자가 1~3차 검증 지적을 **본문(P0~P2)에 직접 흡수**한 통합본을 대상으로, 흡수된 서술이 코드와 정합한지 핵심만 재확인한다. (검증 도구: 소스 직접 grep `@` 현재 작업 트리)

## T. 본문 흡수분 코드 재확인

| 본문 서술 | 위치 | 코드 확인 | 결론 |
|---|---|---|---|
| postgres=NCP 원격 SOT, redis/chroma만 로컬 compose, app `depends_on`=redis·chroma만, chroma healthcheck 추가 | L158–176 | `db.py`/`database.py` 2경로 모두 `DATABASE_URL`(NCP), compose에 chroma healthcheck 없음 | ✅ 정합 |
| `data_node`의 async fetch만 `gather` 대상, evidence는 sync라 `to_thread`/별도 단계 | L200 | `data_node.py:25–29` 5개 `await fetch_*`, **L35 `search_evidence_for_symbol`는 동기**(`evidence_retrieval.py:217`, `session_scope` 블로킹) | ✅ 정합 |
| POST와 SSE 경로 중복 토론 실행 방지 | L201–203, L212 | `debate.py:37` `await run_session(...)` → `:61` `_build_session_response` **일괄 return**(블로킹), `EventSourceResponse` 0건 → SSE 추가 시 진입점 2개 됨 | ✅ 위험 실재, 닫힘기준에 반영됨 |
| Chroma distance와 BM25/reranker raw 합산 금지, RRF만 | L230–232, L241 | `evidence_retrieval.py:91` `merged.sort(key=lambda item: item.score)` **오름차순**, `:175` `score=distance`(낮을수록 유사) → BM25/reranker(높을수록 좋음)와 raw 혼합 시 랭킹 역전 | ✅ 정합 |
| watchdog/telemetry류 부가 실패가 핵심 성공 경로를 깨지 않음 | L140 | moderator `_call`(`moderator_node.py:24–26`)은 `try/except` **없음**(SPOF), `:30`·`:137` try/except는 _parse·DB용 | ✅ 정합(보강 타겟 정확) |
| Docker `/health` 닫힘기준 | L175 | `app/main.py:30` `@app.get("/health")` 실존 | ✅ 충족 가능 |

→ **이번에 본문으로 끌어올린 6개 서술 전부 코드와 일치.** 별도 보정 불필요.

## U. 남은 미세 이슈 (실행 무관, 문서 위생만)

**U-1. 검증 섹션(A~S)의 라인번호 드리프트.**
본문을 편집하면서 길이가 바뀌어, 과거 검증 절들이 인용한 `Lxx`(예: F절의 "L90–104", S절의 "L160–163")가 현재 줄과 어긋난다. **내용·결론은 유효**하나, 추후 이 문서를 근거로 작업 지시할 땐 라인번호보다 **함수/파일명 기준**(`moderator_node._call`, `evidence_retrieval.search_evidence_for_symbol` 등)으로 보는 게 안전하다. 정리할 여력이 있으면 A~S의 `Lxx` 인용을 파일·심볼 기준으로 치환 권장(필수 아님).

**U-2. P-라벨 vs 최종 고정순서.**
섹션 라벨(MCP=P1이 에러핸들링보다 앞 배치)과 최종 고정순서(#2 에러핸들링 > #3 MCP)는 여전히 시각적으로 어긋나나, L330–332 "고정순서가 P 라벨보다 우선"으로 **명시 해소됨**. 추가 조치 불요.

## V. 최종 결론

- **착수 가능 상태로 판단한다.** 본문이 1~3차 검증 + 인프라 확정(O~S)을 모두 흡수했고, 이번에 흡수된 6개 서술이 전부 코드와 정합함을 재확인했다(T절).
- **실행 시 반드시 지킬 구현 주의 4건**(이미 본문 닫힘기준에 박혀 있음): ① redis/chroma만 서비스명 전환·postgres는 NCP 유지·chroma healthcheck 추가, ② `gather`는 async fetch만·evidence는 `to_thread`, ③ POST/SSE 진입점 단일화(또는 단일락 신뢰)로 중복 토론 차단, ④ RAG 융합은 RRF만·raw score 합산 금지.
- 우선순위는 **1.문서 → 2.에러핸들링 → 3.MCP → 4.Docker → 5.스트리밍 → 6.RAG** 유지. 인프라 확정은 #4를 디리스크했을 뿐 순서 변경 사유가 아니다.
- 남은 건 새 설계 결정이 아니라, 위 주의 4건을 **실제 작업 때 그대로 지키는 것**뿐. 사용자의 최종 판단("큰 방향 충돌 없음 / 실행 가능 수준")에 동의한다.

---

# 병합 후 검증 메모 (2026-06-07, `main`의 RAGAS 트랙 병합 반영)

`uc` 브랜치가 `main`을 병합한 뒤(`97a55fa`), 평가 관련 구현이 실제로 얼마나 들어왔는지 다시 확인했다. 결론부터 말하면:

- **RAGAS는 더 이상 "전무" 상태가 아니다.**
- 다만 **게이트 해제를 단정할 정도의 완결형 정량평가 파이프라인도 아직 아니다.**
- 따라서 이 계획 문서의 본 작업 순서(문서 → 에러핸들링 → MCP → Docker → 스트리밍 → RAG)는 유지하되,
- **타 팀 의존성 서술은 "RAGAS 1차 구현 병합 완료, 증적 체인 미완"으로 해석하는 것이 정확**하다.

## W. 이번 병합에서 실제로 들어온 것

| 항목 | 코드 확인 | 판정 |
|---|---|---|
| RAGAS 의존성 | `requirements.txt:52` `ragas==0.2.15` | ✅ 반영 |
| 요약 품질 평가 | `app/domain/debate_evaluation.py` — `faithfulness` 평가 구현 | ✅ 반영 |
| 검색 근거 품질 평가 | `app/domain/debate_evaluation.py` — `context_precision` 평가 구현 | ✅ 반영 |
| 토론 종료 후 자동 트리거 | `moderator_node.py:200+` `asyncio.create_task(...)` 로 summary/evidence eval 비동기 실행 | ✅ 반영 |
| data_agent raw evidence 보존 | `app/agents/state.py:40`, `data_node.py` 주석/상태 필드 | ✅ 반영 |

즉 현재는 **토론 완료 후 백그라운드로 RAGAS 사후 평가를 돌리는 1차 경로**까지는 들어와 있다.

## X. 아직 부족한 부분

| 항목 | 코드 확인 | 의미 |
|---|---|---|
| 평가 결과 영속화 | DB model/repository/API 없음 | 점수는 현재 로그로만 남고, 세션별 조회/리포트 증적이 약함 |
| 실행 스크립트/배치 | `scripts/run_ragas_eval.py` 없음 | golden set 기반 반복 평가 파이프라인은 아직 없음 |
| 리포트 산출물 | `reports/ragas-<sha>.json` 경로 없음 | 발표/평가 제출용 artifact 부족 |
| metric 범위 | `faithfulness`, `context_precision`만 존재 | `answer_relevancy` 등 추가 지표는 아직 없음 |
| 테스트/회귀 검증 | `test_ragas.py`(루트)는 있으나 **더미데이터 단독 스모크**일 뿐, `tests/` 하위 golden-set·pytest 회귀 스위트는 없음 | 함수 동작 확인은 되나, 환경/키/모델·실데이터 기준 재현성 검증은 약함 |

따라서 현재 RAGAS 상태는:
- **0점/미구현 단계는 벗어남**
- 하지만 **"정량평가 파이프라인 완성"으로 바로 닫히진 않음**

## Y. 항목 3/7과의 경계도 다시 확인

이번 병합은 **RAGAS(항목 8)** 쪽만 일부 전진했고, 아래는 여전히 별도 트랙이다.

- **Langfuse**: grep 0건
- **vLLM/Ollama/MLX**: grep 0건
- **`model_used` 메타데이터 하드코딩**: 여전히 남아 있음

즉:
- 항목 8은 **부분 해소**
- 항목 3, 7은 **여전히 타 팀 작업 의존**

**Y-보강 (코드 재확인 2026-06-07).** RAGAS 평가 LLM 자체는 이미 **OpenRouter sLLM**(`debate_evaluation.py:45` `model="openai/gpt-oss-120b:free"`, `base_url=settings.openrouter_base_url`)을 **실제로 호출**한다. 의미:
- `openrouter_base_url`은 **죽은 설정이 아니라 라이브** — 항목3 sLLM 트랙은 이 호출을 **작동 레퍼런스**로 그대로 복제하면 된다(B-2에서 말한 "factory base_url 재배선"이 사실상 평가 경로에 선례로 존재).
- **단 이건 *평가용* LLM**이다. 항목3은 "토론/검증 Agent가 sLLM(≤300B) 사용"을 요구하므로, **토론 본 경로(bull/bear/moderator)가 여전히 `gpt-4o-mini`인 한 항목3은 미충족**이다. 평가 LLM이 sLLM인 것과 항목3 요건은 별개로 읽어야 한다.

## Z. 이 계획 문서에 주는 영향

1. **우선순위는 바꾸지 않는다.**
   - 이 문서 범위 밖이던 RAGAS가 1차 병합됐다고 해서, 지금 당장 문서/MCP/Docker보다 앞세울 이유는 없다.
   - 현재 남은 우리 쪽 작업은 여전히 **문서화·시연·운영 경로 보강**이 핵심이다.

2. **서두의 "RAGAS 미병합 시 게이트 유지" 문구는 해석을 좁혀 읽어야 한다.**
   - 이제는 "RAGAS 코드가 전혀 없음"이 아니라,
   - **"RAGAS의 평가 증적 체인(golden set / batch script / saved report / 테스트)까지 포함해 완성되어야 게이트 해제 근거가 강해진다"** 로 이해하는 것이 맞다.

3. **별도 트랙 항목은 유지한다.**
   - RAGAS는 병합됐지만 아직 증적 저장/배치화가 약하므로, `별도 트랙`에서 완결형으로 마무리하는 구조는 유지하는 편이 안전하다.

## AA. 최종 판정

- `main` 병합으로 **RAGAS 관련 구현은 실제로 들어왔다.**
- 따라서 과거 평가서의 "`ragas grep 0건` / `정량평가 파이프라인 전무`" 평가는 **현재 코드 기준으로는 더 이상 사실이 아니다.**
- 하지만 아직은 **로그 기반 사후 평가 1차 구현**에 가깝고,
  **배치 실행·리포트 산출·결과 영속화까지 닫혀야 평가 증적으로 강해진다.**
- 결론적으로 이 계획 문서는 **그대로 유효**하며, 단지 RAGAS 관련 전제는  
  **"미구현" → "1차 병합 완료, 완결 증적은 미완"** 으로 업데이트해서 읽으면 된다.

---

# 1차 작업 진행 기록 — #1 5대 설계문서 (2026-06-07)

우선순위 #1(5대 설계문서)의 초안을 `memo/design/`에 5종 생성했고, **실제 코드와 대조해 4건을 보정**했다. 닫힘기준("문서 5종 전부 존재 + 코드 경로 1:1 대응")의 전반부는 충족됐고, 정합성 보정으로 후반부도 강화했다.

## BB. 생성된 문서 (5/5)

| 문서 | 파일 | 상태 |
|---|---|---|
| 유스케이스 명세서 | `memo/design/use-case-specification.md` | ✅ |
| 컴포넌트 설계서 | `memo/design/component-design.md` | ✅ |
| 인터페이스 정의서 | `memo/design/interface-definition.md` | ✅ |
| 시퀀스 다이어그램 | `memo/design/sequence-diagram.md` | ✅ |
| ERD | `memo/design/erd.md` | ✅ |

→ **5종 전부 존재** → 평가서 항목4 "누락 캡 1" 해제 조건의 1차 요건 충족.

## CC. 코드 대조로 보정한 4건 (정합성)

| # | 보정 내용 | 근거 | 적용 문서 |
|---|---|---|---|
| 1 | **`debate_note` 테이블 누락** 추가 (14→15종, 평가서 "15개 테이블"과 일치) | `app/models/debate.py:206` `__tablename__="debate_note"`, `(user_id,session_id)` unique | erd.md, component-design.md |
| 2 | **토론 LLM = OpenAI `gpt-4o-mini` 직접 호출**로 정정(문서엔 "OpenRouter"로 오기). OpenRouter는 RAGAS 평가 LLM(`gpt-oss-120b:free`)에만 사용 | `llm_factory.py:38–41` `api_key=openai_api_key`·base_url 없음 / `debate_evaluation.py:45` | sequence-diagram.md, component-design.md, use-case-specification.md |
| 3 | **시퀀스에 `moderator_pre`+`moderator_check` 검증 루프 추가**(기존엔 단순 bull→bear→moderator로 항목1 강점 누락) | `debate_graph.py:60–66` 엣지·`_router` | sequence-diagram.md |
| 4 | **`GET /api/market/indexes` 누락** 추가 | `market_data.py:265` | interface-definition.md |

> ERD의 관계선(evidence→news/filing/price/financial/technical cache FK 5종 `SET NULL`, caches→ticker_metadata 1:N, moderator_summary `session_id` unique=1:1)은 코드와 **일치 확인**.

## DD. #1 정밀 보강 진행 (2026-06-07)

**완료 — ERD/시퀀스 코드 기준 정밀화:**
- **ERD**(`erd.md`): 속성 포함 mermaid로 교체(15개 엔터티 PK/FK/주요 컬럼), **Enum 카탈로그**(9종, 코드 위치 명시), **제약·인덱스 절**(unique 10종 = PG 중복방어선, `cache_key` 부분 unique + `cached_from_session_id` self-ref 토론캐시), 관계표에 CASCADE/SET NULL 명시.
- **시퀀스**(`sequence-diagram.md`):
  - 토론 실행 — RuntimeGuard 단일비행 락(409 거절)·fail-open, checkpoint load/save(매 노드, 24h fail-soft), astream→merge_state 루프, `_router` 5단 분기 우선순위(환각2회/end/주제3개/intervene/턴), 상태키, 실패 시 `update_session_status(failed)` 경로까지 반영.
  - 관심종목 — 404/409 검증 분기, BackgroundTasks 5종(news/financial/price/filing/valuation) + `sync_enqueued` 처리 명시.

**완료 — 인터페이스 정의서 스키마 1:1화(`interface-definition.md`):**
- `app/schemas/{watchlist,market_data,debate}.py`의 모든 Pydantic 모델을 요청/응답·필드·타입·제약(min/max, 기본값, nullable)까지 1:1 반영.
- 쿼리 파라미터 정확화(`q≤100`, `limit` 엔드포인트별 기본/상한: tickers·news·filings=20/100, prices=260/1000, feed=20→1–100 cap).
- `DebateListItem`(목록 경량) vs `DebateSessionResponse`(statements 포함 full) 구분 명시, enum `DebateCategory` 값 명시.

→ **#1 5대 설계문서 트랙 사실상 종료**(5종 존재 + 코드 1:1 정합 + ERD/시퀀스/인터페이스 정밀화 완료). **다음은 #2(에러핸들링).**

## EE. #2 에러핸들링 진행 기록 (2026-06-07)

**완료 — moderator SPOF 완화 + 보조 실패 비전파:**
- `moderator_pre`:
  - LLM 호출 실패 시 기본 의제 3개(`쟁점1~3`)로 진행
  - agenda 파싱 결과가 비정상이면 `_coerce_agenda()`로 기본 의제 보정
- `moderator_summary`:
  - LLM 호출 실패 시 `_build_summary_fallback()`으로 fallback summary / key_points 생성
  - summary 저장 및 session 완료 경로는 계속 진행
- fallback summary가 사용되면 summary RAGAS 평가는 건너뛰고 evidence 평가만 유지
- `save_evidence()`:
  - 개별 evidence 저장 실패는 warning만 남기고 statement/summary/session 완료 경로 유지
- RAGAS 사후평가:
  - `_schedule_background_task()`를 통해 `asyncio.create_task()` 등록
  - 등록 실패 및 실행 중 예외 모두 로그로만 남고 토론 본체 성공 경로에 비전파

**검증:**
- `python3 -m compileall app/agents/nodes/moderator_node.py` 통과
- `python -m py_compile app/agents/nodes/moderator_node.py` 통과
- 현재 코드 기준으로 다음이 닫힘:
  - `moderator_pre` LLM 실패 → 기본 agenda로 진행
  - `moderator_summary` LLM 실패 → fallback summary 저장
  - evidence 일부 저장 실패 → summary/session 완료 유지
  - RAGAS 평가 실패 → 본체 토론 완료 유지

**판정:**
- 계획서의 #2 닫힘 기준("moderator 호출 실패 시 토론 전체가 즉시 500/503으로 죽지 않음", "fallback 또는 graceful summary 반환", "MCP publish 실패가 debate success 경로를 깨지 않음") 중
  - **moderator fallback / graceful summary / RAGAS fail-soft**는 충족
  - **MCP publish fail-soft**는 아직 MCP 트랙 미착수라 유보
- 따라서 **#2는 현재 범위 기준 사실상 종료**, 다음 우선순위는 **#3 MCP 도입**.
- 단, fail-soft 범위는 **moderator/보조기능까지**이며, `save_statement`/`save_moderator_summary`/`update_session_status` 같은 핵심 DB 저장 실패는 여전히 본체 실패로 본다.
