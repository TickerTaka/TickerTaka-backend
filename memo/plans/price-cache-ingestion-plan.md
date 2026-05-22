# Price Cache + Technical Indicator 적재 계획

## 목표

사용자가 관심 종목을 추가하면 해당 종목의 **일봉 가격 시계열**(`price_cache`)과 그로부터 파생되는 **기술지표**(`technical_indicator_cache`)를 캐싱한다.

핵심 원칙:
- 일봉 단위 캐시이며, 토론 시점의 *현재가*(intraday quote)는 본 plan 범위 밖이다.
- `price_cache → technical_indicator_cache`의 의존성을 가지므로 항상 가격 적재 후 지표 계산을 수행한다.
- 기술지표는 외부 API가 아니라 `price_cache`에서 직접 계산한다.
- 초기 구현은 한국 종목(KOSPI/KOSDAQ) 우선이며, 해외 종목은 후속 확장 범위.
- News cache가 가진 정책 패턴(Redis lock / cooldown / fail-closed / 일일 API 호출량 / sweep)을 재사용한다.

## 검증 완료 내용

확인 완료 테이블:
- `price_cache`
- `technical_indicator_cache`
- `ticker_metadata`

`price_cache` 실제 스키마:
- `id UUID PRIMARY KEY DEFAULT gen_random_uuid()`
- `symbol VARCHAR(30) NOT NULL`
- `price_date DATE NOT NULL`
- `open_price NUMERIC(18,4) NULL`
- `high_price NUMERIC(18,4) NULL`
- `low_price NUMERIC(18,4) NULL`
- `close_price NUMERIC(18,4) NOT NULL`
- `adjusted_close NUMERIC(18,4) NULL`
- `volume BIGINT NULL`
- `change_rate NUMERIC(8,4) NULL`
- `retrieved_at TIMESTAMPTZ NOT NULL DEFAULT now()`
- unique `(symbol, price_date)`
- index `(symbol, price_date DESC)`
- FK `symbol -> ticker_metadata.symbol ON DELETE CASCADE`

`technical_indicator_cache` 실제 스키마:
- `id UUID PRIMARY KEY DEFAULT gen_random_uuid()`
- `symbol VARCHAR(30) NOT NULL`
- `indicator_date DATE NOT NULL`
- `ma20 NUMERIC(18,4) NULL`
- `ma60 NUMERIC(18,4) NULL`
- `ma120 NUMERIC(18,4) NULL`
- `rsi14 NUMERIC(6,4) NULL`
- `macd NUMERIC(18,4) NULL`
- `macd_signal NUMERIC(18,4) NULL`
- `macd_hist NUMERIC(18,4) NULL`
- `volume_ma20 NUMERIC(20,2) NULL`
- `retrieved_at TIMESTAMPTZ NOT NULL DEFAULT now()`
- unique `(symbol, indicator_date)`
- index `(symbol, indicator_date DESC)`
- FK `symbol -> ticker_metadata.symbol ON DELETE CASCADE`

결론:
- 현재 스키마만으로 구현 가능
- 추가 컬럼 없이 시작 가능

## 데이터 소스

1순위: **pykrx**
- KRX 공식 데이터 직접 조회 (KOSPI/KOSDAQ 한정)
- 한국 종목 가격에서 가장 정확
- 라이선스: MIT
- requirements.txt 추가 필요 (`pykrx==1.0.45` 또는 최신 핀)

2순위(fallback): **FinanceDataReader**
- KOSPI/KOSDAQ 외 글로벌 종목까지 지원
- pykrx 호출 실패 시 보조용
- 후속 확장 시 도입

3순위(미사용 예정): yfinance — 한국 종목은 약간의 지연/오류 가능. 별도 검증 없이 도입 안 함.

거래 휴장일 처리:
- pykrx가 자동으로 영업일만 반환
- 휴장일이 백필 범위에 들어가도 row가 생성되지 않음

## 환경 변수 / 외부 의존성

- 외부 API 키 불필요 (pykrx는 무인증)
- requirements.txt 추가: `pykrx==1.0.45`, `pandas==2.2.3` (이미 있음)
- 새 환경 변수 없음

## 확정 정책

### 1. 적재 건수와 백필 범위

기본값:
- 초기 백필: 최근 `1년` (영업일 기준 약 `252` row)
- 정기 갱신: 매일 장 마감 후 (15:30 KST + 30분 지연 = 16:00 KST부터 가능)
- 강제 재수집 최소 간격: `1시간`
- 종목당 row 상한: `1260` (5년치) — 그 이상은 archive 또는 삭제

운영 원칙:
- 토론에서 1년치 이동평균 / RSI / MACD 평가에 충분
- MA120(120일 이동평균) 계산을 위해 최소 120 영업일 필요 → 1년 백필이면 안전
- 5년치 이상이 필요하면 백필 한도 증가

