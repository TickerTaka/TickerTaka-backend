

이번 범위는 **공시 목록 메타데이터 저장**까지다. 백엔드는 DART 페이지로 직접 리다이렉션하지 않고, 프론트가 사용할 수 있는 `source_url`을 DB에 저장하고 API 응답 또는 후속 조회에서 내려줄 수 있게 준비한다.

RAG용 본문 저장, pgvector 청크 저장, 공시 원문 파싱은 이번 담당 범위가 아니다.

## 현재 DB 상태

저장 대상 테이블은 `filing_cache`다.

| column | type | null | 현재 사용 계획 |
| --- | --- | --- | --- |
| `id` | `uuid` | not null | 직접 넣지 않음. DB 기본값 `gen_random_uuid()` 사용 |
| `symbol` | `varchar(30)` | not null | DART `stock_code` 저장. 없으면 요청한 종목 코드 사용 |
| `filing_title` | `text` | not null | DART `report_nm` 저장 |
| `filing_type` | `varchar(100)` | nullable | DART 응답에 유형값이 있으면 저장. 없으면 `NULL` |
| `content` | `text` | nullable | 이번 범위에서는 저장하지 않음. `NULL` |
| `summary` | `text` | nullable | 이번 범위에서는 저장하지 않음. `NULL` |
| `dart_receipt_no` | `varchar(20)` | nullable | DART `rcept_no` 저장. 중복 방지 기준 |
| `source_url` | `varchar(2048)` | not null | DART 뷰어 URL 생성 후 저장 |
| `disclosed_at` | `timestamptz` | nullable | DART `rcept_dt`를 KST 날짜로 변환해 저장 |
| `retrieved_at` | `timestamptz` | not null | 직접 넣지 않음. DB 기본값 `now()` 사용 |
| `ttl_until` | `timestamptz` | nullable | 수집 시점 기준 7일 뒤로 저장 |

현재 SQLAlchemy 모델 `FilingCache`는 이미 존재한다.

- 위치: `app/models/cache.py`
- `dart_receipt_no`는 모델에서 `unique=True`로 선언되어 있음
- 인덱스:
  - `idx_filing_symbol_disclosed`
  - `idx_filing_ttl`

## 현재 코드 상태

이미 추가된 초안 코드:

- `app/external/dart.py`
  - DART `corpCode.xml` 다운로드
  - ZIP 안의 XML 파싱
  - `stock_code -> corp_code` 메모리 매핑
  - DART `list.json` 공시 목록 조회
  - `rcept_no` 기반 DART 뷰어 URL 생성

- `app/repositories/filing_cache_repository.py`
  - `dart_receipt_no` 기준 기존 row 조회
  - `filing_cache` upsert

- `app/domain/filing_ingestion.py`
  - 종목 코드 기준 DART `corp_code` 찾기
  - 최근 N일 공시 조회
  - `filing_cache` 저장용 필드 변환
  - insert/update/skipped 결과 카운트 반환

- `app/domain/watchlist_service.py`
  - `sync_watchlist_filings(symbol)` background sync 함수 추가

- `app/api/watchlist.py`
  - 장바구니 생성 후 기존 뉴스 sync와 함께 공시 sync도 background task로 등록

- `scripts/validate_dart_filing_ingestion.py`
  - 단일 symbol 대상으로 DART 공시 수집과 DB 저장을 수동 검증하는 스크립트

현재 검증 상태:

- `PYTHONPYCACHEPREFIX=/private/tmp/tickertaka_pycache python3 -m compileall app scripts` 통과
- 현재 터미널 Python에 `sqlalchemy`가 없어 DB 연동 스크립트는 아직 실행하지 못함
- 실 DART API 검증은 `DART_API_KEY`가 들어간 의존성 설치 환경에서 필요

## 구현할 동작

### 1. 장바구니 추가 트리거

사용자가 `POST /api/watchlists`로 종목을 추가한다.

기존 흐름:

1. `watchlist` row 생성
2. DB commit
3. background task로 뉴스 수집 실행

추가할 흐름:

1. `watchlist` row 생성
2. DB commit
3. background task로 뉴스 수집 실행
4. background task로 DART 공시 수집 실행

실행 함수:

```python
background_tasks.add_task(sync_watchlist_news, watchlist.symbol)
background_tasks.add_task(sync_watchlist_filings, watchlist.symbol)
```

### 2. DART corp_code 매핑

DART 공시검색 API는 `symbol`을 직접 받지 않고 `corp_code`를 요구한다. 따라서 먼저 DART 고유번호 파일을 사용한다.

호출:

```text
GET https://opendart.fss.or.kr/api/corpCode.xml
```

파라미터:

```text
crtfc_key={DART_API_KEY}
```

응답:

- ZIP binary
- 내부 XML에 `corp_code`, `corp_name`, `stock_code`, `modify_date` 포함

처리:

```text
stock_code == watchlist.symbol
-> corp_code 찾기
```

초기 구현에서는 별도 DB 테이블 없이 프로세스 메모리에 `stock_code -> corp_code` 매핑을 캐시한다.

후속 확장 시 `dart_corp_code_cache` 테이블로 분리 가능하다.

### 3. DART 공시 목록 조회

호출:

```text
GET https://opendart.fss.or.kr/api/list.json
```

파라미터:

