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

- `financial_cache`는 **재무제표 원숫자 + ROE + debt_ratio**를 기본 저장값으로 유지
- `PER`, `PBR`은 가격 의존값이라 분기 수집 시점에는 여전히 `NULL`로 시작
- 이후 **price sync 직후** 최신 종가 기준으로 `financial_cache`의 최신 분기 row에 `PER/PBR`을 갱신한다
- 구현은 `PyKrxClient.get_market_fundamental` 경로를 사용해 상장주식수 병목 없이 `PER/PBR/EPS/BPS`를 가져오는 방식으로 해결

### 현재 구현된 cadence

- 재무 원천 데이터: 분기 1회 (`financial_cache`)
- 가격 원천 데이터: 거래일 1회 종가 (`price_cache`)
- `PER/PBR` 저장값: **가격 sync 직후 거래일 1회 재계산**
- 장중 상세/토론용 현재가: `intraday_quote`에서 별도 처리 (저장값과 분리)

### 구현 위치

- `app/domain/financial_ratios.py`
  - `compute_per()`
  - `compute_pbr()`
- `app/external/krx_client.py`
  - `MarketFundamentalRecord`
  - `fetch_latest_market_fundamental()`
- `app/domain/price_ingestion.py`
  - 가격 적재 + 지표 계산 후 `_sync_latest_valuation_metrics()` 수행
- `app/repositories/financial_cache_repository.py`
  - `get_latest_row()`
  - `update_latest_valuation()`

### 검증

- `scripts/validate_price_ingestion.py`
  - fake `price_client`가 fundamental(`PER=12.34`, `PBR=1.23`)을 반환하도록 보강
  - price sync 후 최신 `financial_cache` row의 `per/pbr`이 갱신되는지 함께 확인

로컬 실행:

```bash
source venv/bin/activate
python -m scripts.validate_price_ingestion
```

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

---

## 검증/보완 메모 (2026-05-25, mergedb 통합 이후 재확인)

filing 수동 병합 + Stage 4 인프라 진입 이후, Stage 3.1/3.2 산출물이 여전히 정합한지 다시 확인했다.

### filing 통합 이후의 변경 영향 (확인)

- `app/external/dart/client.py`에 filing API 메서드가 추가되면서 `_get`이 retry + `_record_daily_api_call` 일관 적용 (`client.py:246-259`) → financial 호출도 같은 `dart-api-count:{KST date}` 키로 incr. 본 보고서 위 "1순위" 즉시 보강 권장 사항이 mergedb에서 완료된 상태.
- `app/external/dart/__init__.py`에 `DartApiError, DartFilingItem` export 추가 — financial 흐름은 영향 없음.
- `app/domain/watchlist_service.py`에 `sync_watchlist_filings` 추가 (`watchlist_service.py:110-131`) — `app/api/watchlist.py`가 `news → price → financial → filing` 4-task enqueue (`watchlist.py:71-74`). financial sync는 기존 위치 유지.

### Stage 4 측에서 financial / price를 참조하는 경로

- `data_node` (`data_node.py:24-27`)가 `fetch_price_context` / `fetch_financial_context`로 PG 캐시를 그대로 사용. price/financial 모델/리포 인터페이스 변경 없음.
- `_fmt_finance` (`data_node.py:87-98`)가 `r['fiscal_year']`, `r['fiscal_quarter']`, `revenue/operating_profit/net_income`, `PER/PBR/ROE`를 사용 → 본 보고서가 명시한 PG 컬럼과 동일. financial_ingestion에서 `PER/PBR=None` 유지 정책이 토론에서 "N/A"로 표시되도록 자연 우회되어 있음.

### 미해결 권장 사항 (mergedb 시점에도 그대로)

