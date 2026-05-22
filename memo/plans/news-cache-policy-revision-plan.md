# News Cache 정책 수정 계획 (옵션 B 채택)

## 배경

기존 `news-cache-ingestion-plan.md`와 그 구현물(main 머지 완료, `app/domain/news_ingestion.py` 등)을 기준으로 다음 정책 전환을 진행한다:

- **옵션 B 채택 (해석 B)**: PostgreSQL `news_cache.content`는 항상 NULL, **본문 추출 성공한 row만 PG에 적재** (partial insert 제거 유지)
- **본문 SOT는 외부 원본** (네이버 기사) — ChromaDB는 *재생성 가능한 RAG 인덱스* (졸프 단계 정책, [[infra-stage-policy]])
- UI 방향: 대시보드 뉴스 카드는 `title` + `summary` 표시 + 클릭 시 `source_url`로 redirection
- 토론 evidence는 ChromaDB 의미 검색으로 retrieval

본 plan은 기존 plan을 *대체하지 않고* 수정 사항만 명시한다. 기존 plan은 historical record로 보존.

같은 결정을 `filing_cache`에도 일관 적용한다 (DART 공시 본문도 동일 정책 — 별도 plan으로 분리하지 않고 본 plan에서 함께 다룸).

**중요**: 본 plan은 `vector-db-and-evidence-retrieval-plan.md`의 *로컬 RAG 인덱스* 전제(졸프 단계 = 공용 ChromaDB 없음, 개발자별 로컬 운영)와 정합한다. ChromaDB 운영 인프라(백업/복구/인증/TLS)는 본 plan 범위 밖이고, 운영 진입 시 별도 plan으로 다룬다.

## 변경 결정 요약

| 항목 | 기존 | 변경 후 |
|---|---|---|
| `news_cache.content` | 본문 텍스트 적재 (있을 때) | **항상 NULL** |
| `filing_cache.content` | 본문 텍스트 적재 (있을 때) | **항상 NULL** |
| 본문 SOT | PostgreSQL | **외부 원본 (네이버/DART)** — ChromaDB는 재생성 가능한 RAG 인덱스 |
| UI 본문 표시 | 가능 | 안 함 (`title`+`summary`+redirection) |
| partial insert (옵션 P1) | 허용 (메타만 적재) | **제거** (본문 추출 성공한 row만 적재) |
| 종목당 본문 적재 상한 (`BODY_CRAWL_LIMIT`) | 5건 | **10건** |
| `INITIAL_FETCH_COUNT` | 20 | **30** |
| `REFRESH_FETCH_COUNT` | 5 | 5 (변경 없음) |
| `MIN_CONTENT_LENGTH` | 120 | 120 (변경 없음) |
| `MAX_CACHE_ROWS` | 100 | **10** (= 본문 상한과 통합) |
| `MAX_CONTENT_ROWS` | 10 | **사용 안 함** (content NULL) |
| TTL | 30일 | 30일 (변경 없음) |
| ChromaDB 동기화 | (없음) | **수동 reindex 스크립트** (sync 직후 동기 upsert는 후속 옵션) |
| ChromaDB id 매핑 | — | `news_cache.id` (UUID) = ChromaDB document.id |
| 클러스터링 / Filter A·B / 그룹 fallback | 그대로 | **그대로** |
| 본문 크롤링(trafilatura) | 그대로 | **그대로** (PG 적재 시 본문 추출 시도) |
| ChromaDB upsert 위치 | (계획되었음) | **`scripts/reindex_local_chroma.py`** (vector-db plan Phase 3) |

## 변하지 않는 흐름

옵션 B 결정과 무관하게 다음은 그대로 유지:

```
네이버 API 응답 (initial 30 / refresh 5)
  ↓ Filter A (그래픽/포토성 제목 차단)
  ↓ 제목 정규화 + Jaccard 0.7 클러스터링
  ↓ 그룹별 대표 후보 선정 + 우선순위
  ↓ 본문 크롤링 (trafilatura, BODY_CRAWL_LIMIT=10, 그룹당 최대 3 fallback)
  ↓ Filter B (관련성, name_kr 매칭, 본문 길이)
  ↓ 적재 (← 여기서만 변경)
```

