# Financial Cache 적재 계획

## 목표

사용자가 관심 종목을 추가하면 해당 종목의 **분기/연간 재무제표**(`financial_cache`)를 OpenDART API에서 가져와 캐싱한다.

핵심 원칙:
- 분기 단위 데이터로, 데이터량이 가장 적고 변동도 거의 없다 (분기 1회 공시).
- 한 번 백필되면 분기 공시 시점에만 갱신이 필요하다 → 갱신 빈도가 가장 낮은 캐시.
- News/Filing/Price cache와 동일 인프라(Redis lock / cooldown / sweep)를 재사용한다.
- 초기 구현은 한국 상장 종목(KOSPI/KOSDAQ) 한정 — DART 공시 대상 기업만.

## 검증 완료 내용

확인 완료 테이블:
- `financial_cache`
- `ticker_metadata`

`financial_cache` 실제 스키마:
- `id UUID PRIMARY KEY DEFAULT gen_random_uuid()`
- `symbol VARCHAR(30) NOT NULL`
- `fiscal_year INTEGER NOT NULL`
- `fiscal_quarter INTEGER NULL` (1~4 또는 NULL=연간)
- `revenue NUMERIC(20,2) NULL` (매출액)
- `operating_profit NUMERIC(20,2) NULL` (영업이익)
- `net_income NUMERIC(20,2) NULL` (당기순이익)
- `total_assets NUMERIC(20,2) NULL`
- `total_liabilities NUMERIC(20,2) NULL`
- `total_equity NUMERIC(20,2) NULL`
- `per NUMERIC(10,4) NULL`
- `pbr NUMERIC(10,4) NULL`
- `roe NUMERIC(10,4) NULL`
- `debt_ratio NUMERIC(10,4) NULL`
- `source_url TEXT NULL`
- `retrieved_at TIMESTAMPTZ NOT NULL DEFAULT now()`
- unique `(symbol, fiscal_year, fiscal_quarter)`
- index `(symbol, fiscal_year DESC, fiscal_quarter)`
- FK `symbol -> ticker_metadata.symbol ON DELETE CASCADE`

결론:
- 현재 스키마만으로 구현 가능
- 단 `corp_code` 매핑이 별도 필요 (DART는 symbol이 아니라 corp_code 기반)

## 데이터 소스