- `scripts/refresh_corp_code_map.py` — 본 보고서 추가 반영 섹션에 신설 완료 명시. mergedb에서도 존재 (`scripts/` 확인).
- pykrx + 실 PG 1년치 백필 / DART 1종목 financial 백필 — Stage 3.3 filing은 실 DART API + 실 PG로 `{"symbol":"000020", ...}` 결과까지 확인됐지만, price / financial 자체의 라이브 검증은 여전히 fake 모드 검증만 끝난 상태로 보인다. mergedb 보고서(`2026-05-25`)는 watchlist 4-task가 live로 enqueue되는 것까지 확인했고, 그 안에서 price/financial이 실제 외부 API 호출까지 갔는지(또는 어떤 에러로 끝났는지)에 대한 라이브 로그는 별도 캡처가 없다. → Stage 4 live 품질 보정 작업에 합쳐서 1회 확인 권장.
- `sync_financials_for_ticker(mode="refresh")` 호출 범위 축소(최근 2~3분기) — 코드상 여전히 5년 × 4분기 = 20회. watchlist 100+ 종목으로 확장 시 DART 일일 한도 소진 위험.
- `upsert_many` batch 최적화 / inserted/updated 정확 카운팅 — 회귀 없음 확인. 후속.

### 판정

Stage 3.1/3.2의 구조는 그대로 유효. filing 통합 과정에서 financial 흐름에 회귀를 일으킨 변경 없음. live 검증은 여전히 부분적이지만 plan 보완 메모(2026-05-23) 권장 사항 일부는 mergedb 시점에 이미 반영됨(`dart-api-count`, retry, `refresh_corp_code_map.py`). 나머지는 운영 품질 단계 작업으로 자연 이월.

---

## 검증/보완 메모 (2026-05-30, PER/PBR A안 구현 반영 후 검토)

위 "PER/PBR 정책"에서 후속으로 미뤄두었던 valuation 갱신을 **A안(price sync 직후 거래일 1회 재계산)**으로 실제 구현한 뒤, 변경 파일을 코드 레벨에서 다시 점검했다. 대상:

- `app/domain/financial_ratios.py` — `compute_per()`, `compute_pbr()`
- `app/external/krx_client.py` — `MarketFundamentalRecord`, `fetch_latest_market_fundamental()`
- `app/repositories/financial_cache_repository.py` — `get_latest_row()`, `update_latest_valuation()`
- `app/domain/price_ingestion.py` — `_sync_latest_valuation_metrics()`
- `scripts/validate_price_ingestion.py` — fake fundamental 반영 검증

### 정합성 확인 (OK)

- `compute_per` / `compute_pbr`가 `eps/bps in (None, 0)` 분기로 0 division 방지 — 기존 `compute_roe` 패턴과 일관 (`financial_ratios.py:16-25`)
- `fetch_latest_market_fundamental`: `lookback_days=10`으로 주말/연휴 휴장 커버, `frame.empty → None` 반환, `_to_optional_float`가 pykrx의 NaN/None을 안전 변환 (`krx_client.py:74-117`)
- `_sync_latest_valuation_metrics`가 지표 계산 후 / trim·`_set_last_sync` 전에 호출되고, 직후 `session.commit()`(`price_ingestion.py:105`)으로 영속화됨 — 트랜잭션 경계 정상
- `financial_repo`가 `__init__`에서 주입(`price_ingestion.py:52`), import·호출 시그니처 일치 — 컴파일 통과와 부합
- `update_latest_valuation`이 최신 분기 row 없을 때 `False` 반환으로 graceful skip (`financial_cache_repository.py:86-88`) — 분기 재무 미적재 종목에서 예외 안 남
- fundamental fetch 실패를 `try/except`로 잡아 로그만 남기고 return (`price_ingestion.py:152-154`) — "보조 기능 실패가 가격 적재 본체를 깨면 안 된다" 정책 부분 충족(단 아래 보완 3 참고)
- pykrx `PER/PBR` 우선 + `None`일 때만 `compute_*` fallback (`price_ingestion.py:164-165`) — 상장주식수 병목 우회라는 설계 의도와 정합

### 보완 필요 / 누락