본문 크롤링/클러스터링은 적재 결정과 무관하게 필요한 단계:
- 본문 quota 보호 (API 절약)
- 적재할 10건 선별 — 본문 추출 성공 + 관련성 통과한 기사 중에서 결정
- ChromaDB 임베딩 대상 — 본문 있는 기사만 임베딩 대상
- 재배포 그룹화

## 옵션 B 흐름 (수집 단계 = PG 적재)

```
Filter B 통과한 후보 (본문 추출 성공 + 관련성 OK) — 최대 10건
  ↓
[PostgreSQL]                       (ChromaDB는 별도 reindex 단계에서 처리)
news_cache INSERT
  - id (UUID)
  - symbol
  - title
  - summary
  - source_name
  - source_url
  - published_at
  - retrieved_at
  - ttl_until
  - content = NULL  ★ (본문은 PG에 저장 안 함)
```

본문 텍스트는 PG에 저장하지 않지만 *수집 단계에서 본문 추출은 시도*한다 — 추출 성공해야 PG에 row 적재 (해석 B). 본문 자체는 ChromaDB에 별도 reindex 단계에서 들어감.

## 옵션 B 흐름 (RAG 인덱싱 단계 = reindex 스크립트)

```
PG news_cache row (content=NULL) ─► source_url 기준 본문 재추출
                                  ─► 임베딩 생성
                                  ─► 로컬 ChromaDB upsert
                                  
ChromaDB collection "news"
  - id = news_cache.id (UUID)
  - document = 본문 텍스트 (재추출)
  - embedding = OpenAI text-embedding-3-small
  - metadata = {symbol, source_id, published_at, source_url}
```

청크 정책: 본문이 짧으면(1만자 미만) 단일 청크. 초과 시 `{uuid}:chunk:N`으로 분할.

**reindex 트리거**: `scripts/reindex_local_chroma.py` (vector-db plan Phase 3) — `--symbol`, `--source`, `--reset`, `--force`, `--all-watchlist` 옵션. 수집 단계 끝에 자동 트리거하지 않음 (수동 또는 cron).

## 영향 받는 모듈

| 모듈 | 변경 영역 |
|---|---|
| `app/domain/news_ingestion.py` | 상수 변경, P1 제거, content=NULL 적재 (ChromaDB upsert는 호출 안 함) |
| `app/domain/news_cache_scheduler.py` | `MAX_CONTENT_ROWS` 관련 로직 제거. cleanup은 PG row만 처리 (ChromaDB delete는 reindex 스크립트 영역) |
| `app/repositories/news_cache_repository.py` | `trim_content_for_symbol` 제거 (의미 없어짐), `trim_rows_for_symbol`만 유지 (= 10건 상한) |
| `app/external/chroma_client.py` (신규) | ChromaDB HTTP client wrapper — vector-db plan Phase 1 |
| `app/external/embedding.py` (신규) | OpenAI 임베딩 호출 + 재시도 — vector-db plan Phase 1 |
| `app/domain/evidence_indexing.py` (신규) | reindex 스크립트가 사용하는 헬퍼 (PG row → ChromaDB document 변환 + 본문 재추출) |
| `scripts/reindex_local_chroma.py` (신규) | vector-db plan Phase 3 — PG row 기준 로컬 ChromaDB 인덱싱 |
| `app/domain/filing_ingestion.py` (구현 시) | 같은 옵션 B 정책 적용 (filing-cache plan 참고) |
| `scripts/validate_news_ingestion.py` | 기존 시나리오 갱신 + 옵션 B 시나리오 추가 |
| `scripts/validate_news_cache_scheduler.py` | cleanup 시나리오 갱신 (ChromaDB delete는 검증 대상 아님 — 로컬 재생성으로 처리) |

## 코드 변경 항목 상세

### A. 상수 변경 (`news_ingestion.py`)

```python
INITIAL_FETCH_COUNT = 30        # 20 → 30
REFRESH_FETCH_COUNT = 5         # 그대로
BODY_CRAWL_LIMIT = 10           # 5 → 10
BODY_ATTEMPTS_PER_GROUP = 3     # 그대로
MAX_CACHE_ROWS = 10             # 100 → 10 (BODY_CRAWL_LIMIT와 통합)
# MAX_CONTENT_ROWS = 10         # 삭제
MIN_TITLE_LENGTH = 8            # 그대로
MIN_CONTENT_LENGTH = 120        # 그대로
```

