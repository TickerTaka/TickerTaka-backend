# 프론트 요청 API 구현 보고서

> 작성일: 2026-05-28  
> 대상: 프론트에서 요청한 대시보드/종목/뉴스/토론 조회 API  
> 작업 범위: 기존 FastAPI 백엔드에 조회용 GET API 추가

---

## 1. 요청받은 API 목록

프론트에서 필요한 API는 아래 8개였다.

```text
GET /api/debates
GET /api/tickers?q=...
GET /api/stocks/{symbol}
GET /api/stocks/{symbol}/prices
GET /api/stocks/{symbol}/news
GET /api/market/indexes
GET /api/news/recent
GET /api/dashboard/stats
```

---

## 2. 작업 전 상태

기존에 확인된 API는 아래 정도였다.

```text
POST /api/debates
GET /api/debates/{session_id}
POST /api/watchlists
GET /api/watchlists/{user_id}
GET /health
```

즉 프론트가 요청한 목록 중 기존에 완전히 있는 것은 없었고, 토론 상세 API만 이미 있었다.

---

## 3. 추가한 파일

### 3-1. `app/api/market_data.py`

신규 라우터 파일을 만들었다.

역할:

- 종목 검색
- 종목 상세
- 종목 가격 이력
- 종목 뉴스
- 최근 뉴스
- 시장 요약
- 대시보드 통계

추가된 API:

```text
GET /api/tickers
GET /api/stocks/{symbol}
GET /api/stocks/{symbol}/prices
GET /api/stocks/{symbol}/news
GET /api/news/recent
GET /api/market/indexes
GET /api/dashboard/stats
```

### 3-2. `app/schemas/market_data.py`

신규 응답 스키마 파일을 만들었다.

주요 스키마:

```text
TickerSearchResponse
StockDetailResponse
StockPricesResponse
NewsListResponse
MarketIndexesResponse
DashboardStatsResponse
DebateListResponse
```

Decimal, date, datetime, UUID 값을 FastAPI/Pydantic이 안정적으로 JSON 응답으로 변환할 수 있게 응답 모델을 명시했다.

---

## 4. 수정한 파일

### 4-1. `app/main.py`

신규 라우터를 FastAPI 앱에 등록했다.

변경:

```python
from app.api.market_data import router as market_data_router

app.include_router(market_data_router)
```

이 등록이 있어야 `app/api/market_data.py`에 만든 API들이 실제 서버에 노출된다.

### 4-2. `app/api/debate.py`

기존 토론 라우터에 토론 세션 목록 API를 추가했다.

추가:

```text
GET /api/debates
```

지원 쿼리:

```text
user_id: optional UUID
symbol: optional string
limit: optional int, 기본 20, 최대 100
```

응답에는 세션 ID, 사용자 ID, 종목, 종목명, 카테고리, 상태, 시작/완료 시각, 요약을 포함한다.

---

## 5. API별 구현 내용

### 5-1. `GET /api/debates`

목적:

- 토론 세션 목록 조회

사용 테이블:

```text
debate_session
ticker_metadata
moderator_summary
```

정렬:

```text
started_at DESC
```

필터:

```text
user_id
symbol
```

응답 예:

```json
{
  "items": [
    {
      "session_id": "uuid",
      "user_id": "uuid",
      "symbol": "005930",
      "symbol_name": "삼성전자",
      "category": "financial",
      "status": "completed",
      "started_at": "2026-05-28T10:00:00+09:00",
      "completed_at": "2026-05-28T10:01:00+09:00",
      "summary_content": "..."
    }
  ]
}
```

### 5-2. `GET /api/tickers?q=...`

목적:

- 종목 검색

사용 테이블:

```text
ticker_metadata
```

검색 대상:

```text
symbol
name_kr
name_en
```

조건:

```text
is_active = true
```

지원 쿼리:

```text
q: 검색어, optional
limit: 기본 20, 최대 100
```

응답 예:

```json
{
  "items": [
    {
      "symbol": "005930",
      "name_kr": "삼성전자",
      "name_en": "Samsung Electronics",
      "market": "KOSPI",
      "sector": "전기전자",
      "industry": "반도체"
    }
  ]
}
```

### 5-3. `GET /api/stocks/{symbol}`

목적:

- 종목 상세 조회

사용 테이블:

```text
ticker_metadata
price_cache
financial_cache
technical_indicator_cache
```

포함 내용:

```text
종목 메타데이터
최신 가격
최신 재무 스냅샷
최신 기술지표
```

없는 symbol이면:

```text
404 ticker not found
```

### 5-4. `GET /api/stocks/{symbol}/prices`

목적:

- 종목 가격 이력 조회

사용 테이블:

```text
price_cache
```

정렬:

```text
DB 조회는 price_date DESC
응답은 차트 사용 편의를 위해 오래된 날짜 → 최신 날짜 순서로 reverse
```

지원 쿼리:

```text
limit: 기본 260, 최대 1000
```

없는 symbol이면:

```text
404 ticker not found
```

### 5-5. `GET /api/stocks/{symbol}/news`

목적:

- 특정 종목 뉴스 조회

사용 테이블:

```text
news_cache
```

정렬:

```text
published_at DESC NULLS LAST
retrieved_at DESC
```

지원 쿼리:

```text
limit: 기본 20, 최대 100
```

없는 symbol이면:

```text
404 ticker not found
```

### 5-6. `GET /api/news/recent`

목적:

- 전체 최근 뉴스 조회

사용 테이블:

```text
news_cache
```

정렬:

```text
published_at DESC NULLS LAST
retrieved_at DESC
```

지원 쿼리:

```text
limit: 기본 20, 최대 100
```

### 5-7. `GET /api/market/indexes`

목적:

- 대시보드용 시장 요약 조회

중요:

현재 DB에는 실제 KOSPI/KOSDAQ 지수 캐시 테이블이 없다. 그래서 이 API는 실제 지수값이 아니라, 보유한 종목별 최신 가격 데이터를 기반으로 시장별 요약값을 계산한다.

사용 테이블:

```text
ticker_metadata
price_cache
```

계산 방식:

```text
market별 최신 price_cache 1건씩 집계
average_change_rate = 시장 내 종목 평균 등락률
advancers = 등락률 > 0 종목 수
decliners = 등락률 < 0 종목 수
unchanged = 등락률 = 0 종목 수
constituents = 집계된 종목 수
```

응답 예:

```json
{
  "items": [
    {
      "market": "KOSPI",
      "name": "KOSPI",
      "average_change_rate": 0.42,
      "advancers": 120,
      "decliners": 80,
      "unchanged": 10,
      "constituents": 210
    }
  ]
}
```

나중에 실제 지수값이 필요하면 별도 테이블이 필요하다.

예:

```text
market_index_cache
    index_code
    index_name
    trade_date
    close_value
    change_value
    change_rate
```

### 5-8. `GET /api/dashboard/stats`

목적:

- 대시보드 상단 통계 조회

사용 테이블:

```text
ticker_metadata
news_cache
debate_session
price_cache
```

응답 필드:

```text
ticker_count
active_ticker_count
news_count
debate_session_count
completed_debate_count
latest_news_at
latest_price_date
```

---

## 6. 검증 과정

### 6-1. 첫 번째 컴파일 시도

처음 실행한 명령:

```bash
python -m compileall app/api app/schemas app/main.py
```

결과:

```text
zsh:1: command not found: python
```

원인:

- 현재 쉘에서 `python` 명령이 없고 `python3`만 사용 가능했다.

조치:

```bash
python3 -m compileall app/api app/schemas app/main.py
```

### 6-2. 두 번째 컴파일 시도

실행:

```bash
python3 -m compileall app/api app/schemas app/main.py
```

결과:

```text
PermissionError: [Errno 1] Operation not permitted:
'/Users/ohheungchan/Library/Caches/com.apple.python/...'
```

