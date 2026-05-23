# 2026-05-23 Stage 3 Price/Financial Cache 구현

## 범위

`memo/process/plan-implementation-order.md`의 Stage 3 중:

- `3.1 price-cache`
- `3.2 financial-cache`

이번 작업에서는 `filing-cache`는 제외했다.

참고:
- filing 수동 통합 결과는 별도 문서
  - `memo/results/2026-05-23-stage3-filing-cache-manual-merge.md`

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

## 추가 반영

- `scripts/refresh_corp_code_map.py`
  - `CorpCodeProvider().load_mapping(force_refresh=True)` 수동 갱신 진입점 추가
- `app/external/dart/client.py`
  - `tenacity` 기반 retry/backoff 추가
  - `dart-api-count:{KST date}` Redis 호출량 카운터 추가
- `app/domain/financial_ingestion.py`
  - `mode="refresh"`는 최근 `2년`만 재조회하도록 축소
  - `PER/PBR` 후속 분리 정책 주석 명시

---

## 검증/보완 메모 (2026-05-23, plan 대조)

`price-cache-ingestion-plan.md`(Phase 1-4)와 `financial-cache-ingestion-plan.md`(Phase 0-3, Phase 4는 후속)의 닫힘 기준에 대해, 본 보고서가 명시한 산출물은 거의 모두 존재한다. 단 plan-implementation-order.md의 산출물 목록과 대조 시 누락이 1건 있고, 코드 디테일에서 보강 권장 항목이 다수다.

### 정합성 확인 (OK)

- `PriceIngestionService`: `INITIAL_BACKFILL_DAYS=365`, `MAX_CACHE_ROWS=1260`, `COOL_DOWN_MINUTES=60`, `RECENT_OVERWRITE_DAYS=5` 모두 plan 정책 일치
- `change_rate` 직접 계산(`(close - prev_close) / prev_close * 100`) — plan의 "전일 종가 대비 직접 계산으로 고정"과 일치 (`price_ingestion.py:121-123`)
- `adjusted_close=None` 고정 — plan의 "초기 구현은 NULL 고정" 정합 (`price_ingestion.py:132`)
- `build_indicator_rows`의 MA20/60/120, RSI14, MACD(12/26/9), volume_ma20 모두 plan 명세 일치, NaN 처리도 `_to_optional_float`로 안전 (`technical_indicator.py:22-39`)
- `FinancialIngestionService`: `INITIAL_BACKFILL_YEARS=5`, `MAX_CACHE_ROWS=60`, `COOLDOWN_HOURS=6` — plan 정합
- `compute_roe` / `compute_debt_ratio`가 `total_equity in (None, 0)` 분기로 0 division 방지 (`financial_ratios.py:5,11`)
- `CorpCodeProvider.load_mapping`: `seeds/corp_code_map.json` 캐시 우선 → 만료(1년)/없을 때만 DART zip 다운로드 — plan "부팅 시 로컬 캐시 우선" 정확히 구현 (`corp_code.py:25-33`)
- `DartClient._fetch_single_period`의 `CFS → OFS` fallback이 plan의 "연결재무 우선, 별도 fallback" 정합 (`client.py:69-87`)
- `FINANCIAL_ACCOUNT_NAME_MAP`이 `매출액 / 영업수익` 양쪽 + `당기순이익(손실)` 변형 매핑 포함 (`financial_account_map.py:4-9`)
- watchlist 트리거 순서 news → price → financial 정합 (`watchlist.py:70-72`), 각 background task가 독립 Redis 락(`news-sync` / `price-sync` / `financial-sync`)으로 분리
- 가격 적재 후 별도 commit 한 다음 지표 계산 호출 (`price_ingestion.py:94,96-97`) — plan의 "가격 commit 후 지표 계산" 트랜잭션 경계 부분 충족
- PER/PBR을 financial_ingestion에서 `None`으로 고정 + 코드 주석으로 valuation phase 후속 분리 명시 (`financial_ingestion.py:80-95`) — plan의 "PER/PBR은 가격 의존이라 후속" 정합

### 보완 필요 / 누락