```text
crtfc_key={DART_API_KEY}
corp_code={corp_code}
bgn_de={오늘 - 30일, YYYYMMDD}
end_de={오늘, YYYYMMDD}
last_reprt_at=N
sort=date
sort_mth=desc
page_no=1
page_count=100
```

초기 정책:

- 기본 조회 기간: 최근 30일
- 기본 TTL: 7일
- 조회 결과 없음 `status=013`은 에러가 아니라 빈 목록으로 처리
- `status=000`이 아니고 `013`도 아니면 수집 실패로 로그 기록

### 4. DB 컬럼별 저장 방식

DART `list.json` item을 `filing_cache` row로 변환한다.

| DART field | DB column | 변환 규칙 |
| --- | --- | --- |
| `stock_code` | `symbol` | 값이 있으면 그대로 저장. 없으면 요청 symbol 사용 |
| `report_nm` | `filing_title` | 공시 제목으로 저장 |
| `pblntf_ty` 또는 `pblntf_detail_ty` | `filing_type` | 있으면 저장. 없으면 `NULL` |
| 없음 | `content` | `NULL` |
| 없음 | `summary` | `NULL` |
| `rcept_no` | `dart_receipt_no` | DART 접수번호. 중복 방지 키 |
| `rcept_no` | `source_url` | `https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}` |
| `rcept_dt` | `disclosed_at` | `YYYYMMDD`를 `Asia/Seoul` timezone의 `timestamptz`로 변환 |
| 없음 | `retrieved_at` | DB default `now()` 사용 |
| 없음 | `ttl_until` | `datetime.now(UTC) + 7 days` |

예시:

```python
dart_item = {
    "stock_code": "005930",
    "report_nm": "분기보고서 (2024.09)",
    "rcept_no": "20241114001234",
    "rcept_dt": "20241114",
}

filing_cache = {
    "symbol": "005930",
    "filing_title": "분기보고서 (2024.09)",
    "filing_type": None,
    "content": None,
    "summary": None,
    "dart_receipt_no": "20241114001234",
    "source_url": "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20241114001234",
    "disclosed_at": "2024-11-14 00:00:00+09:00",
    "ttl_until": "수집시각 + 7일",
}
```

저장 필수 조건:

- `rcept_no`가 없으면 저장하지 않음
- `report_nm`이 없으면 저장하지 않음
- `source_url`은 항상 `rcept_no`로 생성
- 같은 `dart_receipt_no`가 이미 있으면 새 row를 만들지 않고 update

### 5. Upsert 정책

중복 기준은 `dart_receipt_no`다.

insert 대상:

```text
symbol
filing_title
filing_type
content = NULL
summary = NULL
dart_receipt_no
source_url
disclosed_at
ttl_until
```

conflict 발생 시 update 대상:

```text
symbol
filing_title
filing_type
source_url
disclosed_at
retrieved_at = now()
ttl_until
```

`content`, `summary`는 이번 범위에서 덮어쓰지 않는다. 추후 다른 담당자가 요약이나 본문을 채우는 경우를 보호하기 위해서다.

## 앞으로 구현/보강할 일

### 1차 완료 조건

- [x] DART client 작성
- [x] `filing_cache` repository 작성
- [x] `FilingIngestionService` 작성
- [x] watchlist 생성 후 background task 연결
- [x] 수동 검증 스크립트 작성
- [x] 문법 검증 통과
- [ ] 의존성 설치 환경에서 watchlist flow 검증
- [ ] 실 DART API key로 `validate_dart_filing_ingestion.py` 실행
- [ ] 실제 `filing_cache`에 row가 저장되는지 DB에서 확인

### 실 API 검증 명령

의존성 설치 및 `.env`에 `DART_API_KEY`가 있는 환경에서 실행한다.

```bash
python3 -m scripts.validate_dart_filing_ingestion --symbol 005930 --limit 10
```

검증할 것:

- `fetched_count`가 0 이상으로 나오는지
- `inserted_count` 또는 `updated_count`가 정상 집계되는지
- `filing_cache.source_url`이 DART 뷰어 URL로 저장되는지
- 같은 명령을 두 번 실행했을 때 중복 row가 생기지 않고 update로 처리되는지

### API flow 검증

장바구니 추가 시 background task가 뉴스와 공시를 모두 등록하는지 확인한다.

```bash
python3 -m scripts.validate_watchlist_api
```

현재 스크립트는 mock 기반으로 `sync_watchlist_news`, `sync_watchlist_filings` 호출 여부를 검증하도록 수정되어 있다.

## 주의사항

- `hc` 브랜치에서 개발 중이므로 코드 변경은 가능하다.
- 다만 클라우드 DB는 팀 공유 자원이므로 DDL 변경은 임의로 적용하지 않는다.
- 이번 구현은 `filing_cache` 기존 컬럼만 사용한다.
- DART 원문 본문은 가져오지 않는다.
- 프론트 리다이렉션은 백엔드가 직접 수행하지 않는다. 백엔드는 `source_url` 저장과 응답 준비만 담당한다.

## 후속 확장 후보

- `dart_corp_code_cache` 테이블 추가
- Redis lock/cooldown으로 같은 symbol 공시 중복 수집 방지
- `data_refresh_job`에 filing job 상태 기록
- 정기 스케줄러에서 watchlist 전체 symbol 공시 갱신
- 공시 유형 필터링
- LLM 요약이 필요해지면 `summary`만 별도 업데이트
- RAG 담당 영역과 `filing_cache.id` 또는 `dart_receipt_no` 기준 연동
