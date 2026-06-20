# 인터페이스 정의서

## 문서 목적

현재 백엔드가 외부에 제공하는 HTTP 인터페이스를 **실제 Pydantic 스키마(`app/schemas/*.py`) 기준 1:1**로 정의한다.
- 라우터: `app/api/watchlist.py`, `app/api/market_data.py`, `app/api/debate.py`
- 스키마: `app/schemas/watchlist.py`, `app/schemas/market_data.py`, `app/schemas/debate.py`

## 공통

- Base URL: `/`
- Health Check: `GET /health` → `{"status": "ok"}` (내부 도메인 상태와 분리된 프로세스 헬스)
- 주 라우터 prefix:
  - `/api/watchlists` (`watchlist.py`)
  - `/api/debates` (`debate.py`)
  - 그 외 market-data 라우터는 prefix 없이 `/api/...` 직접 정의(`market_data.py`)
- 표기: `?` = optional/nullable, `=v` = 기본값. UUID/datetime/date는 ISO 직렬화.

---

## 1. Watchlist API

### POST `/api/watchlists`

- 목적: 관심종목 추가
- 요청 `WatchlistCreateRequest`:

| 필드 | 타입 | 제약 |
|---|---|---|
| `user_id` | UUID | 필수 |
| `symbol` | str | 필수, 1–30자 |
| `memo` | str? | ≤1000자, 기본 null |

- 응답 `WatchlistCreateResponse` (201):

| 필드 | 타입 | 비고 |
|---|---|---|
| `watchlist` | `WatchlistItemResponse` | 아래 |
| `sync_enqueued` | bool | 기본 true, enqueue 실패 시 false |

- `WatchlistItemResponse`: `id:UUID`, `user_id:UUID`, `symbol:str`, `memo:str?`, `created_at:datetime`, `ticker_name_kr:str?`
- 후속 동작: news/financial/price/filing/valuation background sync 5종 enqueue
- 오류: user/ticker 없음 → `404`, 중복(`uq_watchlist`) → `409`

### GET `/api/watchlists/{user_id}`

- 목적: 사용자 관심종목 목록
- 응답 `WatchlistListResponse`: `items: WatchlistItemResponse[]`
- 오류: user 없음 → `404`

### GET `/api/watchlists/{user_id}/feed`

- 목적: 관심종목 기반 뉴스+공시 통합 피드
- 쿼리: `limit:int = 20` (1–100으로 capped)
- 응답 `WatchlistFeedResponse`: `items: WatchlistFeedItem[]`
- `WatchlistFeedItem`: `id:UUID`, `symbol:str`, `symbol_name:str?`, `kind:str`("news"|"filing"), `title:str`, `summary:str?`, `source_name:str?`, `source_url:str`, `published_at:datetime?`
  - **감성분석 필드(분석 전이면 null/빈배열)**: `sentiment:str?`("positive"|"negative"|"neutral"|"mixed"), `impact_score:int?`(-2~+2), `confidence:float?`(0~1), `event_type:str?`(공시 사건유형), `analysis_summary:str?`, `key_points:str[]`, `risks:str[]`, `evidence:str[]` — `evidence_analysis` 결과(동기 FinBERT baseline → 비동기 Qwen 보강)

### DELETE `/api/watchlists/{user_id}/{symbol}`

- 목적: 관심종목 삭제 (종목 캐시는 유지, 관계만 제거)
- 응답: `{status, user_id, symbol}`

---

## 2. Market Data API

### GET `/api/tickers`

- 목적: 종목 검색
- 쿼리: `q:str = ""` (≤100자), `limit:int = 20` (1–100)
- 응답 `TickerSearchResponse`: `items: TickerSearchItem[]`
- `TickerSearchItem`: `symbol`, `name_kr`, `name_en?`, `market`, `sector?`, `industry?`

### GET `/api/stocks/{symbol}`

- 목적: 종목 상세
- 응답 `StockDetailResponse`: `symbol`, `name_kr`, `name_en?`, `market`, `sector?`, `industry?`, `currency?`, `latest_price:PricePoint?`, `latest_financial:FinancialSnapshot?`, `latest_technical:TechnicalSnapshot?`
- `PricePoint`: `date`, `open?`, `high?`, `low?`, `close`, `adjusted_close?`, `volume:int?`, `change_rate?`
- `FinancialSnapshot`: `fiscal_year`, `fiscal_quarter?`, `revenue?`, `operating_profit?`, `net_income?`, `total_assets?`, `total_liabilities?`, `total_equity?`, `per?`, `pbr?`, `roe?`, `debt_ratio?`
- `TechnicalSnapshot`: `date`, `ma20?`, `ma60?`, `ma120?`, `rsi14?`, `macd?`, `macd_signal?`, `macd_hist?`, `volume_ma20?`

### GET `/api/stocks/{symbol}/prices`