1. **`scripts/refresh_corp_code_map.py` 미구현** — plan-implementation-order.md Stage 3.2 산출물에 명시되어 있으나 코드 상 존재하지 않음(`scripts/` 확인). 현재 `CorpCodeProvider`가 stale 캐시(>1년) 자동 갱신은 가능하나, 수동 강제 갱신 진입점이 없음. 신규 상장/상폐 종목이 watchlist에 추가될 때 corp_code 매핑이 실패하면 `ValueError("corp code not found for symbol")`이 발생한다.
   **권장**: `scripts/refresh_corp_code_map.py` 신설 — `CorpCodeProvider().load_mapping(force_refresh=True)` 호출 + 결과 row 수 print.

2. **`dart-api-count:{KST date}` 일일 카운터 미구현** — `financial-cache-ingestion-plan.md` Redis 키 컨벤션 섹션은 `dart-api-count:{date}`가 financial+filing 공유 카운터라고 명시(KST 기준). 현재 `DartClient`는 일일 호출량 카운팅이 없음. plan-implementation-order.md "관측성과 로그"의 `DART API 일일 호출량 (FilingCache와 합산)`도 충족 안 됨. naver-api는 카운터가 있는데 dart는 없는 비대칭.
   **권장**: `_fetch_single_period` 직후 `redis.incr("dart-api-count:" + kst_date)` + `expire 48h` 추가. Stage 3.3 filing-cache 진입 전 정리하면 자연스럽다.

3. **`DartClient`의 외부 호출에 retry/backoff 없음** — `response.raise_for_status()`만 사용. 5년치 × 4분기 = 20회 + (CFS 실패 시 OFS 추가) 호출이 직렬로 일어나는데, 일시적 5xx/429에서 즉시 실패. naver/chroma 쪽은 tenacity 도입했는데 dart는 누락.
   **권장**: `_fetch_single_period`에 `tenacity` 적용(`stop_after_attempt(3)`, `wait_exponential`). naver-news client와 정책 통일.

4. **`PriceCacheRepository.upsert_many` / `FinancialCacheRepository.upsert_many` / `TechnicalIndicatorCacheRepository.upsert_many` 모두 inserted/updated 구분 못 함** — PostgreSQL `on_conflict_do_update`는 insert/update 둘 다 `rowcount=1`로 잡혀 항상 `updated=0` 또는 `count`로만 카운트된다. `SyncPriceResult.updated_count`가 사실상 항상 0. 관측 지표가 부정확.
   **권장**: `RETURNING (xmax = 0) AS inserted` 패턴이나 별도 SELECT 비교로 분리. 또는 보고서에 "현재 inserted/updated를 구분하지 않는다" 한 줄 명시.

5. **`PriceCacheSchedulerService.run_cleanup`이 같은 `PriceIngestionService`를 매 종목마다 새로 생성** — Redis 클라이언트는 inject되지만 `PriceCacheRepository / TechnicalIndicatorCacheRepository`가 매번 새 인스턴스. 종목 수가 늘면 비용 증가. NewsCache scheduler처럼 인스턴스 분리 + repo 직접 호출 패턴이 더 일관.

6. **`sync_technical_indicators_for_ticker`가 `MAX_CACHE_ROWS=1260` 만큼 SELECT** — 5년치 row 전부 메모리 로딩 후 pandas. 종목 수 100 가정 시에는 종목당 1초 미만이라 OK이지만, plan-implementation-order.md의 "단계 3.1 산출물" 중 worker 분리 시그널과 맞닿음. 보고서 "다음" 항목에 "live 검증 시 p95 시간 측정"을 추가 권장.

7. **`upsert_many`가 한 row씩 `INSERT ... ON CONFLICT` 실행** — pgsql `executemany` / batch insert가 아니라 N회 round-trip. 5년치 = 252 거래일 × 매번 RTT면 backfill에 10초 이상 소요 가능. SQLAlchemy `insert(...).values([dicts])` + on_conflict 한 번 호출이 효율적. Stage 4 토론 도메인 진입 전에 보강 가능.

