# 2026-05-23 Stage 2 NewsCache 옵션 B 전환 + 로컬 Chroma reindex 경로

## 범위

`memo/process/plan-implementation-order.md`의 Stage 2 구현:

1. `news-cache option B` 전환
2. `evidence_indexing` 추가
3. `reindex_local_chroma.py` 추가

## 구현 내용

### 1. NewsCache 옵션 B 전환

- `app/domain/news_ingestion.py`
  - `INITIAL_FETCH_COUNT = 30`
  - `REFRESH_FETCH_COUNT = 5`
  - `BODY_CRAWL_LIMIT = 10`
  - `MAX_CACHE_ROWS = 10`
- PostgreSQL `news_cache.content`는 항상 `NULL`
- 본문 추출 성공 + Filter B 통과한 row만 PG 적재
- metadata-only partial insert 제거
- 기존 row는 보강하지 않고 `dedup_skipped`로 집계
- cleanup은 `trim_rows_for_symbol()`만 유지

### 2. Scheduler / Repository 정리

- `app/domain/news_cache_scheduler.py`
  - `MAX_CONTENT_ROWS` 제거
  - cleanup은 expired delete + row trim만 수행
- `app/repositories/news_cache_repository.py`
  - `trim_content_for_symbol()` 제거
  - `list_by_symbol()` 추가

### 3. 로컬 RAG 인덱싱 경로

- `app/domain/evidence_indexing.py`
  - `EvidenceIndexingService.reindex_news_for_symbol()`
  - PG `news_cache` row를 순회하면서 `source_url` 기준 본문을 재추출
  - 본문이 있으면 로컬 Chroma collection `news`에 upsert
- `scripts/reindex_local_chroma.py`
  - `--symbol`
  - `--all-watchlist`
  - `--reset`
  - `--reset`은 대상 symbol delete가 아니라 로컬 `news` 컬렉션 전체 재생성 의미

## 정책 정합성

- PG는 메타데이터 캐시
- 본문은 PG에 저장하지 않음
- 본문은 **크롤링 성공 시 즉시 로컬 ChromaDB에도 upsert**
- `reindex_local_chroma.py`는 복구/백필용 경로로 유지

즉 현재 구조는:

1. `sync_news_for_ticker()`가 PG 메타 row 적재
2. 같은 본문으로 로컬 Chroma `news` 컬렉션에 direct upsert
3. `reindex_local_chroma.py`는 로컬 Chroma를 다시 만들 때만 사용

## 검증 상태

### 코드 레벨

- `python3 -m compileall app scripts` 통과

### Stage 1 선행 인프라

- `python -m scripts.validate_chroma_connection` 통과
  - `heartbeat_ok = true`
  - 검증 전용 컬렉션 `news_validate` upsert/query/delete 정상
  - 검증 전용 컬렉션 `filing_validate` upsert 정상

### Stage 2 검증 스크립트

- `scripts/validate_news_ingestion.py`
  - 통과
  - 옵션 B 기준 기대값으로 갱신
  - 핵심 포인트:
    - `content`는 항상 `NULL`
    - scrape 실패 시 row drop
    - 본문 추출 성공 row만 적재
    - 기존 row dedupe는 `dedup_skipped_count`로 별도 집계
- `scripts/validate_evidence_indexing_news.py`
  - 통과
  - PG 임시 row 1건 생성
  - fake scraper로 본문 공급
  - 검증 전용 컬렉션 `news_validate_reindex`에 `row.id` 기준 upsert 검증
  - 끝나면 PG / Chroma 모두 cleanup

## 라이브 확인 포인트

라이브 sync 확인 시 기대 상태:

- `inserted`는 0 이상일 수 있음
- 적재 row는 존재
- 하지만 `news_cache.content`는 모두 `NULL`

즉 라이브 점검에서 중요한 건:

- row가 들어갔는지
- `summary`, `source_url`, `published_at` 메타가 정상인지
- `content_not_null = 0 / N` 인지

