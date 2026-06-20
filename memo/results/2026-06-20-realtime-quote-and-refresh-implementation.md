# 구현 결과 — 지연 현재가 폴링 + 관심종목 새로고침

- 작성: 2026-06-20 / 브랜치: `feat/realtime-quote-refresh`
- 계획: [2026-06-20-realtime-quote-and-refresh-plan.md](/home/syt07203/TickerTaka-backend/memo/plans/2026-06-20-realtime-quote-and-refresh-plan.md)
- 상태: **백엔드 구현 완료** — 프론트 핸드오프만 남음

---

## 1. 무엇을 했나

계획의 §5 백엔드 작업 3개를 모두 구현했다. **주가 소스는 yfinance 그대로 유지**하고, 두 개의 갱신 경로를 명확히 분리했다.

- **현재가 = 가벼운 폴링**: 관심종목 화면에서 현재가 숫자만 짧은 주기로 당겨온다.
- **콘텐츠(뉴스·공시·지표) = 새로고침 버튼**: 무거운 재수집을 백그라운드로 돌리고 즉시 반환(비차단) + throttle.

두 경로를 섞지 않는 게 핵심 설계 원칙이다.

## 2. 신설 엔드포인트

| 메서드 | 경로 | 용도 | 성격 |
|---|---|---|---|
| `GET` | `/api/stocks/{symbol}/quote` | 지연 현재가 스냅샷 | 가벼움(폴링) |
| `POST` | `/api/watchlists/{user_id}/refresh` | 관심종목 전체 새로고침 | 무거움(비차단) |
| `POST` | `/api/watchlists/{user_id}/{symbol}/refresh` | 개별 종목 새로고침 | 무거움(비차단) |

### 2-1. 현재가 — `GET /api/stocks/{symbol}/quote`

- 내부: 기존 `IntradayQuoteService().get_latest_quote(symbol)`를 **노출만** 했다(`app/domain/intraday_quote.py`).
- 캐시: Redis TTL — **장중(09:00~15:30) 5분 / 장외 30분 / 주말 24h**. 폴링이 5~15초여도 캐시 안이면 yfinance를 다시 때리지 않는다.
- 소스: yfinance(무료) → KRX 기준 약 15~20분 지연. 응답에 `is_delayed: true`가 항상 실린다.
- 응답(`QuoteResponse`):

```json
{
  "symbol": "005930",
  "price": 81200.0,
  "prev_close": 80500.0,
  "change": 700.0,
  "change_rate": 0.87,
  "volume": 12345678,
  "source": "yfinance",
  "is_delayed": true,
  "ts": "2026-06-20T05:30:00+00:00"
}
```

- 오류: 미등록 종목 `404`, yfinance 조회 실패 `502`.

### 2-2. 새로고침 — `POST /api/watchlists/{user_id}/refresh`

- 동작: 관심종목 전체에 대해 기존 `sync_watchlist_{news,financials,prices,filings,valuation}`(`app/domain/watchlist_service.py`)를 `BackgroundTasks`로 큐잉하고 **즉시 `202` 반환**(비차단).
- **throttle**: Redis `SET NX EX`로 최근 `WATCHLIST_REFRESH_THROTTLE_SECONDS`(기본 600초=10분) 안에 이미 갱신된 종목은 건너뛴다. 연타·중복 수집 비용 방어. 원자적이라 동시 클릭에도 한 번만 큐잉된다.
- Redis 없거나 throttle=0이면 throttle 비활성(항상 수집).
- 응답(`WatchlistRefreshResponse`):

```json
{ "status": "refreshing", "symbols": ["005930", "000660"], "skipped": ["035720"] }
```
- `symbols` = 이번에 재수집을 시작한 종목, `skipped` = throttle로 건너뛴 종목.
- 개별 버전 `/{user_id}/{symbol}/refresh`는 해당 종목이 그 유저의 관심종목이 아니면 `404`.

## 3. 변경 파일

| 파일 | 변경 |
|---|---|
| `app/config.py` | `WATCHLIST_REFRESH_THROTTLE_SECONDS`(기본 600, `ge=0`) 추가 |
| `app/schemas/market_data.py` | `QuoteResponse` 추가 |
| `app/schemas/watchlist.py` | `WatchlistRefreshResponse` 추가 |
| `app/api/market_data.py` | `GET /api/stocks/{symbol}/quote` |
| `app/api/watchlist.py` | refresh 2개 + 헬퍼 `_enqueue_symbol_sync`, `_claim_refresh_slot`. 기존 `create_watchlist`도 헬퍼로 리팩터(중복 제거) |
| `memo/plans/...-realtime-quote-and-refresh-plan.md` | 상태를 "구현 완료"로 갱신 |

## 4. 검증

- `python -m py_compile` 통과(5개 파일).
- WSL venv에서 `from app.main import app` 정상 import, 신설 라우트 3개 모두 등록 확인:
  - `POST /api/watchlists/{user_id}/refresh`
  - `POST /api/watchlists/{user_id}/{symbol}/refresh`
  - `GET /api/stocks/{symbol}/quote`
- 라우트 충돌 없음: `/{user_id}/refresh`(2세그먼트) vs `/{user_id}/{symbol}/refresh`(3세그먼트), `user_id`는 UUID 타입이라 "refresh"가 매칭되지 않음.

## 5. 남은 일 (프론트 핸드오프)

- 프론트(별도 레포)는 계획 §4 표대로 연결: 현재가=폴링, 차트=기존 `/prices`, 피드=`/feed`, 버튼=`/refresh`.
- 상세는 본 폴더 동일자 핸드오프 노트 참고(아래 §6).

## 6. 관련 문서

- 계획: [2026-06-20-realtime-quote-and-refresh-plan.md](/home/syt07203/TickerTaka-backend/memo/plans/2026-06-20-realtime-quote-and-refresh-plan.md)
