# 계획 — 실시간(지연) 현재가 + 관심종목 새로고침 (추후 구현)

- 작성: 2026-06-20 / 상태: **설계만, 구현 보류**(우선순위 낮음)
- 배경: 프론트 피드가 "추가 시점 1회 수집"이라 옛 데이터가 보임 + 현재가 실시간 요구

---

## 1. 무료 API(yfinance)로 실시간 가능?
- 현재가 소스 = **yfinance(무료)**, 코드가 `is_delayed=True`로 항상 표시(`app/external/quote_client.py:54-55`).
- **진짜 실시간(틱) → 무료로 불가** (yfinance KRX 약 15~20분 지연).
- **지연 현재가 폴링 → 가능** — `IntradayQuoteService.get_latest_quote`(`app/domain/intraday_quote.py:28`)가 이미 Redis 캐시(장중 5분/장외 30분/주말 24h). **API 노출만 하면 됨.**
- 더 짧은 지연 원하면(옵션): Naver Finance 현재가 스크래핑(거의 실시간·비공식) 또는 증권사 실시간 API(유료/인증). 졸프엔 yfinance 지연 + "지연" 뱃지 권장.

## 2. 현재가 폴링 — 신설 `GET /api/stocks/{symbol}/quote`
- 내부: `IntradayQuoteService().get_latest_quote(symbol)` (Redis 캐시)
- 응답: `{symbol, price, prev_close, change, change_rate, volume, source, is_delayed, ts}` (QuoteSnapshot)
- 프론트: 현재가 숫자만 이걸로, **5~15초 폴링(장중)**, `is_delayed`면 "약 15분 지연" 라벨. 일봉 차트는 기존 `/prices` 유지.

## 3. 새로고침 버튼 — 신설 `POST /api/watchlists/{user_id}/refresh` (+개별 `/{symbol}/refresh`)
- 동작: 기존 `sync_watchlist_{news,financials,prices,filings,valuation}`(`watchlist_service.py`)를 **`BackgroundTasks` 비차단** 재실행 + **throttle**(Redis `refresh:last:{symbol}` → 최근 N분(예 10분) 내면 skip).
- 응답(즉시): `{"status":"refreshing","symbols":[...],"skipped":[...]}` 또는 202.
- 프론트: 버튼 클릭 → 호출 → "갱신 중…" → 수 초 후 `/feed` 재조회(또는 5~10s ×2~3회 폴링).

## 4. 프론트 핸드오프
| UI | 호출 | 트리거 |
|---|---|---|
| 현재가 숫자 | `GET /api/stocks/{symbol}/quote` | 5~15초 폴링(장중) |
| 일봉 차트 | `GET /api/stocks/{symbol}/prices` | 진입 시 1회(기존) |
| 피드 | `GET /api/watchlists/{user_id}/feed` | 진입 + 새로고침 후(기존) |
| 새로고침 버튼 | `POST /api/watchlists/{user_id}/refresh` | 클릭(비차단) |

**원칙**: 현재가=가벼운 폴링(지연) / 콘텐츠=버튼 시 비차단 재수집+throttle. 둘을 섞지 말 것.

## 5. 구현 시 작업(백엔드)
1. `GET /api/stocks/{symbol}/quote` — IntradayQuoteService 노출(market_data.py)
2. `POST /api/watchlists/{user_id}/refresh`(+개별) — BackgroundTasks + Redis throttle (watchlist.py)
3. (선택) `.env`에 `WATCHLIST_REFRESH_THROTTLE_SECONDS=600`
→ 프론트(별도 레포)는 §4 표대로.