**OpenDART API** (https://opendart.fss.or.kr)
- 단일회사 주요계정: `/api/fnlttSinglAcnt.json` (간단 9개 계정)
- 단일회사 전체 재무제표: `/api/fnlttSinglAcntAll.json` (XBRL 전체)
- 일일 호출 한도: `20,000회` (DART 일반)

선택:
- 초기 구현은 **단일회사 주요계정 API** 사용 — 매출/영업이익/당기순이익/자산/부채/자본 핵심 9계정만 필요
- PER/PBR/ROE/debt_ratio는 계산 가능 (PER/PBR은 가격이 필요 → PriceCache 의존)
- 첫 적재에는 PER/PBR/ROE를 NULL로 두고, 후속 단계에서 가격 캐시와 함께 계산

corp_code 매핑:
- DART는 8자리 `corp_code`를 기반으로 동작
- `https://opendart.fss.or.kr/api/corpCode.xml` 다운로드 → ZIP 풀기 → XML 파싱
- 매핑: `stock_code(6자리)` → `corp_code(8자리)`
- 매년 1회 정도 갱신 (신규 상장/상폐 반영)
- 저장 위치 옵션:
  - **A. in-memory dict** (앱 부팅 시 로드, 단순) — 1순위
  - B. 별도 lookup 파일 (`seeds/corp_code_map.json`)
  - C. `ticker_metadata`에 `corp_code` 컬럼 추가 — DB DDL 필요 (Alembic 미사용 정책상 보류)

## 환경 변수 / 외부 의존성

- `DART_API_KEY` (이미 `.env`/`.env.example`에 있음)
- requirements.txt 추가: 별도 추가 없음 — `requests`로 직접 호출 (이미 있음)
- corp_code 파싱: 표준 라이브러리 `xml.etree.ElementTree` + `zipfile`

## 확정 정책

### 1. 적재 건수와 백필 범위

기본값:
- 초기 백필: 최근 `5년` × `4분기` + 연간 `5년` = 최대 `25 row`
- 운영 정책: 분기당 1행 (4Q) + 연간 1행 (NULL quarter)
- 종목당 row 상한: `60` (15년치) — 그 이상은 archive 또는 삭제

운영 원칙:
- 토론에서 5년 추세 + 직전 분기 비교 가능
- DART 보고서 종류:
  - 1분기 (사업보고서 코드 `11013`)
  - 반기 (`11012`)
  - 3분기 (`11014`)
  - 사업 (연간, `11011`)
- 각 분기마다 `fiscal_year` + `fiscal_quarter` 매핑하여 적재

### 2. 중복 제거 / 정정 처리

기본:
- `(symbol, fiscal_year, fiscal_quarter)` unique
- 동일 분기 재공시(정정 공시) 발생 시 `UPSERT`로 덮어쓰기 — `retrieved_at` 갱신

`fiscal_quarter` 매핑:
- 1분기 (3월말 기준) → `fiscal_quarter = 1`
- 반기 (6월말 기준) → `fiscal_quarter = 2`
- 3분기 (9월말 기준) → `fiscal_quarter = 3`
- 사업보고서 (12월말 기준) → `fiscal_quarter = 4`
- 연간 누적값을 별도로 가지려면 `fiscal_quarter = NULL`로 저장 (선택)

초기 구현:
- 1~4분기 행만 적재. 연간 NULL row는 후속 도입.
- 4분기 = 사업보고서의 연간 누적 데이터 그대로

### 3. 갱신 주기

기본값:
- 관심 종목 추가 시: 비동기 수집 `1회 즉시 실행` (5년치 백필)
- 정기 갱신: 매일 KST `02:00` 1회 (전일 공시된 분기 보고서 반영)
- 강제 재수집 최소 간격: `6시간`

운영 방향:
- 분기 공시는 결산일로부터 45일 이내 (사업보고서는 90일)
- 결산월 직후 1~2개월이 갱신이 의미 있는 시기 (그 외에는 신규 데이터 없음)
- 매일 sweep을 돌리되, 신규 row 0건이면 skip 통계로 처리
- `force=True`는 6시간 최소 간격만 무시하며 Redis lock은 우회하지 않음

### 4. 삭제 정책 / TTL

기본:
- 재무제표는 *영구성*. TTL 없음.
- row 상한 `60`(15년) 초과 시 오래된 분기부터 삭제
- 정정 공시 발생 시 `UPSERT`로 갱신

cleanup:
- 매일 02:30 KST에 row 상한 초과분 정리 (실질적으로 거의 발생 안 함)

## 저장 전략

`financial_cache` 최소 저장 필드:
- `symbol`
- `fiscal_year`, `fiscal_quarter`
- `revenue`, `operating_profit`, `net_income`
- `total_assets`, `total_liabilities`, `total_equity`
- `source_url` = DART 보고서 viewer URL
- `retrieved_at`

후속 계산 필드 (Phase 후반에 추가):
- `per` = `close_price` / `net_income_per_share`
- `pbr` = `close_price` / `total_equity_per_share`
- `roe` = `net_income` / `total_equity`
- `debt_ratio` = `total_liabilities` / `total_equity`

계산 시점:
- PER/PBR은 `price_cache` 의존 → 초기 구현에서는 NULL로 두고, 후속 phase에서 `compute_financial_ratios(symbol)` 별도 호출
- ROE/debt_ratio는 재무제표만으로 계산 가능 → 초기 적재 시 함께 계산

DART API 응답 매핑:
- 단일회사 주요계정 응답의 `account_nm`을 키로 매출액/영업이익/당기순이익 등을 추출
- DART 계정명 한글 매핑이 표준화되어 있으므로 dict 매핑 테이블로 처리
- 매핑 dict는 `app/external/dart/financial_account_map.py`에 정의

## Redis 키 컨벤션

- `financial-sync:lock:{symbol}` — 동시 실행 방지 (`SET NX EX=300`)
- `financial-sync:last-sync:{symbol}` — 최근 실행 시각 (6시간 cooldown 판정)
- `financial-sync:sweep:last-run:{mode}` — 전체 sweep 최근 실행 시각
- `dart-api-count:{date}` — 일일 DART API 호출량 (FilingCache와 공유 — DART 한도는 API 키 단위)

운영 환경의 Redis 배치(NCP 서버 + Docker 셀프 호스트, 인증/persistence/메모리 한도)는 `debate-runtime-infrastructure-plan.md`의 "운영 환경 배치" 섹션 참고.

## 수집 함수 시그니처

```python
def sync_financials_for_ticker(
    symbol: str,
    mode: str = "initial",   # "initial" | "refresh"
    force: bool = False,
    backfill_years: int | None = None,
) -> SyncFinancialResult: ...

def compute_financial_ratios(
    symbol: str,
    recompute_years: int | None = None,
) -> ComputeRatiosResult: ...
```

역할:
- `sync_financials_for_ticker`:
  1. `ticker_metadata`에서 종목 확인 + corp_code 매핑
  2. Redis lock 획득
  3. 이미 적재된 (year, quarter) 조합 조회
  4. 누락된 분기 또는 최근 N분기 DART 단일회사 주요계정 API 호출
  5. 응답 정규화 + 계정 매핑
  6. `(symbol, fiscal_year, fiscal_quarter)` upsert
  7. ROE/debt_ratio 같이 계산
  8. row 상한 초과분 정리
- `compute_financial_ratios`:
  1. `financial_cache` + `price_cache` 조인
  2. PER/PBR 계산
  3. upsert로 보강

반환 예시:
- 신규 적재 분기 수
- 갱신된 분기 수 (정정 공시)
- 호출된 DART API 횟수
- 소요 시간(ms)

## 트리거 및 실행 구조

NewsCache와 동일 구조:
1. 사용자가 관심 종목 추가
2. `watchlist` 저장 + DB commit
3. `BackgroundTasks.add_task(sync_watchlist_financials, symbol)` enqueue
4. `sync_watchlist_financials(symbol)`:
   1. `sync_financials_for_ticker(symbol, mode="initial", force=True)`
5. 정기 갱신은 별도 scheduler (`FinancialCacheSchedulerService`)

스케줄러 실행:
- 외부 cron으로 매일 02:00 KST에 `scripts/run_financial_cache_scheduler.py --mode refresh`
- 그 직후 또는 02:30에 `--mode cleanup`

PER/PBR 계산 시점:
- price_cache 갱신 직후(매일 16:00 KST sweep 끝)에 `compute_financial_ratios(symbol)` 호출
- 또는 financial_cache 갱신과 가격 sweep 후 일괄 처리

## 구현 단계

### Phase 0. corp_code 매핑

목표: stock_code → corp_code 매핑 확보

처리 순서:
1. `app/external/dart/corp_code.py` — `download_corp_code()` + `load_corp_code_map()` 함수
2. 앱 부팅 시 한 번 다운로드 → in-memory dict 보관
3. 매핑 갱신은 수동 — 매년 1회 또는 unknown corp_code 발견 시
4. `scripts/refresh_corp_code_map.py` — 수동 실행 스크립트

### Phase 1. DART 클라이언트 + 재무 적재

목표: `sync_financials_for_ticker(symbol)` 구현

처리 순서:
1. `app/external/dart/client.py` — DART API HTTP wrapper (`fnlttSinglAcnt` 등)
2. `app/external/dart/financial_account_map.py` — 한글 계정명 매핑 dict
3. `app/repositories/financial_cache_repository.py` — bulk upsert + 적재된 분기 조회
4. `app/domain/financial_ingestion.py` — `sync_financials_for_ticker` + Redis lock + ROE/debt 계산
5. `scripts/validate_financial_ingestion.py` — 적재/upsert/lock/cooldown/row 상한 시나리오

### Phase 2. watchlist 트리거 연결

목표: watchlist 등록 시 재무 sync

처리 순서:
1. `app/domain/watchlist_service.py`에 `sync_watchlist_financials(symbol)` 추가
2. `app/api/watchlist.py`의 `create_watchlist`에서 background task 추가

### Phase 3. 정기 갱신 / cleanup

목표: 매일 재무 갱신, row 상한 정리

처리 순서:
1. `app/domain/financial_cache_scheduler.py` — `FinancialCacheSchedulerService`
2. `run_watchlist_refresh()` — watchlist 전체 sweep
3. `run_financial_cleanup()` — row 상한 정리
4. `scripts/run_financial_cache_scheduler.py` — cron 진입점

### Phase 4. PER/PBR 보강

목표: 가격 캐시와 조합하여 PER/PBR 계산

처리 순서:
1. `app/domain/financial_ratios.py` — 계산 함수
2. price sweep 직후 또는 financial sweep 끝에 `compute_financial_ratios(symbol)` 호출
3. 별도 검증 스크립트 추가

## 관측성과 로그

최소 구조화 로그:
- `symbol`
- 신규 적재 분기 수
- 갱신된 분기 수 (정정 공시)
- 호출된 DART API 횟수
- 누락된 계정 (account_nm 매핑 실패 시)
- 소요 시간(ms)

추가 운영 지표:
- 종목별 마지막 적재 분기 (`fiscal_year`, `fiscal_quarter`)
- DART API 일일 호출량 (FilingCache와 합산)
- corp_code 매핑 실패율

## 향후 확장 후보

- 전체 재무제표(`fnlttSinglAcntAll`) 적재로 확장 — 자본/현금흐름 세분화
- 연결재무제표 vs 별도재무제표 구분 — 초기는 연결재무제표(`CFS`) 우선
- 동종업종 비교 (`industry` 기반) — 별도 분석 도메인
- IFRS vs K-GAAP 구분 — 초기는 IFRS 한정 (대부분 상장사)
- 영문 재무제표 매핑 — 해외 종목 지원 시점

## 결론

확정 내용:
- 데이터 소스 OpenDART `fnlttSinglAcnt.json` 단일회사 주요계정
- 분기 단위 row + 향후 연간 NULL row 옵션
- 초기 백필 5년 (~20분기), row 상한 60
- 정정 공시는 upsert로 덮어쓰기
- 정기 갱신 매일 KST 02:00, 강제 재수집 최소 6시간
- corp_code 매핑은 in-memory dict (앱 부팅 시 로드)
- PER/PBR은 price_cache 의존 → 후속 phase에서 별도 계산
- watchlist 트리거 NewsCache 동일 패턴
- Phase 0~4 순차 구현
