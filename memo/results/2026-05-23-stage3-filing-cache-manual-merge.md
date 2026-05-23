# Stage 3.3 Filing Cache 수동 통합 결과 (`mergedb`)

## 범위

`origin/hc`의 filing-cache 구현을 `mergedb`에 수동 흡수했다.

이번 1차 범위:
- `DartClient`에 filing API 기능 흡수
- `FilingCacheRepository` 추가
- `FilingIngestionService` 추가
- `watchlist -> filing ingest -> filing indexing` 자동 연결
- filing validation script 2종 추가

이번 범위 밖:
- `filing_cache_scheduler`
- `run_filing_cache_scheduler.py`
- 별도 filing retrieval 계층 신설

## 반영 파일

- `app/external/dart/client.py`
- `app/external/dart/__init__.py`
- `app/repositories/filing_cache_repository.py`
- `app/domain/filing_ingestion.py`
- `app/domain/evidence_indexing.py`
- `app/domain/watchlist_service.py`
- `app/api/watchlist.py`
- `scripts/validate_dart_filing_ingestion.py`
- `scripts/validate_filing_evidence_retrieval.py`

## 구현 요약

### 1. DART filing 기능 흡수

현재 `app/external/dart/` 패키지 구조는 유지하고, `client.py`에 filing 기능만 추가했다.

추가된 기능:
- `DartFilingItem`
- `DartApiError`
- `get_corp_code_by_stock_code()`
- `list_filings()`
- `build_viewer_url()`
- `fetch_document_xml()`
- `extract_document_text()`
- `fetch_filing_text()`

정책:
- `financial-cache`가 쓰는 기존 `DartClient` 흐름은 유지
- filing 기능만 같은 클라이언트에 확장
- DART 일일 카운터는 기존 `dart-api-count:{KST date}`를 그대로 공유

### 2. filing cache 적재

`FilingCacheRepository`와 `FilingIngestionService`를 추가했다.

핵심:
- `dart_receipt_no` 기준 upsert
- `content=None`, `summary=None` 유지
- `source_url`은 DART viewer URL
- TTL은 ingestion 시점에 계산

즉 `hc` 구현의 PG 메타 적재 정책은 유지했다.

### 3. watchlist 자동 연결

`sync_watchlist_filings(symbol)`를 추가했다.

동작:
1. `FilingIngestionService.sync_filings_for_ticker(symbol)`
2. 같은 task 안에서 `EvidenceIndexingService.reindex_filing_for_symbol(symbol)`

즉 filing은 news처럼 ingestion 함수 본체를 direct upsert로 바꾸지 않고,
`watchlist` background task에서 **PG 적재 직후 자동 indexing**으로 연결했다.

### 4. evidence indexing 확장

`EvidenceIndexingService`에 filing 경로를 추가했다.

추가된 기능:
- `ReindexFilingResult`
- `reindex_filing_for_symbol()`
- `build_filing_document()`

컬렉션:
- 실컬렉션: `filing`
- 검증컬렉션: `filing_validate_reindex`

## 검증 상태

확인 완료:
- `python3 -m compileall app scripts` 통과
- 사용자 셸 기준:

```bash
source venv/bin/activate
python -m scripts.validate_dart_filing_ingestion
python -m scripts.validate_filing_evidence_retrieval
python -m scripts.validate_watchlist_flow
```

실제 결과:

- `validate_dart_filing_ingestion`

```json
{"symbol":"000020","fetched":3,"inserted":1,"updated":1,"skipped":1,"final_rows":2}
```

- `validate_filing_evidence_retrieval`

```json
{"symbol":"000020","scanned_rows":1,"indexed_rows":1,"skipped_rows":0,"failed_rows":0,"collection_count":1,"fetched_id":"4a792d31-19f2-4624-b825-aca831463b29"}
```

- `validate_watchlist_flow`
  - 기존 `service_flow`, `empty_watchlist`, `missing_user`, `missing_ticker`, `background_trigger`, `background_failure` 통과 확인
  - filing background trigger는 이후 스크립트에 추가 보강

참고:
- Chroma telemetry 경고
  - `Failed to send telemetry event ClientStartEvent: capture() takes 1 positional argument but 3 were given`
  - 기능 실패 원인은 아니며 filing 검증 결과에는 영향 없음

## 판단

현재 구현은 계획서 기준으로 다음까지 반영된 상태다.

- `hc` filing 도메인 로직 수동 흡수
- 현재 `dart` 패키지 구조 유지
- `watchlist`에 filing background sync 추가
- filing indexing 경로 추가

남은 것은 filing background trigger 보강 검증 결과 확인과, 그 결과를 반영한 후 Stage 3 결과 문서 통합이다.
