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
- 본문 기반 retrieval은 로컬 Chroma 재색인 경로로 분리
- 수집 시점 Chroma direct upsert는 하지 않음

즉 현재 구조는:

1. `sync_news_for_ticker()`가 PG 메타 row 적재
2. `reindex_local_chroma.py`가 PG row를 읽어 본문 재추출 후 `news` 컬렉션 upsert

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
  - 옵션 B 기준 기대값으로 갱신
  - 핵심 포인트:
    - `content`는 항상 `NULL`
    - partial insert 없음
    - 본문 추출 성공 row만 적재
- `scripts/validate_evidence_indexing_news.py`
  - PG 임시 row 1건 생성
  - fake scraper로 본문 공급
  - Chroma collection `news`에 `row.id` 기준 upsert 검증
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

Stage 2 다음 작업은:

- `python -m scripts.validate_news_ingestion`
- `python -m scripts.validate_evidence_indexing_news`
- `python -m scripts.reindex_local_chroma --symbol 005930 --reset`

이 세 경로를 사용자 셸 기준으로 확인한 뒤, Stage 3 이후 cache plan들에 동일 패턴을 확장한다.

## 구현 메모: reindex와 direct upsert의 trade-off

현재 Stage 2는 ingestion 시점에 ChromaDB로 바로 upsert하지 않고, 별도 `reindex_local_chroma.py` 경로를 둔다.

이 구조의 장점:

- 졸프 단계 전제와 정합
  - PostgreSQL만 공용
  - Redis/ChromaDB는 개발자별 로컬 Docker
- 로컬 Chroma를 "재생성 가능한 인덱스"로 유지 가능
- 공용 Chroma 운영 정책 없이도 PG 메타데이터만으로 각자 로컬 RAG 구성 가능

이 구조의 단점:

- 본문 크롤링이 두 번 일어날 수 있음
  1. `sync_news_for_ticker()`에서 적재 후보 선별용 본문 크롤링
  2. `reindex_local_chroma.py`에서 실제 본문 재추출 + 임베딩

즉 기술적으로 더 효율적인 구조는:

1. 본문 크롤링 성공
2. PG 메타 row 저장
3. 같은 본문으로 즉시 Chroma upsert
4. `reindex_local_chroma.py`는 복구/백필 전용

하지만 현재는 "로컬 Chroma / 재생성 가능한 인덱스" 정책 때문에 ingestion과 indexing을 분리한 타협안으로 유지한다.

운영 단계에서 공용 Chroma로 옮기면 direct upsert로 전환하는 것이 자연스럽다.

## 추가 검증 결과

사용자 셸 기준 추가 확인:

- `python -m scripts.validate_chroma_connection`
  - 통과
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

## 남은 확인 사항

- `scripts/validate_news_ingestion.py`
  - 옵션 B 기대값으로 수정 완료
  - 사용자 셸에서 마지막 재실행 1회만 남은 상태