1. **(HIGH·데이터 품질) 적자/자본잠식 종목 PER·PBR이 `0`으로 저장됨** — pykrx `get_market_fundamental_by_date`는 EPS ≤ 0인 종목의 `PER`을 `NULL`이 아니라 **`0.0`으로 반환**한다(자본잠식 시 `PBR`도 동일). 현재 코드는 `fundamental.per if fundamental.per is not None else ...`(`price_ingestion.py:164`) 분기라, `0.0`은 `not None`이므로 **그대로 0이 저장**된다. 결과적으로 적자 종목이 "PER 0 = 초저평가"로 오해될 수 있고, 토론 `data_node`의 `_fmt_finance`에도 `PER 0`으로 흘러간다.
   **권장**: `fetch_latest_market_fundamental`(또는 `_sync_latest_valuation_metrics`)에서 `per/pbr ≤ 0`을 `None`으로 정규화. EPS/BPS가 음수면 valuation 자체가 N/A라는 의미를 살려야 한다.

2. **(HIGH·회귀) financial 재적재가 PER/PBR을 NULL로 덮어씀** — `FinancialCacheRepository.upsert_many`의 `set_` 절에 `per`/`pbr`이 포함(`financial_cache_repository.py:49-50`)되어 있고, `financial_ingestion`은 항상 `"per": None, "pbr": None`을 전달(`financial_ingestion.py:98-99`)한다. 따라서 **`financial_cache_scheduler`의 refresh sweep이 price sweep보다 나중에 돌면, 직전 price sync가 채운 최신 분기 row의 PER/PBR이 다시 NULL로 리셋**되고 다음 price sync까지 빈 값이 된다. watchlist 4-task enqueue 순서가 `news → price → financial`(`watchlist.py:71-74`)이라 **신규 등록 직후에도 financial이 가장 나중에 돌아 PER/PBR이 항상 NULL로 시작**한다.
   **권장**: ① `upsert_many`의 `set_`에서 `per`/`pbr`을 제외해 "가격 경로 전용 컬럼"으로 분리하거나, ② `COALESCE(EXCLUDED.per, financial_cache.per)`로 기존 값 보존. ①이 단순하고 의미상 정확(분기 수집은 valuation을 모름).

3. **(MED·견고성) valuation의 DB 호출이 `try` 밖** — `_sync_latest_valuation_metrics`의 `try/except`는 `fetch_latest_market_fundamental`만 감싼다. 이후 `price_repo.list_recent` / `update_latest_valuation`(내부 `flush`)(`price_ingestion.py:159-166`)는 `try` 밖이라, **DB 측 예외가 나면 가격 적재 트랜잭션 전체가 실패**한다. 보완 1·2의 정책상 valuation은 보조 기능이므로, 본체를 깨지 않아야 한다.
   **권장**: 메서드 전체를 best-effort로(외부 + DB 갱신까지 `try`로) 감싸고 실패 시 로그만 남긴다.

4. **(MED·관측성) PER/PBR이 `SyncPriceResult`에 노출되지 않음** — 결과 dataclass에 valuation 필드가 없어(`price_ingestion.py:23-32`), 검증 스크립트의 "per/pbr 출력" 기대치는 `financial_cache`를 **별도 조회**해야만 확인된다. 스케줄러 `PriceSweepResult`도 valuation 갱신 건수를 집계하지 못해, 운영에서 "오늘 몇 종목 PER 갱신됐나"를 알 수 없다.
   **권장**: `SyncPriceResult`에 `per`/`pbr`(또는 `valuation_updated: bool`) 추가, `PriceSweepResult`에 누적 카운터 추가.

5. **(NOTE·설계) 분기 row에 시점성(point-in-time) valuation을 얹는 의미 불일치** — PER/PBR은 "오늘 종가" 기준값인데 이를 **최신 분기 재무 row**에 저장한다. fiscal period와 valuation 시점이 어긋나며, 신규 분기 데이터가 DART로 들어온 직후 ~ 첫 price sync 전까지 **NULL 윈도우**가 생긴다(보완 2와 연동). `data_node`가 `None`을 N/A로 처리하므로 토론은 안전하지만, 프론트 상세 화면에서 "PER 없음"이 잠깐 노출될 수 있다. 장기적으로는 valuation을 분기 row가 아니라 `price_cache`(일자별) 또는 별도 `valuation_cache`에 두는 편이 의미상 깨끗하다 — 후속 판단.

