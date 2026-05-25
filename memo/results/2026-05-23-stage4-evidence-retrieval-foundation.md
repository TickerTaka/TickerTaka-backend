# 2026-05-23 Stage 4 - Evidence Retrieval Foundation

## 범위

- 토론 Stage 4의 첫 구현으로 `search_evidence()` 더미를 제거했다.
- `news` / `filing` Chroma 컬렉션에서 semantic retrieval 후, PostgreSQL cache 메타데이터와 다시 매핑해 evidence dict로 반환한다.

## 구현 내용

### 1. Retrieval 서비스 추가

- `app/domain/evidence_retrieval.py`
  - `EvidenceRetrievalService`
  - `search_evidence_for_symbol()`
- ChromaDB query는 `symbol` metadata filter를 강제한다.
- `news`와 `filing`을 각각 조회한 뒤 score(distance) 기준으로 합쳐 `top_k`를 반환한다.

### 2. PG 메타 재매핑

- `app/repositories/news_cache_repository.py`
  - `get_by_ids()`
- `app/repositories/filing_cache_repository.py`
  - `get_by_ids()`

이 단계에서 반환하는 evidence payload는 `debate_repo.save_evidence()`와 맞춘다.

- `source_type`
- `source_title`
- `excerpt`
- `source_url`
- `source_label`
- `news_cache_id` 또는 `filing_cache_id`

### 3. 토론 tool 연결

- `app/agents/tools/evidence_tools.py`
  - 기존 더미 `search_evidence()` 제거
  - 실제 retrieval 서비스 호출로 교체

이 변경으로 bull / bear 노드의 ReAct agent는 이제 뉴스/공시 근거를 실제로 검색할 수 있다.

### 4. data_node evidence prefetch 추가

- `app/agents/nodes/data_node.py`
  - 카테고리별 query template 생성
  - 토론 시작 시 `top_k=4` evidence를 미리 검색
  - `evidence_context` 문자열로 요약해 state에 넣음
  - `news_chunks`도 retrieval 결과 기반으로 우선 구성

- `app/agents/state.py`
  - `evidence_context` 필드 추가

- `app/agents/prompts/prompts.py`
  - bull / bear / moderator prompt에 `evidence_context` 주입

이 단계로 토론 프롬프트 자체가 최신 뉴스/공시 근거를 기본 문맥으로 갖게 됐다.

## 현재 설계 판단

- 이번 단계는 retrieval foundation만 우선 구현했다.
- `data_node`는 아직 기존처럼 price/financial/news/filing 컨텍스트를 조합해 프롬프트 입력을 만들고,
  근거 검색은 tool 호출 시점에 수행한다.
- intraday quote, LLM cache, checkpoint, active guard는 Stage 4 후속 범위로 남겨둔다.

## 검증

- `scripts/validate_evidence_retrieval.py`
  - 검증 전용 컬렉션 `news_validate_retrieval`, `filing_validate_retrieval` 사용
  - deterministic embedding으로 news 1건, filing 1건을 upsert
  - retrieval 결과가 두 source를 모두 반환하는지 확인

## 남은 작업

- category별 query template / source quota 세분화
- intraday quote Redis 캐싱
- LLM cache / rate limit / checkpoint

## 후속 진행 - Intraday Quote

- `app/external/quote_client.py`
  - `YFinanceQuoteClient`
  - `QuoteSnapshot`
- `app/domain/intraday_quote.py`
  - Redis key `quote:latest:{symbol}`
  - 장중 5분 / 장후 30분 / 주말 24시간 TTL
- `app/agents/tools/market_tools.py`
  - `get_stock_price()`가 direct yfinance 대신 `IntradayQuoteService`를 통해 시세 조회
- `app/agents/nodes/data_node.py`
  - DB에 가격 데이터가 없을 때 intraday quote + yfinance 기술지표 폴백 조합 사용
- `scripts/validate_intraday_quote.py`
  - Fake Redis / Fake QuoteClient로 cache hit + stale refresh 검증

## 후속 진행 - LLM Cache

- `app/core/llm_cache.py`
  - `CachedChatModel`
  - Redis key: `llm-cache:{model}:{prompt_version}:{sha256(prompt)}:{temperature}`
  - 응답 `content` / metadata를 24시간 TTL로 저장