## 다음 단계

Stage 2의 direct upsert / recovery 경로 검증은 완료되었고, 다음 구현 단계는 Stage 3 cache plan 확장이다.

## 구현 메모: reindex와 direct upsert의 trade-off

Stage 2 최종 구조는 ingestion 시점 direct upsert + backup reindex 경로 병행이다.

direct upsert의 장점:

- 본문 크롤링 1회로 PG 메타 적재 + Chroma 인덱싱 동시 처리
- 같은 본문을 다시 크롤링하지 않아도 됨
- 각자 로컬 Chroma를 쓰는 졸프 단계에서도 공용 PG 기준으로 개발 가능

reindex를 남겨두는 이유:

- 로컬 Chroma 볼륨이 날아갔을 때 복구 필요
- direct upsert 실패 시 특정 symbol만 다시 채우는 백필 경로 필요
- legacy PG row를 다시 읽어 로컬 인덱스를 재구성할 수 있어야 함

즉 현재 역할 분담:

1. **기본 경로**: `sync_news_for_ticker()`에서 direct upsert
2. **복구 경로**: `reindex_local_chroma.py`

## 추가 검증 결과

사용자 셸 기준 추가 확인:

- `python -m scripts.validate_chroma_connection`
  - 통과
- `python -m scripts.validate_news_ingestion`
  - 통과
  - 대표 시나리오:
    - `initial_insert`: `inserted=5`, `final_rows=5`
    - `duplicate_update`: `inserted=1`, `updated=0`, `filtered=2`
    - `filtering_policy`: `inserted=3`, `filtered=8`
- `python -m scripts.validate_evidence_indexing_news`
  - 통과
- `python -m scripts.reindex_local_chroma --all-watchlist --reset`
  - 통과
  - 결과 예시:
    - `symbol = 000660`
    - `scanned_rows = 19`
    - `indexed_rows = 18`
    - `skipped_rows = 1`
    - `failed_rows = 0`

해석:

- watchlist 대상 종목의 PG `news_cache` row를 읽어
- 로컬 Chroma `news` 컬렉션에 실제로 재색인됨
- 스킵 1건은 본문 재추출 실패/빈 본문 케이스

---

## 검증/보완 메모 (2026-05-23, plan 대조)

`news-cache-policy-revision-plan.md`(옵션 B)와 `vector-db-and-evidence-retrieval-plan.md`(Phase 2-3)의 닫힘 기준에 대해, 본 보고서에 명시된 산출물(`news_ingestion.py` 수정, `news_cache_scheduler.py` 수정, `news_cache_repository.py` 수정, `evidence_indexing.py` 신설, `reindex_local_chroma.py` 신설, 검증 스크립트 갱신/추가)이 모두 존재한다. 단 코드를 자세히 보면 다음 사항이 추가 점검 대상이다.

### 정합성 확인 (OK)

- `INITIAL_FETCH_COUNT=30`, `REFRESH_FETCH_COUNT=5`, `BODY_CRAWL_LIMIT=10`, `MAX_CACHE_ROWS=10`이 plan 변경 결정과 정확히 일치 (`news_ingestion.py:69-73`)
- `_build_news_row`가 `content=None` 무조건 고정 (`news_ingestion.py:360`) — plan의 "항상 NULL" 정책 정합
- `_passes_storage_filters`가 본문 추출 성공 + Filter B 통과 row만 통과시켜 partial insert (P1) 제거 정합
- `_upsert_chroma_row`가 sync 직후 direct upsert 호출, `_delete_chroma_documents`로 trim 시 chroma 동기 삭제 — 보고서의 "direct upsert + backup reindex 병행" 구조 일치
- `NewsCacheSchedulerService.run_news_cleanup`이 expired/trim 양쪽에서 chroma delete 동기화 (`news_cache_scheduler.py:121-146`) — direct upsert 정책의 자연스러운 짝
- `NewsCacheRepository.trim_rows_for_symbol_returning_ids` 추가로 cleanup 시 삭제 ID 회수 → vector-db Phase 5(cleanup 동기화)의 cache 쪽 인터페이스 닫힘
- `EvidenceIndexingService.build_news_document`와 `reindex_news_for_symbol`이 `news_cache.id` ↔ ChromaDB document.id 1:1 매핑 (UUID 단일 청크) — vector-db plan의 단일 청크 우선 정책 일치
- `metadata_name_match` 시나리오가 P1 제거 회귀를 단위 수준에서 강제 (보고서가 명시한 "partial_insert_rejected"와 사실상 동일 기능)
- `drop_on_scrape_failure` / `body_failed_empty_content` / `initial_insert`의 `final_content_rows == 0` assert로 "content 항상 NULL" 회귀 테스트 닫힘