원인:

- macOS 기본 Python이 bytecode cache를 `~/Library/Caches/com.apple.python/...` 아래에 쓰려고 했다.
- 현재 작업 환경은 workspace-write 샌드박스라 워크스페이스 밖인 `~/Library/Caches`에 쓰기 권한이 없다.

조치:

```bash
PYTHONPYCACHEPREFIX=.pycache python3 -m compileall app/api app/schemas app/main.py
```

이렇게 Python bytecode cache 위치를 워크스페이스 내부 `.pycache`로 바꿔서 컴파일했다.

결과:

```text
compileall 성공
```

### 6-3. `.pycache`를 삭제한 이유

검증 때문에 워크스페이스 루트에 임시 `.pycache` 디렉터리가 생겼다.

이 파일들은 소스 코드가 아니라 Python 컴파일 캐시다. 커밋하거나 유지할 필요가 없고, 작업 트리를 더럽히므로 삭제했다.

실행:

```bash
rm -rf .pycache
```

삭제 대상:

```text
.pycache
```

주의:

- 앱 코드나 사용자 파일을 삭제한 것이 아니다.
- `PYTHONPYCACHEPREFIX=.pycache`로 인해 생긴 임시 컴파일 캐시만 삭제했다.

### 6-4. FastAPI 앱 import 스모크 테스트 시도

실행:

```bash
PYTHONPYCACHEPREFIX=.pycache python3 -c "from app.main import app; ..."
```

결과:

```text
ModuleNotFoundError: No module named 'fastapi'
```

가상환경으로도 시도:

```bash
PYTHONPYCACHEPREFIX=.pycache .venv/bin/python -c "from app.main import app; ..."
```

결과:

```text
ModuleNotFoundError: No module named 'fastapi'
```

원인:

- 현재 로컬 Python 환경과 `.venv` 모두에 `fastapi`가 설치되어 있지 않았다.
- 그래서 TestClient나 앱 import 기반 런타임 스모크 테스트는 수행하지 못했다.

수행 가능한 검증:

```text
문법 컴파일 체크는 통과
FastAPI 런타임 import 체크는 의존성 미설치로 미수행
```

---

## 7. 현재 주의사항

### 7-1. 실제 지수 API 아님

`GET /api/market/indexes`는 실제 KOSPI/KOSDAQ 지수값이 아니라 시장별 종목 평균 요약이다. 프론트에서 실제 지수 차트를 원하면 별도 지수 수집/캐시가 필요하다.

### 7-2. 응답 형태는 1차 계약용

프론트가 원하는 필드명이 따로 있으면 스키마명을 맞춰야 한다.

현재는 일관성을 위해 목록 응답을 모두 아래 형태로 맞췄다.

```json
{
  "items": []
}
```

### 7-3. 기존 작업물은 건드리지 않음

작업 전부터 이미 수정되어 있던 아래 파일들은 이번 API 작업과 별개로 존재했다.

```text
app/domain/evidence_indexing.py
app/domain/evidence_retrieval.py
app/external/dart/client.py
app/repositories/filing_cache_repository.py
scripts/reset_filing_collection.py
scripts/validate_filing_evidence_retrieval.py
```

이번 API 작업에서 직접 수정한 파일은 아래다.

```text
app/api/debate.py
app/api/market_data.py
app/main.py
app/schemas/market_data.py
```

---

## 8. 프론트 전달용 요약

아래 API는 백엔드에 추가되어 있다.

```text
GET /api/debates?user_id=&symbol=&limit=
GET /api/tickers?q=&limit=
GET /api/stocks/{symbol}
GET /api/stocks/{symbol}/prices?limit=
GET /api/stocks/{symbol}/news?limit=
GET /api/market/indexes
GET /api/news/recent?limit=
GET /api/dashboard/stats
```

목록 응답은 기본적으로 아래 형태다.

```json
{
  "items": []
}
```

없는 종목에 대한 상세/가격/뉴스 요청은 `404`를 반환한다.