### 2. 중복 제거 / 정정 처리

기본:
- `(symbol, price_date)` unique로 1차 dedupe
- 동일 날짜 재수집 시 `UPSERT`로 덮어쓰기 (KRX 종가 정정, 액면분할 보정 대응)

정정 윈도우:
- 최근 `5 거래일`은 매번 덮어쓰기 (KRX 종가 확정/정정 가능성)
- 그 이전 데이터는 신규 row만 추가 (변동 없음 가정)

### 3. 갱신 주기

기본값:
- 관심 종목 추가 시: 비동기 수집 `1회 즉시 실행` (1년치 백필)
- 정기 갱신: 매일 KST `16:00`에 watchlist 전체 sweep
- 강제 재수집 최소 간격: `1시간`

운영 방향:
- 시장 휴장일에는 sweep 자체는 돌지만 신규 row가 없음 (pykrx가 영업일만 반환)
- 휴장 검출은 별도 처리 안 함 → 신규 row 0건이면 skip 통계로 집계
- `force=True`는 1시간 최소 간격만 무시하며 Redis lock은 우회하지 않음
- watchlist에서 종목 삭제 시 해당 종목 정기 갱신 중단

### 4. 삭제 정책 / TTL (장기 보존 + row 상한 trim)

기본:
- 가격 데이터는 **장기 보존** — TTL은 없고 row 상한으로만 정리 (실질적으로는 "최근 5년 보존")
- row 상한 `1260`(5년 영업일 기준) 초과 시 오래된 row부터 삭제
- 기술지표도 동일 정책 (`1260` row 상한)

cleanup:
- 매일 1회 (가격 갱신 sweep 직후) row 상한 초과분 정리
- ticker가 `is_active=False`로 변경되면 해당 row 유지 또는 archive (정책 추후 결정)

## 저장 전략

`price_cache` 최소 저장 필드:
- `symbol`
- `price_date`
- `open_price`
- `high_price`
- `low_price`
- `close_price` (필수)
- `volume`
- `change_rate` — **전일 종가 대비 직접 계산으로 고정** (외부 응답값/계산 혼재 시 정의가 흔들리므로 재현성/검증성 위해 직접 계산만 사용)
- `adjusted_close` — **초기 구현은 NULL 고정**. 조정가 정책이 정해질 때 별도 phase에서 pykrx의 수정주가 API로 채움. `close_price`와 동일하게 저장은 금지(나중에 PER/PBR 계산에서 혼재 위험)
- `retrieved_at`

`technical_indicator_cache` 저장 필드:
- `symbol`
- `indicator_date` (== `price_date`)
- `ma20`, `ma60`, `ma120` (단순 이동평균, 종가 기준)
- `rsi14` (14일 RSI)
- `macd`, `macd_signal`, `macd_hist` (MACD 12/26/9)
- `volume_ma20` (거래량 20일 이동평균)
- `retrieved_at`

계산 라이브러리:
- 기본은 `pandas`로 직접 계산 (rolling mean, ewm). 외부 의존성 최소화.
- pandas-ta 도입은 보류 (의존성 크고 NaN 처리 복잡)

NaN 처리:
- MA120은 119일 미만이면 NULL
- RSI14는 14일 미만이면 NULL
- MACD는 26일 미만이면 NULL
- 가장 오래된 N일은 지표 일부가 NULL인 상태로 저장

## Redis 키 컨벤션

News cache 패턴 일관성:
- `price-sync:lock:{symbol}` — 동시 실행 방지 (`SET NX EX=600`)
- `price-sync:last-sync:{symbol}` — 최근 실행 시각 (1시간 cooldown 판정)
- `price-sync:sweep:last-run:{mode}` — 전체 sweep 최근 실행 시각 (mode: `refresh`/`cleanup`)
- pykrx는 외부 API quota가 없어 일일 호출량 키는 불필요 (다만 호출 횟수 로그는 남김)

운영 환경의 Redis 배치(NCP 서버 + Docker 셀프 호스트, 인증/persistence/메모리 한도)는 `debate-runtime-infrastructure-plan.md`의 "운영 환경 배치" 섹션 참고.

## 토론 코드 연계 (a543ff1 커밋 기준)

토론 에이전트의 `data_agent` 노드(`app/agents/nodes/data_node.py`)가 본 cache의 데이터를 `debate_repo.fetch_price_context(symbol)`로 SELECT:

```sql
SELECT close_price, change_rate, volume, open_price, high_price, low_price
FROM price_cache WHERE symbol=$1 ORDER BY price_date DESC LIMIT 1

SELECT ma20, ma60, ma120, rsi14, macd, macd_signal, macd_hist, volume_ma20
FROM technical_indicator_cache WHERE symbol=$1 ORDER BY indicator_date DESC LIMIT 1
```