- `app/core/llm_factory.py`
  - 기존 `ChatOpenAI` 반환을 cache wrapper로 감쌈
  - role별 prompt version(`bull-v1`, `bear-v1`, `moderator-v1`) 사용
- `app/config.py`
  - `LLM_CACHE_ENABLED`
  - `LLM_CACHE_TTL_SECONDS`
- `scripts/validate_llm_cache.py`
  - Fake LLM / Fake Redis로 동일 prompt가 1회만 실제 invoke 되는지 검증

## 후속 진행 - Checkpoint

- `app/agents/debate_checkpoint.py`
  - Redis key: `debate:checkpoint:{session_id}`
  - state JSON 저장 / 로드 / 삭제
  - partial update를 기존 state에 누적하는 `merge_state()` 제공
- `test_debate.py`
  - 시작 시 동일 `session_id` checkpoint가 있으면 복구
  - 각 graph chunk 이후 checkpoint 저장
- `app/agents/nodes/moderator_node.py`
  - 토론 완료 후 checkpoint 삭제
- `scripts/validate_debate_checkpoint.py`
  - Fake Redis로 save/load/clear 검증

현재 단계는 LangGraph 전용 공식 Redis checkpointer 도입 전의 실용적인 1차 구현이다.
실제 실행 경로(`test_debate.py`)에서 중간 state 복구가 가능하도록 먼저 닫았다.

## 후속 진행 - Active Guard / Rate Limit

- `app/core/debate_runtime_guard.py`
  - active guard key: `debate:active:{user_id}:{symbol}`
  - daily token key: `rate:user:{user_id}:tokens:{KST date}`
  - daily debate key: `rate:user:{user_id}:debates:{KST date}`
  - session cost key: `cost:debate:{session_id}`
- `test_debate.py`
  - 토론 시작 전에 active guard / daily debate limit 검사
  - 실행 중에는 runtime context를 bind해서 LLM usage를 세션/사용자 단위로 자동 집계
  - 종료 시 active guard 해제
- `app/core/llm_cache.py`
  - LLM 응답의 usage metadata를 읽어 현재 토론 context에 token usage 반영
- `scripts/validate_debate_runtime_guard.py`
  - Fake Redis로 active guard, daily debate limit, token usage 집계 검증

## 후속 진행 - Debate API 최소 경로

- `app/domain/debate_service.py`
  - 토론 세션 실행 orchestration
  - active guard / checkpoint / graph 실행을 한 곳에서 묶음
- `app/api/debate.py`
  - `POST /api/debates`
  - `GET /api/debates/{session_id}`
- `app/schemas/debate.py`
  - request / response schema
- `app/main.py`
  - debate router 등록
- `scripts/validate_debate_service.py`
  - fake graph / fake tracker 기반 서비스 검증
- `scripts/validate_debate_api.py`
  - FastAPI TestClient 기반 endpoint smoke test
  - `POST /api/debates`, `GET /api/debates/{session_id}`, 404/422 검증

## 이후 live 검증 및 트러블슈팅

Stage 4 이후 `uvicorn + curl` 기준 live debate 실행을 실제로 확인했다.

다만 그 과정에서 다음 이슈를 순차적으로 해결했다.

- debate enum DB 저장값 mismatch (`FINANCIAL` / `RUNNING` -> DB enum value)
- `OPENROUTER_API_KEY` 미설정 시 500 대신 503 정리
- OpenRouter 모델 ID 404 (`deepseek/deepseek-r1:free` -> `openrouter/auto`)
- Redis 미기동
- LangGraph recursion limit 상향
- `CachedChatModel.bind_tools()` 누락
- ReAct agent 경로에서 cache wrapper가 LangChain Runnable이 아닌 문제

최종적으로 `POST /api/debates`는 `201 Created`와 함께 `summary_content`, `key_points`, `statements`를 반환하는 상태까지 확인했다.

상세 기록은 아래 별도 종합 문서를 참조한다.

- `memo/results/2026-05-25-mergedb-phase3-4-integration-and-troubleshooting.md`

---

## 검증/보완 메모 (2026-05-25, 외부 검증)

본 보고서가 명시한 Stage 4 1차 구현 범위와 실제 코드 / `vector-db-and-evidence-retrieval-plan.md` Phase 4 + `debate-runtime-infrastructure-plan.md` Phase 1-5 + 토론 plan 영역을 대조했다.

