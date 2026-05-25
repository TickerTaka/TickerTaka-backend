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

---

## 검증/보완 메모 (2026-05-25, 외부 검증)

본 보고서가 명시한 수동 통합 결과를 `filing-cache-manual-merge-plan.md` 채택 기준과 실제 코드를 대조해 검증했다. **plan의 핵심 채택 원칙은 모두 반영되었고**, plan 보완 메모(A~G) 항목 중 plan에서 권장한 어댑팅도 코드에 반영되어 있다.

### plan 채택 기준 vs 실제 구현 (OK)

1. **`app/external/dart/` 패키지 구조 유지** (`client.py:1-283`) — hc의 단일 `app/external/dart.py`는 도입되지 않았고, financial 흐름(`_fetch_single_period`, `fetch_financials`)이 filing 메서드(`list_filings`, `fetch_document_xml`, `extract_document_text`, `fetch_filing_text`)와 같은 `DartClient`에 공존한다. plan 본문 117행 채택 기준 정합.

2. **`__init__.py` export 정합** (`__init__.py:1-10`) — `DartApiError`, `DartFilingItem`이 추가되어 `app.external.dart import DartClient, DartFilingItem, DartApiError` 패턴이 동작한다. plan 보완 메모 A.4 권장 사항 반영 완료.

3. **`DartClient._get`에 retry + dart-api-count 통합** (`client.py:246-259`) — `@retry(wait_exponential, stop_after_attempt(3))` + `_record_daily_api_call()`가 financial / filing 양쪽 모든 호출에 자동 적용된다. plan 보완 메모 B.7(filing 카운터 비대칭 회피) + G(닫힘 기준 — 동일 키 incr) 동시 충족.

4. **`corp_code_provider` 어댑팅** — hc의 `get_corp_code_by_stock_code(stock_code) -> DartCorpCode`(객체)를 `DartClient.get_corp_code_by_stock_code(stock_code) -> str | None`(문자열)로 단순화했고, `FilingIngestionService.sync_filings_for_ticker` 본문도 `corp_code` 변수로 바로 사용하도록 어댑팅됨 (`filing_ingestion.py:59-65`). plan 보완 메모 B.3 권장사항(어댑터 적용)이 가장 단순한 형태로 반영됨.

5. **`FilingCacheRepository.upsert_filing`** (`filing_cache_repository.py:45-81`) — `index_elements=[FilingCache.dart_receipt_no]` 기준 PG `on_conflict_do_update`, `content=None / summary=None` 정책 유지, `retrieved_at=now()` 갱신. plan 닫힘 기준 1-2 충족.

6. **`sync_watchlist_filings(symbol)` 자동 트리거** (`watchlist_service.py:110-131`) — PG 적재 후 같은 task / 같은 session 안에서 `EvidenceIndexingService.reindex_filing_for_symbol(symbol)` 호출. plan 닫힘 기준 3번 ("PG 적재 직후 자동 reindex 트리거") 정확 반영.

7. **`app/api/watchlist.py` enqueue 순서** (`watchlist.py:71-74`) — `news → price → financial → filing` 4-task. plan 본문 178행 최종 목표 정합. `IntegrityError` 발생 시 rollback + 409, enqueue 실패 시 `sync_enqueued=False`로 우회 — 응답 일관성 OK.

8. **evidence indexing 확장 일관성** (`evidence_indexing.py:115-154`) — hc 골격을 가져오되 chroma 호출이 현재 `upsert(name, documents=[ChromaDocument], embedding_client=...)` wrapper 시그니처로 변환됐고, `build_filing_document`가 metadata에 `symbol/source_type=filing/source_url/published_at(disclosed_at)/dart_receipt_no`까지 포함 → retrieval 쪽 metadata 필터(`{"symbol": symbol}`)와 정합. plan 보완 메모 A.1 권장 옵션 (ii) 채택.