영향:
- 본 plan 적재 컬럼이 위 SELECT와 일치 (`close_price`/`change_rate`/`volume`/`open/high/low_price` + 지표 8개)
- 본 plan 단계 3.1 완료 시 토론 `data_agent`의 yfinance 폴백이 더 이상 필요 없어짐 (DB 데이터 사용)
- evidence 영구화 시 `evidence.price_cache_id` / `evidence.technical_indicator_cache_id` 외래 키로 cache row 참조 (vector-db plan 참고)

## 수집 함수 시그니처

```python
def sync_prices_for_ticker(
    symbol: str,
    mode: str = "initial",   # "initial" | "refresh"
    force: bool = False,
    backfill_days: int | None = None,
) -> SyncPriceResult: ...

def sync_technical_indicators_for_ticker(
    symbol: str,
    recompute_days: int | None = None,  # 최근 N일치만 재계산. None이면 NULL인 row만.
) -> SyncIndicatorResult: ...
```

역할:
- `sync_prices_for_ticker`:
  1. `ticker_metadata`에서 종목 확인
  2. Redis lock 획득
  3. 마지막 `price_date` 조회
  4. pykrx로 누락 구간 + 최근 5일 가져오기
  5. `(symbol, price_date)` upsert + **여기서 가격 트랜잭션 commit**
  6. `change_rate` 직접 계산 (전일 종가 대비)
  7. row 상한 초과분 정리
  8. **별도 트랜잭션**으로 `sync_technical_indicators_for_ticker` 트리거 — 지표 계산 실패가 가격 적재까지 롤백시키지 않도록 분리
- `sync_technical_indicators_for_ticker`:
  1. `price_cache`의 최근 N일치 종가/거래량 로딩 (직전 트랜잭션 commit 완료 후)
  2. pandas로 지표 계산
  3. `(symbol, indicator_date)` upsert
  4. row 상한 초과분 정리

트랜잭션 경계 정책:
- 가격 적재와 지표 계산을 같은 세션/트랜잭션에 묶지 않음
- 지표 계산이 실패해도 가격 row는 보존 → 다음 sync에서 지표만 재계산
- 운영 안정성 측면에서 분리가 표준

반환 예시:
- 신규 적재 가격 row 수
- 갱신된 가격 row 수
- 계산된 지표 row 수
- 삭제된 오래된 row 수
- 소요 시간(ms)

## 트리거 및 실행 구조

NewsCache와 동일 구조:
1. 사용자가 관심 종목 추가
2. `watchlist` 저장 + DB commit
3. `BackgroundTasks.add_task(sync_watchlist_prices, symbol)` enqueue
4. `sync_watchlist_prices(symbol)`:
   1. `sync_prices_for_ticker(symbol, mode="initial", force=True)` — 1년치 백필
   2. 가격 commit 후 별도 트랜잭션으로 지표 계산
5. 정기 갱신은 별도 scheduler (NewsCache의 `NewsCacheSchedulerService`와 같은 패턴으로 `PriceCacheSchedulerService` 신설)

Background task 시간 / worker 분리 메모:
- watchlist 등록 직후 1년치 백필 + 지표 계산은 종목당 수~수십 초 소요
- News/Filing/Financial과 병렬 실행되면 FastAPI 프로세스 background task 점유 시간 증가
- 종목 수 증가 또는 task 시간 p95 > 60초 시 worker 분리 (RQ/Celery/Arq) 검토
- 초기는 BackgroundTasks 유지, 신호 보고 이전 결정

스케줄러 실행:
- 외부 cron으로 매일 16:00 KST에 `scripts/run_price_cache_scheduler.py --mode refresh` 호출
- `--mode cleanup`은 같은 cron에서 refresh 직후 호출 또는 매일 새벽 1회

## 구현 단계

### Phase 1. pykrx 클라이언트 + price 적재

목표: `sync_prices_for_ticker(symbol)` 구현

처리 순서:
1. `app/external/krx_client.py` — pykrx wrapper (날짜 범위 → DataFrame)
2. `app/repositories/price_cache_repository.py` — bulk upsert + 최근 N일 조회 + row 상한 정리
3. `app/domain/price_ingestion.py` — `sync_prices_for_ticker` + Redis lock
4. `scripts/validate_price_ingestion.py` — 적재/upsert/cooldown/lock/row 상한 시나리오
5. **라이브 검증** — 대표 종목(예: 005930, 000660)로 16:00 KST sweep 시점에 당일 row가 안정적으로 생성되는지 확인. pykrx의 장중/장후 시차와 휴장일 동작을 실측으로 검증.

### Phase 2. 기술지표 계산

목표: `sync_technical_indicators_for_ticker(symbol)` 구현