### 보완 필요 / 누락

1. **`news_ingestion.py:165-178`의 "기존 row 보강" else 분기가 dead code** — `_select_body_candidate_groups`는 `new_candidates = [c for c in candidates if c.existing is None]`만 처리해서 existing row는 절대 `body_urls`에 들어가지 않는다. 결과적으로 existing 후보는 모두 `scraped is None` → `_passes_storage_filters` False → `result.filtered_count += 1`로 빠지고, 165-178행의 update 경로 (`candidate.existing.summary = ...`, `_upsert_chroma_row(candidate.existing, scraped)`)는 어떤 입력으로도 실행되지 않는다. 본 보고서의 "기존 row 보강 시에도 PG content는 채우지 않음"이 *옳지만, 그 경로 자체가 실행되지 않으므로 실효 없는 주장*이다.
   **권장**: dead code 삭제 또는 (메타 보강이 필요한 의도였다면) existing 후보도 body_candidate_groups에 포함시키도록 정책 명문화.

2. **existing row가 `filtered_count`로 집계되는 점이 통계 의미를 흐림** — Filter A/B로 컷된 후보와 "이미 적재된 동일 URL이라 무시"가 같은 카운터에 합산된다. 실 라이브에서 `filtered`가 부풀어 보임.
   **권장**: `existing_skipped`(또는 `dedup_skipped`) 카운터를 따로 두거나, 적어도 보고서에 이 의미를 명시.

3. **`scripts/reindex_local_chroma.py`의 `--force` 옵션이 실효 없음** — `EvidenceIndexingService.build_news_document`와 `reindex_news_for_symbol`이 `force` 값을 chroma metadata에 `"force": bool`로 넣을 뿐, 어떤 분기에도 영향을 주지 않는다. `--reset` 처리는 script-level `delete_collection`이 하고, per-symbol `reset=True` 경로(`evidence_indexing.py:50-51`)는 실제로 사용되지 않는다(script가 `reset=False`만 전달).
   **권장**: `--force`를 제거하거나, 본래 의도(예: "이미 indexed_at metadata 있는 row 다시 색인")를 구현. metadata에 들어가는 `"force"` 키도 검색 잡음이 될 수 있어 제거 권장.

4. **`reindex_news_for_symbol`이 매 row마다 ArticleScraper로 본문을 *재추출*** — plan의 "ChromaDB는 재생성 가능한 RAG 인덱스" 정책상 의도된 흐름이지만, 라이브 실행 시 종목당 ~10건 × HTTP 요청이 발생한다(추가 검증의 `000660`은 19건 중 18건 indexed). naver-api-count는 increment 안 되지만 실제 외부 네트워크 호출은 늘어남.
   **권장**: rate limit 회피 위해 row 간 sleep 옵션 또는 동시성 제한 추가 검토. 또한 direct upsert 경로가 본문을 이미 가진 상태에서 호출하므로, 두 경로의 본문 추출 결과 차이(trafilatura 버전 차이 등)를 가끔 감시.

