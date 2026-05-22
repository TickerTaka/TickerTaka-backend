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
- 기존 row 보강 시에도 PG `content`는 채우지 않음
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
  - `--force`
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