처리 순서:
1. `app/domain/technical_indicator.py` — pandas 계산 함수 (`compute_ma`, `compute_rsi`, `compute_macd`, `compute_volume_ma`)
2. `app/repositories/technical_indicator_cache_repository.py` — bulk upsert
3. `app/domain/price_ingestion.py`에서 가격 적재 후 자동 트리거
4. `scripts/validate_technical_indicator.py` — 알려진 데이터셋(예: 삼성전자 1년) 기준 검증

### Phase 3. watchlist 트리거 연결

목표: watchlist 등록 시 가격+지표 sync

처리 순서:
1. `app/domain/watchlist_service.py`에 `sync_watchlist_prices(symbol)` 추가
2. `app/api/watchlist.py`의 `create_watchlist`에서 `background_tasks.add_task(sync_watchlist_prices, symbol)` 추가
3. NewsCache 트리거와 병렬로 실행 (둘 다 별도 background task)

### Phase 4. 정기 갱신 / cleanup

## 검증/보완 메모 (2026-05-22)

1. `PriceCache`는 영구성 데이터로 두면서 row 상한 1260을 둔 상태다. 이 경우 "영구성"의 의미가 실질적으로는 "최근 5년 보존"이므로, 문서 표현은 영구 보존보다 "장기 보존 + 상한 trim" 쪽이 더 정확하다.
2. `adjusted_close`를 초기에는 `close_price와 동일 또는 NULL`로 적었는데, 이후 PER/PBR 등 후속 계산에서 조정가 사용 여부가 혼재될 수 있다. 초기 구현에서는 `adjusted_close = NULL`로 고정하고, 조정가 정책이 정해질 때만 채우는 편이 안전하다.
3. `change_rate`는 외부 응답값 사용 또는 계산으로 적혀 있는데, 소스가 섞이면 값 정의가 달라질 수 있다. 구현 시에는 "전일 종가 대비 직접 계산"으로 고정하는 것이 재현성과 검증성 면에서 낫다.
4. `sync_prices_for_ticker` 마지막에 지표 계산을 자동 트리거하는 구조는 자연스럽지만, 가격 upsert와 지표 계산을 같은 세션/트랜잭션에 묶을지 분리할지 먼저 정해야 한다. 지표 계산 실패가 가격 적재까지 롤백시키는 구조는 운영상 비효율적일 수 있다.
5. `pykrx`를 1순위로 두는 결정은 타당하지만, 장중/장후 시차와 휴장일 동작을 실 라이브로 한 번 검증해야 한다. 특히 16:00 KST sweep 기준에 실제 당일 row가 안정적으로 생성되는지 확인이 필요하다.
6. `watchlist` 등록 직후 1년치 백필은 종목 수가 늘면 background task가 길어질 수 있다. News/Filing과 병렬 실행될 때 API 서버 프로세스에서 감당 가능한지, 또는 추후 worker 분리 전제가 필요한지 메모해두는 편이 좋다.

목표: 매일 가격 + 지표 갱신, row 상한 정리

처리 순서:
1. `app/domain/price_cache_scheduler.py` — `PriceCacheSchedulerService` 신설
2. `run_watchlist_refresh()` — watchlist 전체 sweep
3. `run_price_cleanup()` — row 상한 정리
4. `scripts/run_price_cache_scheduler.py` — cron 진입점
5. `scripts/validate_price_cache_scheduler.py` — sweep 시나리오

## 관측성과 로그

최소 구조화 로그:
- `symbol`
- 신규 가격 row 수
- 갱신된 가격 row 수 (정정 윈도우 5일분 등)
- 계산된 지표 row 수
- pykrx 호출 횟수
- 소요 시간(ms)

추가 운영 지표:
- 종목별 마지막 `price_date` 갱신 시각
- 휴장일 skip 횟수 (신규 0건 케이스)
- row 상한 cleanup 누적 삭제 수

## 향후 확장 후보

- adjusted_close 정확화: pykrx의 수정주가 API 별도 호출
- 분봉/주봉 캐시 도입 (현재는 일봉 한정)
- 해외 종목 지원: FinanceDataReader 또는 yfinance 백엔드 추가
- 추가 지표: Bollinger Band, Stochastic, ATR 등
- ChromaDB 등 벡터 DB에 가격 임베딩 (시장 유사성 검색용)

## 결론

확정 내용:
- 데이터 소스 `pykrx` (1순위), FinanceDataReader는 fallback 후보
- 일봉 캐시, 토론용 현재가는 별도 (`intraday quote` plan)
- 초기 백필 1년, row 상한 5년
- 정정 윈도우 5 거래일은 매 갱신마다 덮어쓰기
- 정기 갱신 매일 KST 16:00, 강제 재수집 최소 1시간
- 기술지표는 pandas로 직접 계산 (MA/RSI/MACD/volume_ma)
- watchlist 트리거 NewsCache 동일 패턴
- Phase 1~4 순차 구현