9. **수동 통합 1차 범위 (scheduler 제외)** — `filing_cache_scheduler.py` / `run_filing_cache_scheduler.py` 미존재 확인. plan 본문 142-150행 "이번 통합 범위 밖" 정합.

### 검증 결과 (실 데이터 기준)

보고서 102-117행에 인용된 `validate_dart_filing_ingestion`, `validate_filing_evidence_retrieval`, `validate_watchlist_flow` 결과는 코드와 일치하는 시나리오:

- `validate_dart_filing_ingestion.py`의 시드 3 row × FakeDartClient → `fetched=3, inserted=1, updated=1, skipped=1`은 (신규 1건, 기존 receipt 갱신 1건, 빈 report_name 1건 skip) 검증 의도 정확.
- `validate_filing_evidence_retrieval.py`의 `FILING_VALIDATE_COLLECTION = "filing_validate_reindex"` 분리 + `DeterministicEmbeddingClient` 사용 → plan 본문 261-275행 "검증 컬렉션과 실컬렉션 분리" 정합.
- `validate_watchlist_flow.py`에 `run_filing_background_trigger_flow`가 신설되어 `FilingIngestionService` + `EvidenceIndexingService` 모두 patch되어 sync_session 공유 + symbol 전파가 검증됨 → plan 닫힘 기준 4번("watchlist background filing sync 동작") 충족.

### 약점 / 잔여 권장

1. **Redis lock / cooldown 부재** — plan 본문 261-275행 "filing 인덱싱 정책"과 사용자 결정대로 hc 그대로 유지. 단일 사용자 졸프 단계에서는 OK이지만 운영 진입 시 동시 watchlist 재등록 race 가능 → 후속 plan으로 분리.

2. **`FilingIngestionService.sync_filings_for_ticker`가 fetch 후 모두 `existing` lookup만 하고 별도의 변경 감지(internal hash) 없이 매번 `upsert_filing` 수행** — `on_conflict_do_update`가 `retrieved_at=now()`까지 항상 갱신해서 변경 없어도 DB write가 발생. PG IO는 `dart_receipt_no` unique index 한 번이라 비용 작음. 운영 진입 시 정정 공시 검출과 함께 정리.

3. **`FilingCache.summary`는 PG에 항상 NULL인 상태** — `debate_repo.fetch_filing_context()`가 summary 컬럼 SELECT하므로 토론 컨텍스트에 빈 summary 노출. plan 보완 메모 8 (UI/토론 측 빈 summary 처리 결정 필요) 그대로 미해결. Stage 4의 `data_node`는 evidence retrieval로 본문 chunk를 가져오므로 우회되지만, `fetch_filing_context()` 직접 사용 경로(news_chunks fallback)에서 약점.

4. **`reindex_filing_for_symbol`에서 50자 미만 본문 skip이 silent** (`evidence_indexing.py:141-143`) — skipped_rows로 카운트되지만 어떤 receipt가 짧았는지 로그가 없음. 운영 진입 시 진단 로그 추가 권장.

5. **`fetch_filing_text` 50자 임계값(`_MIN_FILING_TEXT_LEN=50`)이 `client.py:27`과 `evidence_indexing.py:141`에 중복** — 한쪽이 raise / 다른 쪽이 skip 하는 이중 가드. 정책상 문제는 없지만 임계값 변경 시 한 곳만 바꾸면 silent 불일치 가능 → 운영 진입 전 단일 상수로 합치는 게 안전.

### 판정

**Stage 3.3 filing-cache 수동 통합 = 구조적으로 닫힘.** plan 본문/보완 메모의 모든 채택 기준과 닫힘 기준 1-7번이 실제 코드에 반영됨. 닫힘 기준 8번(Stage 3 문서 통합)도 본 보고서로 충족. 잔여 약점은 모두 운영 진입 시 후속 plan으로 분리하기에 적절한 범위(scheduler / lock / 정정 공시 검출 / 진단 로그).