- 목적: 가격 시계열
- 쿼리: `limit:int = 260` (1–1000)
- 응답 `StockPricesResponse`: `symbol`, `prices: PricePoint[]`

### GET `/api/stocks/{symbol}/news`

- 목적: 종목별 뉴스
- 쿼리: `limit:int = 20` (1–100)
- 응답 `NewsListResponse`: `items: NewsItem[]`
- `NewsItem`: `id:UUID`, `symbol`, `title`, `summary?`, `source_name?`, `source_url`, `published_at?`, `retrieved_at`

### GET `/api/stocks/{symbol}/filings`

- 목적: 종목별 공시
- 쿼리: `limit:int = 20` (1–100)
- 응답 `FilingListResponse`: `items: FilingItem[]`
- `FilingItem`: `id:UUID`, `symbol`, `filing_title`, `filing_type?`, `summary?`, `source_url`, `disclosed_at?`, `retrieved_at`

### GET `/api/news/recent`

- 목적: 최근 전체 뉴스
- 쿼리: `limit:int = 20` (1–100)
- 응답 `NewsListResponse` (위와 동일)

### GET `/api/market/indexes`

- 목적: 시장 지수 통계(KOSPI/KOSDAQ 등)
- 응답 `MarketIndexesResponse`: `items: MarketIndexItem[]`
- `MarketIndexItem`: `market`, `name`, `average_change_rate?`, `advancers:int=0`, `decliners:int=0`, `unchanged:int=0`, `constituents:int=0`

### GET `/api/dashboard/stats`

- 목적: 대시보드 통계
- 응답 `DashboardStatsResponse`: `ticker_count`, `active_ticker_count`, `news_count`, `debate_session_count`, `completed_debate_count`, `latest_news_at:datetime?`, `latest_price_date:date?`

---

## 3. Debate API

### POST `/api/debates`

- 목적: 토론 시작 (요청-응답형, 완료 후 일괄 반환)
- 요청 `DebateCreateRequest`:

| 필드 | 타입 | 제약 |
|---|---|---|
| `user_id` | UUID | 필수 |
| `symbol` | str | 필수, 1–30자 |
| `category` | enum `DebateCategory` | technical/financial/market/macro/synthesis |
| `decision_agent` | str | 선택, 기본 `moderator` (`moderator`\|`judge`) — 토론 종결 판정 주체 선택 |

> `decision_agent="judge"`면 토론 종료 후 `moderator_summary` 대신 **`judge_agent`** 노드가 승패/판정을 내린 뒤 요약한다(아래 §3 디베이트 그래프·시퀀스 참조). `POST /api/debates/sessions`(스트리밍)도 동일 바디.

- 응답 `DebateSessionResponse` (201):

| 필드 | 타입 |
|---|---|
| `session_id` | UUID |
| `user_id` | UUID |
| `symbol` | str |
| `category` | str |
| `status` | str (pending/running/completed/failed) |
| `started_at` | datetime? |
| `completed_at` | datetime? |
| `summary_content` | str? |
| `key_points` | str[] (기본 []) |
| `statements` | `DebateStatementResponse[]` |
| `notion_page_id` | str? (Notion 발행 시) |
| `notion_page_url` | str? |
| `notion_published_at` | datetime? |

- `DebateStatementResponse`: `agent_role:str`, `round:str`, `round_order:int`, `content:str`, `model_used:str`, `evidence_count:int=0`
- ※ `notion_*` 3필드로 프론트는 "노션에 저장 ↔ 노션에서 보기" 버튼 상태를 토글
- 오류: ticker 없음 → `404`, 동일 user/symbol 진행중(가드 거절) → `409`, 런타임 실패 → `503`/`500`

### POST `/api/debates/sessions`

- 목적: 토론 **세션 사전 생성**(스트리밍 토론 준비). 세션 row를 `pending`으로 만들고 즉시 반환 → 이후 `GET /{session_id}/stream`으로 실행/스트리밍.
- 요청 `DebateCreateRequest` (위 POST `/api/debates`와 동일): `user_id`, `symbol`, `category`
- 응답 `DebatePrepareResponse` (201):

| 필드 | 타입 |
|---|---|
| `session_id` | UUID |
| `user_id` | UUID |
| `symbol` | str |
| `category` | str |
| `status` | str (`pending`) |
| `started_at` | datetime? |

- ※ 일괄 반환형 `POST /api/debates`와 달리, 이 엔드포인트는 **SSE 스트리밍용** 세션만 선생성한다(실제 토론 실행은 stream에서 시작).
- 오류: ticker 없음 → `404`

### GET `/api/debates`

- 목적: 토론 세션 목록
- 쿼리: `user_id:UUID?`, `symbol:str?` (+ limit)
- 응답 `DebateListResponse`: `items: DebateListItem[]`
- `DebateListItem`: `session_id`, `user_id`, `symbol`, `symbol_name?`, `category`, `status`, `started_at?`, `completed_at?`, `summary_content?` (※ statements/key_points 미포함 — 목록 경량 버전)