5. **plan 닫힘 기준의 "라이브 검증: SK하이닉스 watchlist 등록 → `content_not_null = 0` + 적재 10건 중 실제 유의미 기사 비율 측정"이 본 보고서에 부분만 있음** — reindex 라이브(`000660`)는 통과 결과(scanned=19, indexed=18)가 적혀 있으나, *direct upsert 흐름 자체*(즉 `sync_news_for_ticker`를 라이브로 한 번 돌려 PG content NULL 유지 + chroma 즉시 적재 + 적재 10건 유의미 비율 정성 샘플)는 결과로 적혀 있지 않음. plan의 닫힘 기준 중 "본문 추출 성공률/유의미 기사 비율 정성 측정"은 후속 보강 권장.

6. **`news_cache_scheduler.run_news_cleanup`이 "수동 reindex 우선" 정책과 약간 다른 방향** — `news-cache-policy-revision-plan.md` "ChromaDB 동기화 정책 (수동 reindex 우선)" 섹션은 "cleanup은 PG만, ChromaDB orphan은 `reindex_local_chroma.py --reset`으로 정리"라고 적혀 있지만, 실제 코드는 cleanup 시 chroma delete를 즉시 호출한다. 본 보고서가 "direct upsert + backup reindex 병행"으로 정책을 의식적으로 옮겼으니 일관성은 있으나, plan 본문에는 아직 이 변경이 반영 안 됨.
   **권장**: 두 plan 문서 중 하나(news-cache-policy-revision 또는 vector-db Phase 5)의 표현을 코드에 맞춰 갱신.

7. **신규 시나리오 4종 명시 누락** — plan은 `content_always_null`, `chroma_upsert_on_insert`, `chroma_delete_on_cleanup`, `partial_insert_rejected`의 4개 시나리오를 권고. 코드 상으로는 기존 시나리오에 흡수돼 있다(initial_insert/drop_on_scrape_failure/metadata_name_match가 1·4 회귀, NullChromaClient로 2·3은 mock 통과). 다만 이름으로는 명시되지 않아 *후속 검증 회귀에서 어떤 시나리오가 어떤 변경을 막아주는지* 추적이 어렵다.
   **권장**: 시나리오 이름을 plan의 표현에 맞춰 alias 추가 또는 docstring으로 명문화.

8. **`evidence_indexing.py`의 chroma metadata에 `published_at`이 ISO string으로 들어가지만 `None`도 허용** — chroma의 일부 버전은 `None` 값을 metadata에 허용 안 함. 현재 코드는 `row.published_at.isoformat() if row.published_at else None`으로 None을 명시 삽입. 라이브 reindex가 성공한 점으로 보아 현재 chromadb 0.5.23은 허용하지만, 차후 업그레이드 시 회귀 위험.
   **권장**: None일 경우 metadata 키 자체 제외 또는 sentinel 문자열 사용.

### 다음 단계 권고

- dead code 정리(보완 1번)와 `--force` 정리(보완 3번)는 Stage 3.3(filing-cache)에서 같은 패턴을 복제하기 전에 해두면 후속 비용 절감
- 라이브 SK하이닉스(또는 다른 종목)로 `sync_watchlist_news` 한 번 실행 후 `content_not_null` 측정 + 적재 10건 카드(title/summary) 정성 리뷰가 plan 닫힘 기준 마무리 작업
- 시나리오 alias/docstring 정리는 Stage 4 토론 도메인 시작 전에 끝내 두면 evidence 회귀 추적 비용 절감

### 반영 완료

- `news_ingestion.py`
  - dead code였던 기존 row update 경로 제거
  - 기존 row 무시는 `dedup_skipped_count`로 분리 집계
- `evidence_indexing.py`
  - Chroma metadata에서 `None` 값(`published_at`) 삽입 제거
- `reindex_local_chroma.py`
  - 실효 없는 `--force` 옵션 제거