### B. partial insert (P1) 제거

- `_matches_ticker_reference`의 `if not body_text and cls._contains_exact_name_reference(metadata_text, ...)` 분기 제거
- 본문 추출 실패 또는 Filter B 컷 후보는 적재하지 않음 (전체 skip)
- `_select_body_candidate_groups`의 폭은 그대로 유지 (그룹 내 fallback E 정책 유효)

### C. content 적재 분기 제거

`news_cache` INSERT 시 `content` 필드 무조건 NULL:

```python
# (기존)
content = scraped.content if scraped else None

# (변경)
content = None  # 항상 NULL — 본문은 ChromaDB에 저장
```

scraped.content는 ChromaDB upsert 페이로드로만 사용.

### D. ChromaDB upsert는 sync 흐름에서 호출하지 않음

수집 단계는 PG만 다루고, ChromaDB upsert는 `scripts/reindex_local_chroma.py`(vector-db plan Phase 3)로 분리. sync 함수에 ChromaDB 호출 코드를 추가하지 않음 — 졸프 단계 정책.

운영 진입 시점에 동기 upsert로 전환할 수 있는 hook은 `app/domain/evidence_indexing.py`에 두기 (현재는 reindex 스크립트만 호출).

### E. cleanup은 PG만

`news_cache_scheduler.py`의 `run_news_cleanup`에서 TTL/row 상한으로 삭제된 PG row만 처리. ChromaDB 동기화 안 함:

```python
# (요지)
news_repo.delete_expired_rows(now=now)
for symbol in symbols:
    news_repo.trim_rows_for_symbol(symbol, MAX_CACHE_ROWS)
```

로컬 ChromaDB에 orphan이 남으면 `reindex_local_chroma.py --reset` 또는 `--symbol X`로 수동 재생성.

기존 `trim_content_for_symbol`은 코드에서 제거 (content NULL 정책상 의미 없음).

### F. `MAX_CACHE_ROWS = 10` 의미

본문 적재만 일어나므로 row 수 = 본문 수. row 상한 100건은 의미 없음. 10건으로 통합:
- `BODY_CRAWL_LIMIT = 10` → sync 시점에 최대 10건 적재 시도
- `MAX_CACHE_ROWS = 10` → cleanup 시점에 10건 초과면 가장 오래된 row부터 삭제

두 상수가 동일 값이지만 책임이 다름 (sync vs cleanup) — 별도 유지하되 default를 일치시킴.

## 검증 시나리오 변경

### 기존 시나리오 영향