6. **(LOW·정합) 종가 소스 이원화** — pykrx의 `PER/PBR`은 KRX 자체 종가 기준으로 계산된 값이고, fallback `compute_*`는 우리 `price_cache` 최신 `close_price` 기준(`price_ingestion.py:163-165`)이다. 두 경로가 서로 다른 날짜/종가를 쓸 수 있어 미세 불일치 가능. 대부분 KRX 값을 그대로 쓰므로 영향은 작다.

7. **(LOW·테스트) 엣지 케이스 미검증** — `validate_price_ingestion.py`는 happy path(`PER=12.34`, `PBR=1.23`) 1건만 fake로 검증한다. ① fundamental `None`(상폐/신규 종목), ② 최신 분기 row 없음(`update_latest_valuation → False`), ③ 적자 종목 `PER=0`(보완 1) 경로는 미검증.
   **권장**: 위 3개 fake 분기를 validate에 추가.

### 다음 단계 권고

- 1순위(즉시): 보완 1(적자 `PER=0 → None` 정규화) + 보완 2(financial 재적재 NULL 덮어쓰기 차단). 이 둘은 잘못된 값이 토론·프론트로 흘러가는 **데이터 정확성** 문제라 우선.
- 2순위: 보완 3(valuation DB 호출 best-effort) — 가격 적재가 valuation 때문에 실패하는 회귀 방지.
- 3순위: 보완 4(결과 노출) + 보완 7(엣지 테스트) — 관측성·검증 보강.
- 후속: 보완 5(valuation 저장 위치 재설계)는 Stage 4 토론 품질 작업과 함께 판단.
- 라이브: `pykrx` 설치 환경에서 `python -m scripts.validate_price_ingestion` 통과 확인 + 실제 적자 종목 1개로 `PER` 저장값이 `0`이 아닌 `NULL`로 들어가는지 라이브 점검(보완 1 회귀 테스트).

### 판정

A안 구조 자체(price sync 직후 pykrx fundamental로 최신 분기 row의 PER/PBR 갱신)는 설계 의도대로 연결되었고 컴파일·인터페이스 정합도 확인됨. 다만 **적자 종목 PER=0 저장(보완 1)**과 **financial 재적재 시 NULL 덮어쓰기(보완 2)** 두 건은 출력값 정확성에 직접 영향을 주므로 라이브 검증 전에 정리하는 것을 권장한다. 나머지는 견고성·관측성 보강으로 후속 이월 가능.

### 후속 반영 (2026-05-30)

- `app/domain/financial_ratios.py`에서 `compute_per()` / `compute_pbr()`가 `price <= 0`, `eps/bps <= 0`이면 `None`을 반환하도록 강화했다.
- `app/domain/price_ingestion.py`에서 `normalize_positive_ratio()`를 적용해 pykrx가 돌려주는 `per/pbr == 0` 값을 그대로 저장하지 않고 `None`으로 정규화한다.
- `_sync_latest_valuation_metrics()` 전체를 best-effort `try/except`로 감싸 fundamental 조회뿐 아니라 `list_recent()` / `update_latest_valuation()`의 DB 예외도 가격 적재 본체를 깨지 않도록 바꿨다.
- `FinancialCacheRepository.upsert_many()`의 `set_` 절에서 `per/pbr`를 제거해, financial 재적재가 price sync가 채운 valuation 값을 `NULL`로 덮어쓰지 않도록 수정했다.
- `scripts/validate_price_ingestion.py`에 적자 케이스 fake(`per=0`, `pbr=0`, `eps<0`, `bps<0`)를 추가해 `loss_case_per=None`, `loss_case_pbr=None` 경로를 검증하도록 보강했다.

### 최종 판정

2026-05-30 보완 적용 후, 본 메모의 HIGH 항목 1~3은 코드 반영 완료. 남은 것은 관측성(`SyncPriceResult` 노출)과 장기 저장 모델(`valuation_cache` 분리 여부) 같은 후속 품질/설계 보강이다.

---

## 검증/보완 메모 (2026-05-30, 보완 반영분 재검증)

위 "후속 반영 (2026-05-30)"에 기재된 보완 4건이 실제 코드에 반영되었는지 재검토하고, 검증 스크립트를 다시 실행했다.

### 코드 재확인 (반영 OK)