### GET `/api/debates/{session_id}`

- 목적: 개별 토론 결과 (statements 포함 full)
- 응답 `DebateSessionResponse` (위와 동일)

### GET `/api/debates/{session_id}/stream`

- 목적: 토론을 **SSE(`text/event-stream`)로 실시간 스트리밍**. LangGraph `astream` 노드 산출을 청크 단위로 즉시 forward(일괄 반환 아님).
- 쿼리: `decision_agent: "moderator" | "judge"` (기본 `moderator`)
- 응답: `EventSourceResponse` (sse-starlette). 이벤트:

| event | data |
|---|---|
| `session_started` | session_id, symbol, symbol_name, category, decision_agent, status |
| `statement` | agent_role, round, round_order, content, model_used, evidence_count |
| `summary` | session_id, summary_content, key_points[] |
| `done` | session_id, status |
| `error` | session_id, status(`rejected`/`failed`), message |

- **세션 상태별 분기**:
  - `pending` → 실시간 실행하며 스트리밍(`running`으로 전환)
  - `completed` → DB의 발언/요약을 읽어 **replay**(이벤트에 `replay:true`)
  - `failed` → `error` 이벤트
  - `running` → `409`(이미 실행 중)
- **연결 정리**: 클라이언트 disconnect(`CancelledError`) 시 `fail_session_if_running`으로 세션을 실패 처리해 좀비 세션 방지.
- 오류: 세션/ticker 없음 → `404`, 이미 실행 중 → `409`

### DELETE `/api/debates/{session_id}`

- 목적: 토론 세션 삭제 (CASCADE로 statement/summary/evidence/note 동반 삭제)
- 응답: `{status: "deleted", session_id}`

### POST `/api/debates/{session_id}/publish/notion`

- 목적: 완료된 토론을 **Notion DB row(page)로 발행** (버튼 기반 온디맨드, MCP 경유)
- 요청: body 없음 (path `session_id`)
- 응답 `DebateNotionPublishResponse`:

| 필드 | 타입 |
|---|---|
| `session_id` | UUID |
| `notion_page_id` | str |
| `notion_page_url` | str |
| `notion_published_at` | datetime |

- **멱등**: 이미 발행된 세션은 새 페이지 생성 없이 **기존 값 그대로 반환**
- 동작: `app/integrations/notion_mcp.py`(MCP client) → self-host Notion MCP server(stdio) → `API-post-page`. 속성은 DB property, 요약/발언/근거는 페이지 본문 block
- 오류: 세션 없음 → `404`, `completed` 아님/요약 미생성 → `409`, MCP/Notion 발행 실패 → `502`(본문에 실제 오류 메시지, 토론 본체는 rollback로 보존)

---

## 4. 내부 서비스 인터페이스

### Watchlist background sync (`POST /api/watchlists` 후속)

- `sync_watchlist_news` / `sync_watchlist_financials` / `sync_watchlist_prices` / `sync_watchlist_filings` / `sync_watchlist_valuation`
- FastAPI `BackgroundTasks` 기반(별도 큐 아님), 본 요청 트랜잭션과 분리

### Debate runtime (`POST /api/debates` 후속)

- `DebateExecutionService.run_session(session_id, user_id, symbol, symbol_name, category, ...)`
- 진입 시 `RuntimeGuard.try_start_session`(단일비행 락), 종료 시 `end_session`

---

## 5. 오류 처리 정책

| 상황 | 코드 |
|---|---|
| ticker / user / watchlist not found | `404` |
| watchlist 중복 / debate 가드 거절 / 미완료 세션 발행 | `409` |
| external / runtime 실패 | `503` (일부 `500`) |
| MCP/Notion 발행 실패 | `502` (본체 토론은 비전파·rollback) |
| health check | 도메인 상태와 분리된 단순 프로세스 헬스 |

---

## 6. 추후 확장 예정 인터페이스

- RAGAS 평가 결과 **조회** endpoint — 결과는 이미 `debate_eval_result`에 **영속화됨**(배치 `run_ragas_eval.py` + 리포트 `reports/ragas-<sha>.json`), 조회 API만 미구현
- 뉴스/공시 **감성·투자분석**은 구현 완료 — `GET /api/watchlists/{user_id}/feed`의 `WatchlistFeedItem`에 노출(§1). 전용 조회 endpoint는 선택 확장
- ※ **SSE debate stream**(`GET /api/debates/{session_id}/stream`)·**세션 사전생성**(`POST /api/debates/sessions`)은 §3에 **정식 구현 완료**(추후 확장에서 제외)
- ※ Notion 발행 endpoint는 §3에 **정식 구현 완료**(추후 확장에서 제외)