8. **`FinancialIngestionService`가 `dart_client.fetch_financials`에서 5년 × 4분기 = 20회 직렬 호출** — 분기 1회 갱신이라 빈도는 낮지만, watchlist 추가 직후 초기 백필이 종목당 ~10초+ 가능. Background task 시간 늘어남. `years` 범위 줄이는 환경 변수 또는 캐시된 (year, quarter) 조합 skip 정책이 plan에 약하게 있으나(`이미 적재된 (year, quarter) 조합 조회`) 코드는 항상 모든 조합 호출.
   **권장**: `repo.list_recent`로 기존 (year, quarter) 조회 후 누락분만 호출. plan의 "누락된 분기 또는 최근 N분기"가 의도하는 흐름.

9. **`refresh` 모드에서 `sync_financials_for_ticker`가 항상 backfill_years 전체 재호출** — `mode` 파라미터를 받지만 분기 호출 범위에 영향 없음(`current_year - 5 + 1 ~ current_year`). plan은 "매일 02:00 sweep + 정정 공시는 upsert"라 했지만 매일 5년 × 4분기 = 20회를 watchlist 전 종목마다 호출하면 DART 일일 한도 20,000 대비 watchlist 1,000종목이면 즉시 소진. **권장**: refresh 모드에서는 최근 2~3분기만 재조회하도록 cap.

10. **`PriceCacheSchedulerService._set_last_run`와 `FinancialCacheSchedulerService._set_last_run` 키 컨벤션이 News scheduler와 미세 차이** — news는 `make_key("news-sync", "sweep:last-run", mode)` (purpose 자리에 콜론 포함), price/financial은 `make_key("price-sync", "sweep", "last-run", mode)` (4-segment). 둘 다 최종 키 문자열은 같지만 호출 형식이 다름. 일관성 위해 한쪽으로 통일하면 후속 검색/매트릭 도구 작성 시 비용 절감.

11. **PER/PBR `None` 정책 코드 주석은 있으나 valuation date 기준 미결정** — plan의 "valuation date 기준 (분기말 종가 권장)"이 후속 phase로 미뤄짐. 보고서도 동일. 다만 `FinancialCacheRepository.upsert_many`의 `set_` 절에 `per`/`pbr`이 포함돼 있어 향후 valuation phase에서 별도 update 흐름이 자연스러움 (이미 인터페이스 준비됨).

12. **fake 검증의 한계** — `validate_price_ingestion.py`와 `validate_financial_ingestion.py`가 monkey-patch 방식(`price_module.PriceCacheRepository = FakePriceRepo`)으로 repo 클래스를 통째 갈아끼움. 실 SQLAlchemy upsert / on_conflict_do_update / unique constraint 동작이 검증되지 않음. plan-implementation-order.md "이 단계 완료 시 상태"의 "100% 닫힘 ✅"은 실 DB 검증 1회가 닫혀야 진짜 완료. 본 보고서 "실 API / 실 DB 검증은 아직 하지 않음"이 정확한 표현.
   **권장**: pykrx + 실 PG로 1년치 백필 1회, DART_API_KEY 있는 환경에서 1종목 financial 백필 1회 — Stage 3.3 진입 전에 끝내면 안전.

13. **`build_indicator_rows`가 NaN인 row도 모두 upsert** — 백필 초기 119일은 ma120=null, 13일은 rsi14=null. 정책상 OK이지만 (`technical_indicator_cache`에 NULL 컬럼 자체가 nullable) 토론 evidence 단계에서 "최신 row가 NULL이면 어떻게 처리할지" 정책이 plan에는 없음. Stage 4 시작 시 정리 필요.

### 다음 단계 권고

- 1순위(즉시): `scripts/refresh_corp_code_map.py` 신설, dart-api-count 카운터 추가, DART retry 도입 — Stage 3.3 filing-cache 진입 전에 정리하면 같은 코드 재사용에 유리
- 2순위: `sync_financials_for_ticker(mode="refresh")`의 호출 범위 축소(최근 2~3분기) — DART 일일 한도 보호
- 3순위: 실 DB 라이브 검증 1회 (`pykrx + 005930`, `dart + 005930`) — plan 닫힘 기준 마무리
- 4순위(후속): `upsert_many` batch 최적화, scheduler key 컨벤션 통일, inserted/updated 정확 카운팅