| 시나리오 | 영향 | 처리 |
|---|---|---|
| `initial_insert` | `BODY_CRAWL_LIMIT` 변경 + content NULL | 기대값 갱신 |
| `body_quota_saved` | 본문 절약 로직 그대로 — content NULL이라 검증 포인트 약간 변경 | 기대값 갱신 |
| `whitespace_variant_match` | 매칭 로직 그대로 유효 | 변경 없음 |
| `metadata_name_match` (P1 검증) | P1 제거됨 — 시나리오 자체 폐기 또는 "rejected" 검증으로 전환 | 시나리오 폐기 또는 변경 |
| `filtering_policy` | P1 제거로 inserted 수 변경 | 기대값 갱신 |
| `body_failed_empty_content` | 빈 본문 → body_failed 카운트는 그대로 | 변경 없음 |
| `body_fallback_within_group` (E) | 그룹 내 fallback 그대로 | 변경 없음 |
| `body_fallback_on_storage_cut` (E') | storage filter 컷 시 fallback 그대로 | 변경 없음 |
| `daily_api_counter` | API 카운터 그대로 | 변경 없음 |
| `ttl_window` / `cooldown_skip` / `lock_skip` | Redis 정책 그대로 | 변경 없음 |

### 신규 시나리오

- `content_always_null`: 본문 추출 성공해도 PG row의 content는 NULL인지 검증
- `chroma_upsert_on_insert`: PG insert 후 ChromaDB에 같은 ID로 document 존재 확인 (mock client로 검증)
- `chroma_delete_on_cleanup`: TTL/row 상한 cleanup 시 ChromaDB delete 호출 확인
- `partial_insert_rejected`: 본문 없는 후보가 들어왔을 때 PG에 row 적재 안 되는지 확인 (P1 제거 회귀)

`scripts/validate_news_ingestion.py`에 위 시나리오 추가 + 기존 시나리오 갱신.

ChromaDB 검증을 위해 in-memory fake ChromaDB client 또는 docker-compose chroma에 별도 컬렉션(`news_test`) 사용.

## 마이그레이션 (기존 PG content 처리)

현재 PG `news_cache.content`에 본문이 일부 적재되어 있다 (라이브 검증 결과 SK하이닉스 등). 옵션 B 전환 시점에 두 가지 처리 옵션:

- **옵션 M1**: 기존 content를 일괄 NULL 처리 (`UPDATE news_cache SET content = NULL`). 단순. PG 용량 즉시 회수.
- **옵션 M2**: 기존 content는 그대로 두고, 신규 적재부터 NULL. TTL 30일 내에 자연 정리.

추천: **옵션 M2** — 기존 데이터로 토론 검증 가능, 자연 정리되니 작업 부담 없음. 단 30일 동안 옵션 A/B 혼재 상태 인지.

ChromaDB 측: 기존 content 있는 row 대상으로 일회성 backfill 스크립트 (`scripts/backfill_chroma_from_news_cache.py`) 작성 권장:
1. `news_cache.content IS NOT NULL` row 조회
2. 각 row에 대해 OpenAI 임베딩 + ChromaDB upsert
3. 이후 신규 sync는 본 plan에 따라 자동 동기화

backfill은 1회 실행, 임베딩 비용 추정 ~$0.01 (현재 약 19건 가정).

## 관측성 변경

기존 sync 로그에 추가:
- `chroma_upserted_count`: ChromaDB upsert 성공 건수
- `chroma_failed_count`: ChromaDB 실패 (fail-soft 후 로그)

기존 cleanup 로그에 추가:
- `chroma_deleted_count`: ChromaDB delete 성공 건수

추가 운영 지표:
- ChromaDB collection별 document 수 (주기적)
- ChromaDB document 수 vs PG news_cache row 수 정합성 (drift 감지)
- 임베딩 API 호출량 / 비용

## 후속 변경 가능성

본 plan은 *현재 결정*을 반영. 다음 시나리오는 미래 plan으로:

- **UX 변경 (UI 본문 표시 필요)**: ChromaDB → PG content backfill 스크립트 작성, 옵션 A로 전환
- **감성분석 도입**: PG `news_cache.sentiment` 컬럼 추가 + ChromaDB metadata 확장 (별도 plan)
- **본문 상한 조정**: 운영 신호(검색 빈 응답률, evidence 중복 사용) 보고 12~15건 상향 또는 8건 하향
- **ChromaDB 백업 자동화**: NCP Object Storage 24h sync (인프라 측 진행)
- **filing-cache 동일 정책 적용**: 본 plan 결정을 `filing_cache`에도 일관 적용

## FilingCache 일관 적용 사항

`filing-cache-ingestion-plan.md`도 본 plan과 같은 결정을 적용:

- `filing_cache.content`는 항상 NULL
- 본문은 ChromaDB collection `filing`에 저장
- partial insert 정책은 없으므로 변경 없음 (filing-cache plan은 처음부터 본문 있는 row만 적재 가정)
- 본문 상한은 그대로 10건 (이미 plan에 명시)
- `dart_receipt_no` unique 기반 dedupe는 그대로 (PG row 메타데이터만 갖고도 dedupe 가능)
- ChromaDB id 매핑: `filing_cache.id` (UUID) = ChromaDB document.id
- cleanup 시 ChromaDB delete 동기화

FilingCache는 아직 구현 전이므로 별도 마이그레이션 불필요 — 구현 시점에 본 plan 따라 적재.

## VectorDB plan 동기 갱신 필요

`vector-db-and-evidence-retrieval-plan.md`는 현재 옵션 A 가정으로 작성됨:

> "Cache row 적재 시 → 동기 또는 비동기 임베딩 upsert"  

## 검증/보완 메모 (2026-05-22)

1. `MAX_CACHE_ROWS = 10` 전환은 "본문 추출 성공 기사만 row 적재"가 전제다. 이 전제가 깨지면 row 상한 10은 메타데이터 보존 정책까지 함께 줄여버리므로, 구현 시 `partial insert 제거`와 반드시 한 묶음으로 반영해야 한다.
2. `INITIAL_FETCH_COUNT = 30`, `BODY_CRAWL_LIMIT = 10`은 옵션 B에서 retrieval pool을 늘리기 위한 결정인데, 현재 relevance precision이 완전히 닫힌 상태는 아니다. 구현 후 대표 종목 3~5개 라이브 샘플로 `적재 10건 중 실제 유의미 기사 비율`을 한 번 재확인하는 단계가 필요하다.
3. `MAX_CONTENT_ROWS` 제거는 맞지만, 스키마상 `content` 컬럼은 남는다. 따라서 코드 검증은 "항상 NULL"을 강제하는 회귀 테스트가 필수다. 컬럼이 남아 있으면 나중에 다른 경로에서 우발적으로 채워질 수 있다.
4. cleanup 시 ChromaDB delete 동기화는 필수인데, Chroma 장애 시 fail-soft로 둘지 fail-closed로 둘지 명시가 필요하다. 현재 문서 방향은 fail-soft에 가깝고, 이 경우에는 `drift 복구용 backfill/reconcile 스크립트`를 별도 운영 항목으로 남겨두는 편이 안전하다.
5. FilingCache까지 같은 옵션 B를 적용한다고 적었으므로, `filing-cache-ingestion-plan.md`의 본문/partial insert/상한 서술도 구현 전 동기 갱신되어야 한다. 지금 상태에선 두 문서 간 일부 표현이 아직 완전히 일치하지 않는다.
6. 본문 SOT를 ChromaDB로 두면 백업/복구 책임도 Chroma 쪽으로 이동한다. 본 plan의 닫힘 기준에는 코드뿐 아니라 `Chroma 백업 경로 + 복구 절차 문서화`가 사실상 포함된다고 보는 게 맞다.
> "metadata에 source_id 보관"  
> "검색 결과(ChromaDB)와 원본 표시(PostgreSQL) 분리"

옵션 B 채택으로 인한 갱신 사항:
- "PostgreSQL은 원본 저장" → "PostgreSQL은 *메타데이터* 저장, **ChromaDB가 본문 SOT**"
- 본문 retrieval 경로: 토론 evidence는 ChromaDB의 document 텍스트 그대로 사용
- 임베딩 대상 텍스트: News는 `title + content` (이전과 동일), Filing은 `filing_title + content`
- 백업 정책 강조: ChromaDB가 SOT라 NCP Object Storage 주기 백업 의무화

별도 작업으로 `vector-db-and-evidence-retrieval-plan.md` 직접 수정 권장 (본 plan에 포함하지 않음).

## 구현 순서

1. **vector-db plan 갱신** (옵션 B 반영) — 본 plan과 별도 작업
2. **`app/external/chroma_client.py`** + **`app/external/embedding.py`** 신설 (`vector-db plan` Phase 1)
3. **`app/domain/evidence_indexing.py`** 신설
4. `news_ingestion.py` 수정:
   - 상수 변경 (`INITIAL_FETCH_COUNT`, `BODY_CRAWL_LIMIT`, `MAX_CACHE_ROWS`)
   - P1 제거
   - content=NULL 분기
   - ChromaDB upsert 호출 추가
5. `news_cache_scheduler.py` 수정:
   - `MAX_CONTENT_ROWS` 관련 로직 제거
   - cleanup 시 ChromaDB delete 동기화
6. `news_cache_repository.py` 수정:
   - `trim_content_for_symbol` 제거
   - `trim_rows_for_symbol`을 ID 반환 형태로 변경 (ChromaDB delete 입력용)
7. 검증 스크립트 갱신
8. (선택) `scripts/backfill_chroma_from_news_cache.py` 1회 실행 — 기존 content 있는 row의 ChromaDB backfill
9. 라이브 검증 — `scripts/live_test_watchlist_sync.py` 결과에 `content_not_null = 0` + ChromaDB collection 적재 확인

## 토론 코드 연계 (a543ff1 커밋 기준)

토론 에이전트의 `data_agent` 노드가 본 cache 데이터를 `debate_repo.fetch_news_context(symbol)`로 SELECT:

```sql
SELECT id, title, summary, source_url, source_name, published_at
FROM news_cache WHERE symbol=$1 AND ttl_until > now()
ORDER BY published_at DESC LIMIT 10
```

영향 (옵션 B 정합):
- `content`는 SELECT 안 됨 — 옵션 B 정책(PG content NULL)과 정합
- 토론 컨텍스트는 `title + " " + summary`로 구성 — `summary`가 비어 있으면 title만 사용됨
- LLM이 본문 자체가 필요할 때는 `evidence_tools.search_evidence`(ChromaDB)에서 retrieval — 본 plan과 별개로 vector-db Phase 4 도입 후 동작
- evidence 영구화 시 `evidence.news_cache_id` 외래 키로 cache row 참조 + ChromaDB metadata `source_id`로 본문 retrieval
- `ttl_until > now()` 필터 — 본 plan의 TTL 30일과 정합

토론 코드는 *이미 옵션 B 패턴에 정합되어 있음* (content를 SELECT 안 함). 옵션 B 전환 시 토론 측 코드 변경 불필요.

## ChromaDB 동기화 정책 (수동 reindex 우선)

본문 SOT가 외부 원본이고 로컬 ChromaDB는 재생성 가능한 인덱스이므로, fail-soft + reconcile 같은 복잡한 동기화 정책은 졸프 단계에서 불필요.

### 인덱싱 흐름

- PG `news_cache` INSERT/UPDATE는 commit
- ChromaDB upsert는 **별도 reindex 스크립트**로 진행 (`scripts/reindex_local_chroma.py`)
- 수집 직후 자동 트리거하지 않음 (운영 진입 시 옵션)

### drift 발생 시

- PG에는 있는데 로컬 ChromaDB에 없는 row → `reindex_local_chroma.py --symbol X` 실행
- PG에 없는데 로컬 ChromaDB에 남은 orphan → `--reset` 옵션으로 collection 재생성
- 졸프 단계에서는 *수동 재실행*으로 충분, 별도 reconcile 스크립트 작성하지 않음

### 운영 진입 시점

- 공용 ChromaDB로 이전하면 fail-soft + reconcile 정책 다시 검토
- 별도 `production-deployment-plan.md` 신설 시점에 fail 정책 / 자동 동기화 / 백업 절차 정리
- 본 plan 범위 밖

## 닫힘 기준 (plan 종료 시점에 검증되어야 할 항목)

졸프 단계 기준:

1. 코드 변경 (상수/P1 제거/content=NULL)
2. 검증 시나리오 갱신 + 신규 시나리오 PASS
3. **content NULL 강제 회귀 테스트** — `news_cache.content`가 어떤 경로로도 채워지지 않음을 보장 (스키마상 컬럼은 남기 때문에 우발적 채움 위험)
4. `scripts/reindex_local_chroma.py` (vector-db plan Phase 3)로 PG news_cache row 기준 로컬 ChromaDB 1회 인덱싱 성공
5. 라이브 검증: SK하이닉스 watchlist 등록 → `content_not_null = 0` + reindex 후 로컬 ChromaDB collection 적재 확인 + **적재 10건 중 실제 유의미 기사 비율 측정** (대표 종목 3~5개 라이브 샘플로 relevance precision 재확인)

운영 진입 시 별도 추가 검증 (본 plan 범위 밖):
- ChromaDB 백업/복구 절차 검증
- fail-soft + reconcile 스크립트 도입
- 공용 ChromaDB 동기화 정합성

## 결론

옵션 B 채택 + 본문 상한 10건 + partial insert 제거를 한 plan으로 묶음. 코드 변경 폭은 크지 않고 (상수 + INSERT 분기 + ChromaDB 호출 추가), 검증 시나리오 일부 갱신 + 신규 추가.

핵심 전제: **`MAX_CACHE_ROWS = 10` 전환은 partial insert 제거와 반드시 한 묶음**으로 반영. 둘 중 하나만 적용 시 메타데이터 보존 정책까지 함께 줄어들어 retrieval pool이 의도와 다르게 축소된다.

FilingCache도 같은 정책 일관 적용 — `filing-cache-ingestion-plan.md` 본문 갱신 완료.

PostgreSQL 용량 절감(~12MB/년 수준)은 그 자체로 큰 효과는 아니지만, **데이터 모델 일관성과 검색 측면 ChromaDB SOT 흐름의 단순화 + 책임 분리**가 본 plan의 주된 가치.

다음 단계는 vector-db plan 옵션 B 반영(완료) → 본 plan 구현 phase 진입.