- **보완 1 (적자 valuation 0 저장 차단)** — 반영 확인.
  - `financial_ratios.py:17,23`: `compute_per`/`compute_pbr`가 `price <= 0`·`eps/bps <= 0`에서 `None` 반환.
  - `financial_ratios.py:28-31`: `normalize_positive_ratio()` 신설(`value <= 0 → None`).
  - `price_ingestion.py:160-166`: pykrx의 `per/pbr`을 `normalize_positive_ratio`로 거른 뒤, `None`일 때만 `compute_*` fallback. → pykrx가 적자 종목에 돌려주는 `0.0`이 그대로 저장되지 않음.
- **보완 2 (financial 재적재 NULL 덮어쓰기 차단)** — 반영 확인.
  - `financial_cache_repository.py:42-53`: `upsert_many`의 `set_` 절에서 `per`/`pbr` 제거됨. → 분기 재적재(refresh/cleanup sweep)가 price sync가 채운 valuation을 더 이상 NULL로 덮지 않음. valuation은 `update_latest_valuation()` 단일 경로로만 쓰여 책임이 명확해짐.
- **보완 3 (valuation DB 호출 best-effort)** — 반영 확인.
  - `price_ingestion.py:146-170`: `_sync_latest_valuation_metrics` 전체 본문이 `try`로 감싸짐. `list_recent()`/`update_latest_valuation()`(내부 flush)의 DB 예외도 잡아 로그만 남기고 return → 가격 적재 본체 트랜잭션을 깨지 않음.
- **보완 7 (엣지 케이스 테스트)** — 부분 반영. `validate_price_ingestion.py:212-225`에 적자 케이스(`per=0`, `pbr=0`, `eps=-100`, `bps=-1000`) fake 추가. (fundamental `None` / 최신 분기 row 없음 경로는 여전히 미검증 — LOW로 잔존.)

### 검증 실행 결과 (live)

`wsl Ubuntu / venv` 환경에서 실제 실행함(이전 회차는 셸에 sqlalchemy 부재로 미실행이었음).

```
$ python -m scripts.validate_price_ingestion
{'fetched': 140, 'inserted': 140, 'updated': 0, 'indicators': 140,
 'per': 12.34, 'pbr': 1.23, 'trimmed_price_rows': 0, 'trimmed_indicator_rows': 0}
{'loss_case_fetched': 140, 'loss_case_per': None, 'loss_case_pbr': None}

$ python -m compileall app scripts -q
COMPILE_OK
```

- 일반 케이스: `per=12.34`, `pbr=1.23` 정상 갱신.
- 적자 케이스: `loss_case_per=None`, `loss_case_pbr=None` → **보완 1 회귀 테스트 통과**(0이 아닌 NULL).
- `compileall` 통과.

### 잔존 항목 (후속 이월)

- **보완 4 (관측성)**: `SyncPriceResult`에 valuation 필드가 여전히 없어, 검증·스케줄러가 PER/PBR 갱신 건수를 결과 객체로는 알 수 없음. 현재는 `financial_cache` 직접 조회로만 확인. — MED, 후속.
- **보완 5 (저장 위치 재설계)**: 시점성 valuation을 분기 row에 얹는 의미 불일치 + 신규 분기 직후 NULL 윈도우는 구조 이슈로 잔존. `valuation_cache` 분리 여부는 Stage 4 품질 작업과 함께 판단. — NOTE.
- **보완 6 (종가 소스 이원화)** / **보완 7 잔여 엣지**: LOW, 영향 작음.
- **라이브(실 pykrx/실 PG)**: 여전히 fake 검증. 실제 적자 종목으로 `PER` 저장값이 NULL인지 1회 라이브 점검은 미수행.

### 판정

후속 반영 4건 중 HIGH 3건(보완 1·2·3)은 코드·실행 검증 모두 통과. fake 기준이지만 적자 회귀 테스트가 NULL로 닫혔고 컴파일도 정상이다. 출력값 정확성에 직접 영향을 주던 항목은 이 회차로 정리 완료. 남은 보완 4~7은 관측성·설계·라이브 검증 영역으로, 동작 정확성을 막지 않으므로 후속 품질 단계로 이월한다.