### 닫힘 평가

| 영역 | plan Phase | 상태 |
|---|---|---|
| Retrieval API | vector-db Phase 4 | **닫힘** — `EvidenceRetrievalService.search_symbol_evidence`가 news + filing 양 컬렉션 metadata filter + score 머지 (`evidence_retrieval.py:77-92`) |
| Intraday quote | debate-runtime Phase 1 | **닫힘** — `IntradayQuoteService` + Redis TTL key `quote:latest:{symbol}` |
| LLM cache | debate-runtime Phase 2 | **닫힘 (1차)** — `CachedChatModel.invoke` + Redis SETEX, 캐시 히트 시 inner 호출 X (`llm_cache.py:48-71`) |
| Rate limit / active guard | debate-runtime Phase 3, 5 | **닫힘 (1차)** — `DebateRuntimeGuard.try_start_session`이 active SETNX + daily debate INCR + daily token cap (`debate_runtime_guard.py:59-93`) |
| Checkpoint | debate-runtime Phase 4 | **1차 닫힘** — 자체 Redis 키 `debate:checkpoint:{session_id}`, LangGraph 공식 checkpointer는 후속 (`debate_checkpoint.py:13-73`) |
| Debate API | 토론 plan | **닫힘** — `POST /api/debates` 201, `GET /api/debates/{session_id}` 200, 404/422/409/503 분기 (`api/debate.py:17-100`) |

### 구조적으로 잘 된 점

1. **evidence retrieval 카테고리 → query 분기** (`evidence_retrieval.py:23-29`) — technical/financial/market/macro/synthesis 5개 카테고리별 query template + symbol_name 치환. plan 본문 "카테고리별 query template / source quota" 1단계 충족. quota는 아직 source당 동일이지만 head_limit으로 조정 가능.

2. **`_safe_query_collection`의 fail-soft** (`evidence_retrieval.py:136-154`) — chroma 호출이 예외(차원 충돌, 컬렉션 부재 등) 시 빈 dict 반환 + logger.exception. 토론 그래프 자체가 죽지 않게 격리. live 트러블슈팅 H에서 발견된 64/768 차원 충돌이 토론을 멈추지 않는 이유.

3. **`CachedChatModel`이 `bind_tools` + `__getattr__`로 LangChain 인터페이스 보존** (`llm_cache.py:62-74`) — 트러블슈팅 F의 fix가 단순한 위임 패턴이라 후속 LangChain 업그레이드 시 brittle. 단 ReAct agent 경로에서는 `cached=False`로 우회(`llm_factory.py:51-52`)해 Runnable 계약 깨짐 회피. **현재 단계에서는 적절한 절충**, 운영 진입 시 LangChain `BaseChatModel` 정식 상속으로 정리 권장.

4. **`_record_usage`가 캐시 미스에서만 호출됨** (`llm_cache.py:57-60`) — 캐시 히트 시 토큰 카운트 중복 누적 안 되도록 정확한 위치. 단 캐시 히트도 사용자 입장에서는 "토론 1회" → daily_debate 카운터(`try_start_session`)는 캐시 여부와 무관하게 +1.

5. **`DebateExecutionService.run_session`의 finally end_session** (`debate_service.py:68-69`) — 예외 발생해도 active guard 반드시 해제. `update_session_status(..., "failed", ...)`도 try 안에서 동작. 사용자 두 번째 토론이 영구 차단되는 사고 방지.

6. **`debate_service`가 checkpoint를 chunk마다 save** (`debate_service.py:58-62`) — 중간 실패 시 다음 호출에서 `load_checkpoint`로 복구 시도. `merge_state`가 statements는 append, 나머지는 overwrite — 부분 round 결과 누락 없음.

7. **`api/debate.py`의 에러 코드 분기** (`api/debate.py:42-55`) — `DebateStartRejectedError` → 409 + 세션 row delete, 기타 Exception → 503 + session_id를 응답에 포함. 트러블슈팅 B의 fix가 사용자 경험에 적합 (UI에서 재시도 가능).

### 잔여 약점 / 운영 품질 보정 항목

