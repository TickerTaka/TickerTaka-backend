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
- **로딩 정책 (구체화)**: 부팅 시 로컬 캐시 파일(`seeds/corp_code_map.json`) 우선 → 없거나 만료(1년 초과) 시 DART에서 다운로드 → 파일 저장 + in-memory dict
- 저장 위치 옵션:
  - **A. in-memory dict + 로컬 파일 캐시** (앱 부팅 시 로드, 단순) — 1순위
  - B. `ticker_metadata`에 `corp_code` 컬럼 추가 — DB DDL 필요 (Alembic 미사용 정책상 보류)

## 환경 변수 / 외부 의존성

- `DART_API_KEY` (이미 `.env`/`.env.example`에 있음)
- requirements.txt 추가: 별도 추가 없음 — `requests`로 직접 호출 (이미 있음)
- corp_code 파싱: 표준 라이브러리 `xml.etree.ElementTree` + `zipfile`

## 확정 정책

### 1. 적재 건수와 백필 범위

**초기 구현 범위 (분기 row만)**:
- 최근 `5년` × `4분기` = 최대 `20 row` (연간 NULL row는 미도입)
- 운영 정책: 분기당 1행 (Q1~Q4)

**후속 확장 범위 (연간 NULL row 도입 시)**:
- 5년 × (4분기 + 1 연간) = 최대 `25 row`

종목당 row 상한: `60` (15년치) — 초기 구현 기준에서는 5년 = 20 row, 상한은 여유분 확보.

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

**Valuation date 기준 (PER/PBR 후속 phase 결정 사항)**:
- 같은 분기 재무 row에 PER/PBR을 어떤 가격 기준으로 계산할지 명시 필요
- 후보:
  - **a. 분기말 종가** — `fiscal_quarter` 종료일(3/31, 6/30, 9/30, 12/31) 직전 거래일 종가
  - b. 공시일 종가 — DART 공시 시점의 종가 (보통 결산일 + 45~90일)
  - c. 최신 종가 — 토론 시점의 가장 최근 종가 (시점에 따라 변동)
- 같은 row에 대해 계산값이 흔들리지 않도록 후속 phase에서 단일 기준으로 고정 (현재는 a. 분기말 종가 권장 — 분기 데이터의 "당시 가치" 의미가 가장 명확)
- 최신 종가 기준 PER/PBR은 별도 컬럼/뷰로 두는 것도 가능 (현재 스키마에는 없음)

DART API 응답 매핑:
- 단일회사 주요계정 응답의 `account_nm`을 키로 매출액/영업이익/당기순이익 등을 추출
- DART 계정명 한글 매핑이 표준화되어 있으므로 dict 매핑 테이블로 처리
- 매핑 dict는 `app/external/dart/financial_account_map.py`에 정의

연결/별도 재무 정책:
- **초기 구현은 연결재무제표(`CFS` — Consolidated Financial Statements) 우선**
- 연결 응답이 없는 종목(작은 기업)은 별도재무제표(`OFS` — Own Financial Statements)로 fallback
- DART API 호출 시 `fs_div` 파라미터로 명시 (`CFS` 또는 `OFS`)
- 같은 종목이라도 분기마다 연결/별도 혼재 가능 → 적재 시 `fs_div` 별도 기록 검토 (현재 스키마에는 없음, 후속 확장 후보)
- IFRS 한정 (대부분 상장사) — K-GAAP은 후속

## Redis 키 컨벤션

- `financial-sync:lock:{symbol}` — 동시 실행 방지 (`SET NX EX=300`)
- `financial-sync:last-sync:{symbol}` — 최근 실행 시각 (6시간 cooldown 판정)
- `financial-sync:sweep:last-run:{mode}` — 전체 sweep 최근 실행 시각
- `dart-api-count:{date}` — 일일 DART API 호출량 (FilingCache와 공유 — DART 한도는 API 키 단위, **`date`는 KST 기준 `YYYY-MM-DD` 일관 적용** — news의 `naver-api-count` 정책과 통일)

운영 환경의 Redis 배치(NCP 서버 + Docker 셀프 호스트, 인증/persistence/메모리 한도)는 `debate-runtime-infrastructure-plan.md`의 "운영 환경 배치" 섹션 참고.

## 토론 코드 연계 (a543ff1 커밋 기준)

토론 에이전트의 `data_agent` 노드가 본 cache 데이터를 `debate_repo.fetch_financial_context(symbol)`로 SELECT:

```sql
SELECT fiscal_year, fiscal_quarter, revenue, operating_profit,
       net_income, per, pbr, roe, debt_ratio
FROM financial_cache WHERE symbol=$1
ORDER BY fiscal_year DESC, fiscal_quarter DESC NULLS LAST LIMIT 4
```

영향:
- 본 plan 적재 컬럼이 위 SELECT와 일치 (분기 4개 = 최근 1년치)
- PER/PBR/ROE/debt_ratio 모두 SELECT 대상 — 본 plan Phase 4 (PER/PBR 보강)가 토론 품질에 직접 영향
- 초기 적재 시 PER/PBR이 NULL이면 토론에서 "N/A"로 표시됨 — 빨리 채우는 게 유리
- evidence 영구화 시 `evidence.financial_cache_id` 외래 키로 cache row 참조

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

## 검증/보완 메모 (2026-05-22)

1. `corp_code`를 in-memory dict로 두는 선택은 초기엔 적절하지만, 앱 프로세스 재기동마다 다운로드할지 파일 캐시를 둘지 명시가 아직 약하다. 운영에서는 "부팅 시 로컬 캐시 우선, 없으면 다운로드" 정도로 한 단계 더 구체화하는 편이 안전하다.
2. `fiscal_quarter = NULL` 연간 row를 후속으로 미루면서도 초기 백필 건수 예시는 `연간 5년`을 포함하고 있다. 현재 구현 범위 기준 건수와 후속 범위 기준 건수가 섞여 있으니, 초기 구현 기준 최대 row 수를 별도로 적는 편이 명확하다.
3. PER/PBR은 price cache 의존인데, 어떤 가격 기준일(분기말, 공시일, 최신 종가)을 쓸지 아직 결정이 없다. 이 기준이 없으면 같은 재무 row에 대해 계산값이 흔들릴 수 있으므로, 후속 phase 전에 valuation date 기준을 먼저 정해야 한다.
4. DART 주요계정 API는 계정명이 표준적이지만 기업/연결/별도 재무 기준 차이가 있다. 초기 구현에서 `연결(consolidated) 우선`인지 `별도(separate) fallback`인지 명시가 필요하다.
5. `row 상한 60`은 충분히 여유 있지만, 실제로는 재무 데이터가 매우 작다. cleanup 구현은 가능하되 우선순위는 낮고, 초기 단계에선 upsert/정합성/계정 매핑 정확도 검증이 훨씬 중요하다.
6. `dart-api-count:{date}`를 Financial/Filing이 공유한다고 했으므로, 일일 카운터 기준 날짜(KST/UTC)는 두 plan과 `news-cache`의 KST 정책에 맞춰 통일하는 게 좋다.

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
