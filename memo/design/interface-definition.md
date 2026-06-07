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
| `avg_price` | float? | 기본 null |

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

- `DebateStatementResponse`: `agent_role:str`, `round:str`, `round_order:int`, `content:str`, `model_used:str`, `evidence_count:int=0`
- 오류: ticker 없음 → `404`, 동일 user/symbol 진행중(가드 거절) → `409`, 런타임 실패 → `503`/`500`

### GET `/api/debates`

- 목적: 토론 세션 목록
- 쿼리: `user_id:UUID?`, `symbol:str?` (+ limit)
- 응답 `DebateListResponse`: `items: DebateListItem[]`
- `DebateListItem`: `session_id`, `user_id`, `symbol`, `symbol_name?`, `category`, `status`, `started_at?`, `completed_at?`, `summary_content?` (※ statements/key_points 미포함 — 목록 경량 버전)

### GET `/api/debates/{session_id}`

- 목적: 개별 토론 결과 (statements 포함 full)
- 응답 `DebateSessionResponse` (위와 동일)

### DELETE `/api/debates/{session_id}`

- 목적: 토론 세션 삭제 (CASCADE로 statement/summary/evidence/note 동반 삭제)
- 응답: `{status: "deleted", session_id}`

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
| watchlist 중복 / debate 가드 거절 | `409` |
| external / runtime 실패 | `503` (일부 `500`) |
| health check | 도메인 상태와 분리된 단순 프로세스 헬스 |

---

## 6. 추후 확장 예정 인터페이스

- SSE debate stream endpoint (`text/event-stream`, astream 청크 forward)
- MCP publish endpoint 또는 내부 worker 경로
- RAGAS 평가 결과 조회 endpoint (현재 결과는 로그만, 영속화 미완)
