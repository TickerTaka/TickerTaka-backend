# 2026-05-23 Stage 3 Price/Financial Cache 구현

## 범위

`memo/process/plan-implementation-order.md`의 Stage 3 중:

- `3.1 price-cache`
- `3.2 financial-cache`

이번 작업에서는 `filing-cache`는 제외했다.

## 구현 내용

### 1. Price / Technical Cache

- `app/external/krx_client.py`
  - `PyKrxClient`
  - `DailyPriceRecord`
- `app/repositories/price_cache_repository.py`
  - `(symbol, price_date)` upsert
  - row trim
- `app/repositories/technical_indicator_cache_repository.py`
  - `(symbol, indicator_date)` upsert
  - row trim
- `app/domain/technical_indicator.py`
  - `MA20`, `MA60`, `MA120`
  - `RSI14`
  - `MACD`, `MACD signal`, `MACD histogram`
  - `volume_ma20`
- `app/domain/price_ingestion.py`
  - 초기 1년 백필
  - refresh 시 최근 5거래일 덮어쓰기
  - 가격 적재 후 지표 재계산
  - Redis lock / cooldown
- `app/domain/price_cache_scheduler.py`
  - watchlist 대상 refresh sweep
  - cleanup sweep

### 2. Financial Cache

- `app/external/dart/corp_code.py`
  - `stock_code -> corp_code` 매핑
  - 로컬 JSON 캐시
- `app/external/dart/client.py`
  - OpenDART `fnlttSinglAcnt.json` 래퍼
  - `CFS -> OFS` fallback
- `app/external/dart/financial_account_map.py`
  - 핵심 계정명 매핑
- `app/repositories/financial_cache_repository.py`
  - `(symbol, fiscal_year, fiscal_quarter)` upsert
  - row trim
- `app/domain/financial_ratios.py`
  - `roe`
  - `debt_ratio`
- `app/domain/financial_ingestion.py`
  - 최근 5년 분기 데이터 백필
  - Redis lock / cooldown
  - `roe`, `debt_ratio` 계산
  - `PER`, `PBR`은 가격 의존값이라 이번 단계에서는 `NULL` 유지
- `app/domain/financial_cache_scheduler.py`
  - watchlist 대상 refresh sweep
  - cleanup sweep

### 3. Watchlist 연동

- `app/domain/watchlist_service.py`
  - `sync_watchlist_prices()`
  - `sync_watchlist_financials()`
- `app/api/watchlist.py`
  - watchlist 생성 후
    - `sync_watchlist_news`
    - `sync_watchlist_prices`
    - `sync_watchlist_financials`
    순서로 background task enqueue

## Filing과의 경계

이번 작업에서 `filing-cache`와 겹치는 공용부는 다음만 도입했다.

- `app/external/dart/corp_code.py`
- `app/external/dart/client.py`

즉 `filing-cache` 구현 시 재사용 가능하지만, 이번 단계에서는:

- `list.json`
- `document.xml`
- filing parser
- filing ingestion
- filing Chroma 연동

은 전혀 건드리지 않았다.

## 검증 스크립트

- `scripts/validate_technical_indicator.py`
- `scripts/validate_price_ingestion.py`
- `scripts/validate_price_cache_scheduler.py`
- `scripts/validate_financial_ingestion.py`
- `scripts/run_price_cache_scheduler.py`
- `scripts/run_financial_cache_scheduler.py`

## 상태

- `python3 -m compileall app scripts` 통과
- 로컬 검증 통과:
  - `python -m scripts.validate_technical_indicator`
  - `python -m scripts.validate_price_ingestion`
  - `python -m scripts.validate_price_cache_scheduler`
  - `python -m scripts.validate_financial_ingestion`
  - `python -m scripts.validate_financial_cache_scheduler`
- 실 API / 실 DB 검증은 아직 하지 않음
- 현재 단계는 **구조 구현 + fake client 검증 완료**까지

## PER/PBR 정책

- `financial_cache`는 **재무제표 원숫자 + ROE + debt_ratio**까지만 저장
- `PER`, `PBR`은 가격이 바뀌면 함께 바뀌므로 `financial_cache`의 고정 저장값으로 다루지 않음
- 이번 단계 구현에서는 `PER`, `PBR`을 `NULL`로 유지
- 후속 단계에서 `price_cache + financial_cache`를 조합하는 별도 계산 정책이 정해질 때만 보강

## 다음

1. `pykrx` 설치 후 `validate_price_ingestion.py` 실환경 검증
2. `DART_API_KEY`가 있는 환경에서 `financial` live 검증
3. `PER/PBR`은 후속 단계에서 `price_cache` 결합 계산 정책 결정 후 별도 구현
4. 이후 `filing-cache` 구현 또는 Stage 4 토론 도메인 연계