1. **`try_start_session`의 estimated_tokens=0 디폴트** (`debate_service.py:34`, `api/debate.py:34-41`) — caller가 항상 0을 넘기므로 daily_token cap이 실행 후에만 적용된다. 한 사용자가 daily_token 한도를 넘어서도 *해당 세션은 실행 완료*되고, 다음 세션에서야 차단된다. 1차 인프라로는 OK이지만 운영 진입 시 토큰 사전 추정(평균값) 추가 권장.

2. **filing 컬렉션 차원 64/768 충돌** (트러블슈팅 H) — 구조 문제가 아니라 데이터 상태 문제. `_safe_query_collection`가 fail-soft하므로 토론은 계속 진행. **운영 품질**. 즉시 보정:
   - `chroma.delete_collection("filing")` → 재생성 후 `EvidenceIndexingService.reindex_filing_for_symbol(symbol)` 전 watchlist symbol에 대해 재실행
   - 또는 `scripts/reindex_local_chroma.py`에 filing reset 옵션이 있다면 그것으로 일괄 처리
   - 검증컬렉션(`filing_validate_reindex`)은 매 검증 끝에 삭제되므로 영향 없음.

3. **`data_node._yfinance_fallback`이 한국 종목 suffix 미보정** (트러블슈팅 I, `data_node.py:101-128`) — `yf.Ticker(symbol)`에 `000020` 그대로 전달 → yfinance 인식 못 함 → empty hist → fallback도 실패. **구조 미완 (약함)**:
   - 어댑터 한 줄 추가하면 해결 (`yf_symbol = symbol + ".KS"` 또는 KOSDAQ ".KQ"); 단 PG 캐시가 있으면 fallback 자체가 호출 안 되므로 핵심 경로는 정상. price_cache 적재가 watchlist 등록 후 background로 끝나기 전 토론을 즉시 시작할 때만 영향. → 운영 품질 + 보조 경로 보강.

4. **`_search_news` / `_search_filings`의 score 머지가 distance 기준** (`evidence_retrieval.py:91-92`) — chroma의 distance는 작을수록 가까운 값. score 정렬은 ascending이라 정합 ✓. 단 두 컬렉션이 서로 다른 embedding 모델/차원이면 distance 분포가 비교 불가능해질 수 있음 — 현재는 둘 다 동일 `get_embedding_client()`로 768d 통일이라 OK. **유지 조건**으로 명시 필요.

5. **`debate_service._astream_with_config`의 `TypeError` fallback** (`debate_service.py:115-119`) — 그래프 구현체가 `astream(state, config)` 시그니처를 지원 안 하면 `astream(state)`로 폴백. 향후 LangGraph 버전 업 시 시그니처 변경 자동 흡수, 단 fake graph가 `astream(state)`만 받아도 동작 — 본 보고서의 `validate_debate_service.py:38-43`이 그것에 의존.

6. **`_record_usage`의 `usage_metadata` 형식 가정** (`llm_cache.py:124-146`) — OpenRouter 응답의 usage 위치가 LangChain 버전마다 다름. 3개 경로(`usage_metadata.input_tokens`, `response_metadata.token_usage.prompt_tokens`, `response_metadata.usage.prompt_tokens`)를 모두 시도 — 강건성 OK. 단 0/0이면 silent return이라 토큰 미과금 케이스가 invisible. 운영 진입 시 0/0 카운터 추가하면 모델 사이드 변경 감지 가능.

7. **`debate_checkpoint`의 statements 머지 정책** (`debate_checkpoint.py:66-73`) — base statements + patch statements를 단순 concat. 동일 round_order 중복은 검출 안 됨. 정상 흐름에서는 노드별로 새 statements만 yield하므로 OK이지만, 사용자가 같은 session_id로 토론을 재실행하면 statements가 누적될 수 있다. `api/debate.py`가 매번 새 `DebateSession.id`를 발급하므로 실 경로에서는 발생 안 함.

### 판정

**Stage 4 = 1차 구현 완료 + live 경로 검증 완료.** plan 본문이 명시한 Phase 1-5 + 토론 plan 영역이 모두 코드 + 검증 스크립트 + live `uvicorn + curl` 동작까지 닫혔다. 잔여 항목은 모두 *구조 미완*이 아닌 *운영 품질 보정* 또는 *조건부 보강*(suffix 어댑터 1줄)에 해당한다.
