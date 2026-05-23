# Filing Cache 수동 통합 계획 (`mergedb`)

## 목적

`mergedb` 브랜치에서 `origin/hc`의 `filing-cache` 구현을 **수동 흡수**하여, 현재 `plandb` 계열에서 정리된 Stage 1~3 인프라/정책 위에 `Phase 3.3 filing-cache`를 닫는다.

핵심 원칙:
- `hc`를 그대로 merge/cherry-pick 하지 않는다.
- **현재 브랜치의 공용 인프라와 정책을 우선 유지**한다.
- `hc`에서는 **filing 도메인 로직과 검증 시나리오를 최대한 그대로** 가져온다.
- 단, 현재 브랜치와 충돌하는 공용 계층(`dart`, `watchlist`, `evidence_indexing`, `chroma_client`)은
  **현재 구조에 맞게 감싸서 흡수**한다.
- 충돌 파일은 “더 최근 구조 + 더 일반화된 구현 + 이미 검증된 정책”을 우선 채택한다.

---

## 현재 전제

### 현재 브랜치(`mergedb`)에서 이미 닫힌 것

- Stage 1: 공용 Redis / Chroma / Embedding 인프라
- Stage 2: `news-cache` Option B
  - PG는 메타만 저장
  - ingest 시점 direct Chroma upsert
  - `reindex_local_chroma.py`는 복구/백필용 유지
- Stage 3.1: `price-cache`
- Stage 3.2: `financial-cache`
  - `corp_code` 패키지 구조 도입
  - `DartClient`는 현재 `app/external/dart/` 패키지 구조

### `origin/hc`에만 있는 것

- `app/domain/filing_ingestion.py`
- `app/repositories/filing_cache_repository.py`
- `scripts/validate_dart_filing_ingestion.py`
- `scripts/validate_filing_evidence_retrieval.py`
- filing용 DART 본문 추출 흐름
- **filing scheduler는 없음**

### 현재 충돌 예상 파일

- `app/domain/watchlist_service.py`
- `app/api/watchlist.py`
- `app/domain/evidence_indexing.py`
- `app/external/chroma_client.py`
- `app/config.py`
- `app/external/dart/*`

결론:
- **수동 통합이 필수**
- 자동 merge는 현재 구조를 망가뜨릴 가능성이 큼

---

## 채택 기준

### 현재 브랜치 구현을 유지할 것

1. `app/external/dart/` 패키지 구조
- 이유: `financial-cache`가 이미 의존 중
- `hc`의 단일 파일 `app/external/dart.py`로 되돌리면 구조 퇴행

2. `app/external/chroma_client.py`
- 이유: Stage 1에서 이미 `news`/`filing` 컬렉션과 validation 흐름 검증 완료

3. `app/domain/evidence_indexing.py`
- 이유: Stage 2 direct upsert + recovery 경로가 이미 정리됨
- filing support는 이 파일을 **확장**하는 방식으로 붙인다

4. `app/domain/watchlist_service.py`, `app/api/watchlist.py`
- 이유: 현재 `news + price + financial` background sync가 이미 구현됨
- 여기에 `filing` task만 추가하는 것이 정합적

### `origin/hc`에서 가져올 것

1. `FilingIngestionService`의 핵심 플로우
- corp_code 조회
- `list.json` 호출
- filing metadata upsert
- TTL/skip/insert/update 집계

2. `FilingCacheRepository`
- `dart_receipt_no` 기준 upsert 전략

3. DART filing 본문 추출 로직
- `document.xml`
- ZIP/XML/HTML 파싱
- 본문 텍스트 추출

4. filing 검증 스크립트 시나리오
- metadata ingestion 검증
- filing evidence indexing/retrieval 검증

정책:
- filing 도메인 로직은 `hc` 구현을 **최대한 그대로 채택**
- 현재 브랜치와 겹치는 공용 계층에서만 최소 수정
- `hc` 구현의 `content=None`, `summary=None` 저장 정책도 그대로 유지

---

## 목표 구조

### 1. DART 계층

현재:
- `app/external/dart/client.py`
- `app/external/dart/corp_code.py`
- `app/external/dart/financial_account_map.py`

통합 후:
- `client.py`에 filing API 기능 추가
  - `list_filings(...)`
  - `build_viewer_url(...)`
  - `fetch_document_xml(...)`
  - `extract_document_text(...)`
  - `fetch_filing_text(...)`
- `corp_code.py`는 그대로 재사용
- 필요 시 filing 전용 parser 분리:
  - `app/external/dart/document_parser.py`

채택 원칙:
- financial 기능은 유지
- filing 기능만 같은 패키지에 추가
- `hc`의 단일 파일 구조는 사용하지 않음

### 2. Filing ingestion

추가 대상:
- `app/repositories/filing_cache_repository.py`
- `app/domain/filing_ingestion.py`

기능:
- 관심 종목 기준 filing metadata 수집
- `dart_receipt_no` dedupe
- `filing_cache` upsert
- TTL/trim/cleanup
- DART 일일 API 카운터는 기존 Redis 규칙 재사용

초기 통합 범위:
- `watchlist -> filing ingest`
- `watchlist task 안에서 자동 filing indexing`
- validation scripts

이번 통합 범위 밖:
- `filing_cache_scheduler`
- `run_filing_cache_scheduler.py`

이유:
- `hc`에도 scheduler 구현이 없고,
- 우선은 Phase 3.3 핵심인 ingest/evidence 경로를 먼저 닫는 것이 맞다.

### 3. Chroma / evidence

filing은 **news의 옵션 B 정책을 강제하지 않는다.** `hc`의 구현 패턴(PG 메타 적재 + 별도 인덱싱) 그대로 유지하되, 누락 방지를 위해 watchlist sync task 안에서 자동 reindex를 트리거한다.

원칙:
- `filing_cache.content`는 NULL 유지 (hc 정책 그대로)
- `FilingIngestionService.sync_filings_for_ticker`는 `list.json` 호출 → PG 메타 적재까지만 수행 (hc 그대로)
- 본문 추출 + 임베딩 + Chroma `filing` 컬렉션 upsert는 `EvidenceIndexer.index_filing_rows` / `reindex_symbol`에서 수행 (hc 그대로)
- **watchlist sync task 흐름**: `sync_watchlist_filings(symbol)` 안에서 `FilingIngestionService.sync_filings_for_ticker(symbol)` 직후 `EvidenceIndexer(session).reindex_symbol(symbol)` 자동 호출

근거:
- news는 본문 크롤링 fallback 경로가 있어 *본문 확보 시점 = PG row 채택 시점* 묶음이 의미 있음 (direct upsert)
- filing은 DART `document.xml`이 단일 reliable 소스 → 본문 확보 가능성 ≈ 1, fallback 경로 없음 → ingest와 인덱싱을 분리해도 외부 호출 횟수 동일
- 따라서 두 패턴의 결과(API 호출량, Chroma 적재 결과)는 동등하며, hc 함수 본체는 손대지 않고 watchlist task에서 한 줄 트리거만 추가하는 것이 사용자 원칙("hc 방식 수정 금지, 충돌 회피만")과 가장 정합

### 4. Watchlist 트리거

현재:
- `sync_watchlist_news`
- `sync_watchlist_prices`
- `sync_watchlist_financials`

추가:
- `sync_watchlist_filings`

최종:
- watchlist 생성 시 `news + price + financial + filing` background task enqueue

---

## 구현 단계

### Phase A. 비교/흡수 설계

목표:
- `hc` filing 구현에서 가져올 코드 경계 확정
- 현재 브랜치 유지 대상 확정

닫힘 기준:
- 파일 단위 채택 기준 문서화
- 충돌 파일별 통합 방향 확정

### Phase B. DART filing 기능 흡수

작업:
- 현재 `app/external/dart/client.py`에 filing API 추가
- 필요 시 parser 분리
- `DartApiError`/retry/카운터 정책을 filing 경로에도 일관 적용

닫힘 기준:
- filing metadata + document.xml 추출을 현재 패키지 구조에서 수행 가능

### Phase C. filing cache 도메인 추가

작업:
- `FilingCacheRepository`
- `FilingIngestionService`
- TTL/cleanup 정책 반영

닫힘 기준:
- `sync_filings_for_ticker(symbol)` 동작
- row insert/update/skip 집계 가능

### Phase D. Chroma / evidence 통합

작업:
- `EvidenceIndexingService`에 filing 경로 확장
- watchlist task 안에서 filing indexing 자동 연결

닫힘 기준:
- filing row → `filing` 컬렉션 임베딩 저장 가능
- filing evidence indexing 검증 가능

### Phase E. watchlist / API 통합

작업:
- `sync_watchlist_filings`
- `app/api/watchlist.py` enqueue 추가

닫힘 기준:
- watchlist 생성 시 filing background sync까지 포함

### Phase F. 검증 / 문서화

작업:
- validation scripts 정리
- Stage 3 결과 문서 확장 또는 filing 별도 결과 문서 작성
- process 문서에서 Phase 3 완료 판정 가능 상태로 갱신

닫힘 기준:
- fake 검증 + 가능한 범위의 live 검증 완료
- 문서 정합성 확보

---

## 파일별 통합 전략

| 파일 | 채택 방향 |
|---|---|
| `app/external/dart/client.py` | **현재 파일 유지 + hc filing 기능 흡수** |
| `app/external/dart.py` (`hc`) | **직접 도입 안 함** |
| `app/external/chroma_client.py` | **현재 유지** |
| `app/domain/evidence_indexing.py` | **현재 유지 + filing 지원 추가** |
| `app/domain/watchlist_service.py` | **현재 유지 + filing task 추가** |
| `app/api/watchlist.py` | **현재 유지 + filing enqueue 추가** |
| `app/repositories/filing_cache_repository.py` | **hc 기반 신규 추가** |
| `app/domain/filing_ingestion.py` | **hc 기반 신규 추가, 최소 수정만 적용** |
| `scripts/validate_dart_filing_ingestion.py` | **hc 기반 신규 추가/수정** |
| `scripts/validate_filing_evidence_retrieval.py` | **hc 기반 신규 추가/수정** |

---

## 주의할 충돌 지점

### 1. filing 인덱싱 정책

news의 옵션 B(direct upsert)를 filing에 강제하지 않는다.

근거:
- news 옵션 B는 *크롤링 실패가 빈번*하다는 전제에서 본문 확보 시점에 PG 적재를 묶기 위한 정책
- filing은 DART API 단일 소스, fallback 경로 없음 → 두 패턴(direct upsert vs PG 적재 + 별도 reindex)의 외부 호출 횟수가 동등
- 사용자 원칙: hc 구현 방식을 수정하지 않고, 실제 *충돌*(import/시그니처/패키지 구조)만 어댑팅

이번 filing 통합 정책:
- hc의 `FilingIngestionService` 본체는 PG 메타 적재까지만 수행 (그대로)
- `EvidenceIndexer.index_filing_rows` / `reindex_symbol`로 본문 추출 + Chroma `filing` upsert (그대로)
- **`sync_watchlist_filings(symbol)`에서 두 단계를 자동 연결** — PG 적재 직후 같은 task 안에서 reindex 트리거
- 그 외 `force` 같은 Stage 2 구식 옵션은 흡수하지 않음 (사용 코드 없음 → 자연 정리)

### 2. DART 클라이언트 계층 중복

`hc`의 `app/external/dart.py`와 현재 `app/external/dart/`는 공존하면 안 된다.

정책:
- 현재 패키지 구조 유지
- filing 관련 메서드만 흡수

### 3. watchlist enqueue 누락

`hc`는 `news + filing`
현재는 `news + price + financial`

최종 통합 목표:
- `news + price + financial + filing`

### 4. evidence index 차원/컬렉션 충돌

이미 news에서 겪었던 문제를 filing에도 반복하지 않도록:
- 검증 컬렉션과 실컬렉션 분리
- `filing_validate` 같은 검증용 컬렉션 사용

### 5. 토론 컨텍스트 계층 범위

- `EvidenceRetriever`는 현재 브랜치 기준 구현 대상이 아님
- `debate_repo.fetch_filing_context()`는 이미 존재함
- 따라서 이번 통합 범위는:
  - filing row 적재
  - watchlist task 안에서 자동 filing indexing
  - filing evidence indexing 검증
  - `debate_repo.fetch_filing_context()`와 컬럼 정합성 확인
- 이번 통합 범위 밖:
  - 새로운 filing retrieval 계층 추가
  - 별도 `EvidenceRetriever` 구현

---

## 검증 계획

### 1. 단위/시뮬레이션 검증

- `validate_dart_filing_ingestion.py`
  - corp_code 있음/없음
  - 신규 filing insert
  - 기존 receipt update
  - skip count
- `validate_filing_evidence_retrieval.py`
  - filing row 인덱싱
  - Chroma `filing` 컬렉션 조회
  - symbol filter retrieval

### 2. watchlist 연동 검증

- watchlist 생성 시 filing task enqueue 확인
- existing `validate_watchlist_flow.py` 또는 별도 검증 확장

### 3. 로컬 Chroma 검증

- `validate_chroma_connection.py`는 유지
- filing 전용 validation collection 추가 필요 시 분리

### 4. 라이브 검증

가능 조건:
- `DART_API_KEY`
- 로컬 Chroma 실행 중

검증 예:
- 종목 1개에 대해 filing metadata 적재
- `filing` 컬렉션 count 증가
- source_url이 DART viewer URL인지 확인

---

## 닫힘 기준

다음을 만족하면 `mergedb`에서 filing 수동 통합 구현 시작 승인 가능:

1. 현재/`hc` 중 무엇을 유지하고 무엇을 흡수할지 명확하다.
2. `app/external/dart/` 패키지 구조 유지에 동의한다.
3. filing은 `hc` 구현대로 `PG content=NULL`, `summary=NULL` 정책을 유지한다.
4. filing 인덱싱은 ingest 함수 본체를 바꾸지 않고, watchlist task에서 자동 연결하는 방식으로 간다.
5. watchlist 최종 트리거가 `news + price + financial + filing`임을 확정한다.
6. 검증 스크립트 범위가 metadata ingestion + filing evidence indexing까지 포함됨을 확정한다.

구현 완료 판정 기준:

1. `sync_filings_for_ticker()` 동작 (PG 메타 적재까지)
2. `filing_cache` upsert/trim/cleanup 동작
3. **`sync_watchlist_filings(symbol)` 안에서 PG 적재 직후 `EvidenceIndexer.reindex_symbol(symbol)`이 자동 트리거되어 `filing` 컬렉션에 본문이 적재됨**
4. watchlist background filing sync 동작
5. validation scripts 통과
6. `debate_repo.fetch_filing_context()`와 컬럼 정합성 확인
7. DART 일일 카운터(`dart-api-count:{KST date}`)가 financial / filing 양쪽에서 같은 키로 incr
8. Stage 3 문서에 filing까지 포함되어 전체 닫힘 판정 가능

---

## 검증/보완 메모 (2026-05-23)

- `hc`의 filing 도메인 로직은 가져올 가치가 충분하다.
- 하지만 `hc`의 `dart.py` 단일 파일 구조는 현재 `financial-cache`와 충돌하므로 채택하면 안 된다.
- filing도 news와 동일하게 **direct Chroma upsert** 정책으로 맞추는 것이 현재 단계에선 가장 깔끔하다.
- `hc`에는 scheduler 구현이 없으므로, 이번 수동 통합 1차 범위는 ingest/evidence/watchlist까지로 자르는 것이 맞다.
- `debate_repo.fetch_filing_context()`는 이미 있으므로, 새로운 retrieval 계층을 구현하기보다 현재 SELECT 컬럼과 적재 컬럼 정합성만 맞추면 된다.
- 구현 전 추가로 확인할 것:
  - 현재 `FilingCache` 모델이 Option B (`content NULL`) 정책과 충돌 없는지
  - `debate_repo.fetch_filing_context()`와의 정합성

---

## 검증/보완 메모 (2026-05-23, plan vs hc 실제 코드 대조)

본 계획서가 명시한 채택 기준은 큰 틀에서 맞으나, **hc 브랜치의 실제 코드 형태를 보면 "최대한 그대로 가져온다"는 표현 그대로는 빌드/실행이 안 된다.** 인터페이스 차이가 크고, 사용자가 명시한 "방식은 수정하지 말고 구현된 정도만 가져온다" 원칙과 plan의 일부 항목이 부분적으로 충돌한다. 아래 항목을 plan 본문 갱신 또는 mergedb 작업 체크리스트로 반영해야 안전하다.

### A. 호환 안 됨 — 흡수 시 어댑터 필수 (그대로 가져오면 import / runtime 실패)

1. **`ChromaClient` 인터페이스 시그니처가 완전히 다름**
   - 현재 (`plandb`): `upsert(name, documents: Sequence[ChromaDocument], *, embedding_client)` — wrapper가 임베딩 내부 호출, `ChromaDocument` dataclass 사용
   - hc: `upsert_documents(collection_name, ids, documents, embeddings, metadatas)` — embeddings를 호출자가 만들어 넘김
   - hc는 추가로 `get_existing_ids`, `delete_by_symbol`, `delete_by_ids`, `query`(positional)도 가짐 — 현재 wrapper에는 `get_existing_ids` / `delete_by_symbol` 없음
   - **결과**: hc `EvidenceIndexer.index_filing_rows`(`chroma.upsert_documents(...)` 직접 호출)를 그대로 가져오면 NameError 발생. 두 가지 선택:
     - (i) 현재 wrapper에 `get_existing_ids` / `delete_by_symbol` 메서드 추가 + hc 인덱서 호출부를 `upsert(name, [ChromaDocument(...)], embedding_client=...)` 호출로 다시 씀
     - (ii) hc `EvidenceIndexer`의 골격(메서드 흐름)만 가져오고, chroma 호출은 모두 현재 wrapper 시그니처로 바꿔 씀
   - 본 plan 본문 244행 "`evidence_indexing.py` 현재 유지 + filing 지원 추가" 결정과 가장 정합하는 선택은 **(ii)** — filing 전용 메서드(`index_filing_rows`, `reindex_filing_for_symbol` 등)를 현재 `EvidenceIndexingService`에 추가하되, 호출부는 현재 wrapper로 변환.

2. **`EmbeddingClient`가 Protocol vs 구체 클래스로 갈림**
   - 현재: `EmbeddingClient`는 Protocol, 구체 클래스는 `HuggingFaceEmbeddingClient` / `OpenAIEmbeddingClient` / `DeterministicEmbeddingClient` (Stage 1 보고서)
   - hc: `EmbeddingClient`라는 *단일 구체 클래스* (provider 분기 내장), `EmbeddingClient()` 직접 instantiation
   - **결과**: hc `EvidenceIndexer.__init__(... embedder: EmbeddingClient | None = None, ...)`에서 `embedder or EmbeddingClient()`로 fallback하면 Protocol을 instantiate하려는 셈이 되어 TypeError. 채택 시 모든 호출부를 `get_embedding_client()`(현재 헬퍼)로 교체 필요.

3. **`DartClient` 구조와 corp_code 인터페이스 비호환**
   - 현재: `app/external/dart/client.py` (financial용) + `app/external/dart/corp_code.py` (`CorpCodeProvider`). `CorpCodeProvider().get_corp_code(symbol)` → `str | None`
   - hc: `app/external/dart.py` 단일 파일, `DartClient.get_corp_code_by_stock_code(stock_code)` → `DartCorpCode | None` (dataclass, `.corp_code` 속성)
   - plan 본문 117행 "`corp_code.py`는 그대로 재사용"과 실제 hc 코드가 어긋남: hc `FilingIngestionService.sync_filings_for_ticker`는 `self.dart_client.get_corp_code_by_stock_code(normalized_symbol)`을 호출하고 `corp_code.corp_code`를 사용한다. 그대로 가져오면 동작 안 함.
   - **권장 작업**: hc `FilingIngestionService` 본문의 `corp_code = self.dart_client.get_corp_code_by_stock_code(...)` → `corp_code_str = self.corp_code_provider.get_corp_code(symbol); if not corp_code_str: skip` 형태로 어댑팅. plan 본문 채택 원칙(`hc 도메인 로직 최대한 그대로`)을 지키려면 어댑터 줄 수가 최소화되도록 `FilingIngestionService(...)` 생성자에 `corp_code_provider`를 inject하는 방식이 자연스럽다.

4. **`DartApiError` / `DartCorpCode` / `DartFilingItem` 클래스 위치**
   - hc `FilingIngestionService`와 `EvidenceIndexer`가 `from app.external.dart import DartClient, DartFilingItem, DartApiError`를 import한다.
   - 현재 `app/external/dart/__init__.py`는 `CorpCodeProvider, DartClient, FinancialStatementRecord`만 export.
   - **권장 작업**: filing 흡수 시 현재 `dart` 패키지에 다음을 추가
     - `DartFilingItem` dataclass (hc 그대로 복사)
     - `DartApiError` 예외 (hc 그대로 복사)
     - 그리고 `__init__.py`에 export 추가
   - plan 본문 117-119행("필요 시 filing 전용 parser 분리: `document_parser.py`")과 호환 — `DartFilingItem`/`DartApiError`는 client.py 또는 별도 `filing_types.py`에 둘 수 있음. 결정 필요.

### B. plan 정책 ↔ hc 구현 정책 (사용자 결정 반영)

5. **인덱싱 정책 — 결정됨**
   - 사용자 결정: filing은 news 옵션 B(direct upsert)를 강제하지 않음. hc의 "PG 적재 + 별도 reindex" 패턴 유지.
   - 단, 누락 방지를 위해 `sync_watchlist_filings(symbol)` 안에서 PG 적재 직후 `EvidenceIndexer(session).reindex_symbol(symbol)`을 자동 트리거.
   - 근거: filing은 DART `document.xml` 단일 reliable 소스라 fallback이 없고, 두 패턴의 외부 호출 횟수가 동등.
   - plan 본문 152-164행("Chroma / evidence")과 261-275행("filing 인덱싱 정책")이 이 결정을 반영해 갱신됨.
   - 닫힘 기준 3번을 "PG 적재 직후 자동 reindex 트리거" 형태로 갱신함.

6. **Redis lock / cooldown — hc 그대로 유지**
   - 사용자 원칙: hc 구현 방식 수정 금지, 실제 충돌만 회피.
   - filing의 lock/cooldown 부재는 *충돌*이 아니라 *일관성 차이*이므로 이번 통합 범위 밖.
   - 단일 사용자 졸프 단계에서 동시 sync 위험은 낮고, `dart_receipt_no` unique constraint가 PG 측 최종 방어선.
   - 운영 진입 시점에 필요하면 후속 plan으로 분리.

7. **`dart-api-count` 일일 카운터 — 충돌 회피용 추가만 필요**
   - 현재 `app/external/dart/client.py`(financial)는 Stage 3 보완에서 `dart-api-count:{KST date}` Redis 카운터 추가됨.
   - hc `DartClient`(filing 메서드 포함)는 카운터 없음 → 흡수 시 financial은 카운트되지만 filing은 안 카운트되는 *비대칭* 발생 = 충돌.
   - 사용자 원칙 ("실제 충돌만 피함") 적용: filing API 메서드(`list_filings`, `fetch_document_xml`)에도 같은 키로 incr 추가 — hc 방식 자체를 바꾸는 게 아니라 카운터 한 줄 끼우는 보강.
   - plan 본문 137행 ("DART 일일 API 카운터는 기존 Redis 규칙 재사용")의 책임 위치를 명시.

### C. 모델/스키마 정합성

8. **`FilingCache.summary` 컬럼은 nullable** (`app/models/cache.py:185-186`) — hc `repo.upsert_filing(...)`이 `summary=None`으로 저장하므로 PG 스키마 호환 OK. 다만 `debate_repo.fetch_filing_context()`가 `summary` 컬럼을 SELECT하므로 토론 컨텍스트에 `summary`는 항상 NULL 상태로 노출됨. UI/토론 측에서 빈 summary를 어떻게 표시할지 별도 결정 필요. plan 384행 "구현 전 추가로 확인할 것"에 이미 명시 → OK, 보완 책임 위치만 분명히.

9. **`dart_receipt_no String(20) unique=True`** (`app/models/cache.py:187`) — hc upsert가 `index_elements=[FilingCache.dart_receipt_no]`로 PG `ON CONFLICT`. unique constraint가 column-level이라 정합 ✓. DART receipt no는 14자리 숫자이므로 20자리 컬럼은 여유.

10. **`disclosed_at` 타임존**: hc `_parse_receipt_date`는 KST tz로 저장(`replace(tzinfo=KST)`). 현재 PG `disclosed_at` 컬럼은 `TIMESTAMPTZ`(=내부 UTC 저장) — Postgres가 자동 변환. `debate_repo.fetch_filing_context`가 ORDER BY `disclosed_at DESC`로 사용 → 정합 ✓. 단 news/price/financial은 모두 UTC로 저장 — 일관성 측면에서 미세 차이.

### D. plan에서 다루지 않은 hc 변경분 (의도 명시 필요)

11. **`docker-compose.yml`의 chroma 섹션이 hc와 현재가 다름** (`git diff --name-status` 결과에 M으로 잡혀 있음)
    - hc: `command: ["--host", "0.0.0.0", "--port", "8080"]`, ports `8080:8080`, env vars 없음
    - 현재(mergedb/plandb): command 없음, ports `8080:8000`, `IS_PERSISTENT/ALLOW_RESET/ANONYMIZED_TELEMETRY` 환경변수
    - **Stage 1 보고서 96행이 명시**: "0.5.x 이미지는 1.x CLI의 `run --host --port`를 쓰지 않는다" → 현재가 정답. hc 변경은 **채택 금지**.
    - plan에 명시 누락 → "docker-compose.yml은 현재 유지, hc 형식 무시"를 plan에 추가 권장.

12. **`requirements.txt` 변경 (hc M)** — Stage 1/3 보완에서 핀 정책이 적용된 현재 버전이 우선. hc 측 변경은 무시. plan 명시 누락.

13. **`.env.example` / `.gitignore` (hc M)** — hc가 어떤 키를 추가했는지 plan에서 다루지 않음. 적어도 `OPENAI_API_KEY`, `EMBEDDING_PROVIDER`, `EMBEDDING_MODEL`은 plandb에 이미 들어가 있으니 hc의 추가 항목이 있다면 그것만 흡수 결정 필요. **권장**: plan에 ".env.example diff 1회 확인" 항목 추가.

14. **`vector_store/chroma.sqlite3` 삭제 (hc D)** — 운영상 무관 (개발 로컬 파일), plan에서 다룰 필요 없음.

15. **`memo/plans/dart-filing-*.md` 3개 (hc A) / `memo/results/2026-05-2[12]-dart-filing-*.md` 2개 (hc A)** — plan은 미언급. 문서 가치가 있다면 도입, 아니면 plandb 측 `filing-cache-ingestion-plan.md`(plan 인벤토리에 이미 있음)와 중복 정리. **권장**: plan에 "문서 흡수 정책 — hc의 dart-filing-* 문서는 *참고용*으로만 도입, 권위 plan은 기존 `filing-cache-ingestion-plan.md`" 한 줄 추가.

16. **`memo/plans/vector-db-and-evidence-retrieval-plan.md`가 hc에서 M** — 우리도 Stage 2 결과로 일부 갱신 권고를 적었음. 두 변경이 충돌 가능. plan 명시 누락.

17. **`scripts/validate_chroma_connection.py` / `scripts/reindex_local_chroma.py`가 hc에 A로 잡혀 있음** — 둘 다 현재(mergedb)에 이미 존재 (Stage 1/2 산출물). 채택 방향(현재 유지)을 plan에 명시 필요. plan 본문 337행 "validate_chroma_connection.py는 유지"는 있으나 reindex 스크립트는 미언급.

18. **`scripts/validate_watchlist_api.py` / `scripts/validate_watchlist_flow.py` (hc M)** — hc가 어떤 가정으로 갱신했는지 미확인. 현재 watchlist enqueue 순서(news+price+financial)와 hc 가정(news+filing)이 다르므로, hc 버전을 그대로 가져오면 회귀. **권장**: 현재 유지 + filing task 추가만 반영하는 형태로 통합.

### E. plan 본문 정정 권장 사항

- **본문 32행 "현재 충돌 예상 파일" 목록에 `docker-compose.yml`, `requirements.txt`, `.env.example`, `.gitignore`, `memo/plans/vector-db-and-evidence-retrieval-plan.md` 추가** — 실제 git diff 기준 충돌함.
- **본문 39행 "filing scheduler는 없음"** — 정합 ✓.
- **본문 117행 "`corp_code.py`는 그대로 재사용"** — 표현은 맞지만 hc 호출부가 인터페이스 다르므로 *어댑팅 필요*임을 명시.
- **본문 152-164 / 261-275행 "filing 인덱싱 정책"** — 사용자 결정 반영해 갱신됨 (hc 패턴 유지 + watchlist task 자동 트리거).
- **본문 244-256 "파일별 통합 전략" 표에 다음 행 추가 필요**:
  - `app/external/embedding.py` — **현재 유지** (Protocol 구조)
  - `docker-compose.yml` — **현재 유지** (hc command 형식 채택 금지)
  - `requirements.txt` — **현재 유지** (Stage 1/3 핀 정책 우선)
  - `app/external/dart.py` (hc) — **버림**
  - `scripts/reindex_local_chroma.py` / `scripts/validate_chroma_connection.py` — **현재 유지**

### F. 작업 순서 권장 (Phase A-F 체크리스트 보강)

plan 본문 Phase B-E는 그대로 두되, 각 Phase 시작 직전 다음 한 줄을 추가 권장:

- **Phase B 시작 전**: 현재 `app/external/dart/__init__.py`에 `DartFilingItem`, `DartApiError` 도입 + filing API 메서드들을 `client.py`에 머지
- **Phase C 시작 전**: hc `FilingIngestionService.__init__`에 `corp_code_provider` inject 인자 추가 (어댑터), `get_corp_code_by_stock_code` 호출을 `corp_code_provider.get_corp_code` + 별도 `DartCorpCode` 객체 합성으로 변환
- **Phase D 시작 전**: 현재 `EvidenceIndexingService`에 hc `EvidenceIndexer`의 메서드(`index_filing_rows`, `reindex_symbol`, `reset_symbol`) 흐름을 흡수 — chroma 호출만 현재 wrapper 시그니처(`upsert(name, [ChromaDocument], embedding_client=...)`)로 변환. embedding 호출은 `get_embedding_client()`로.
- **Phase E 시작 전**: hc `watchlist.py`(news+filing) / `watchlist_service.py`를 그대로 덮어쓰지 말고, 현재 흐름(news+price+financial)에 filing task만 *추가*. `sync_watchlist_filings(symbol)` 본문에 PG 적재 후 `EvidenceIndexer.reindex_symbol(symbol)` 자동 호출 추가 (사용자 결정 옵션 1).
- **Phase F**: validate_watchlist_api.py / validate_watchlist_flow.py는 hc의 가정(news+filing)이 아니라 현재(news+price+financial+filing)로 갱신

### G. 닫힘 기준 추가 권장

plan 본문 354-371행 닫힘 기준에 다음을 추가:

- DART 일일 카운터(`dart-api-count`)가 financial / filing 양쪽에서 동일 키로 incr되는지 확인
- `app/external/dart/__init__.py`의 export 항목에 `DartFilingItem`, `DartApiError` 추가됨
- hc의 `app/external/dart.py`, `app/external/chroma_client.py`, `app/external/embedding.py`, `app/domain/evidence_indexing.py`, `app/domain/evidence_retrieval.py`는 mergedb에 *파일로 새로 도입되지 않음* (내용만 흡수)

### 요약

- plan의 큰 방향(현재 인프라 유지 + filing 도메인 흡수)은 옳다.
- "hc 도메인 로직 최대한 그대로"라는 표현은 hc의 *인터페이스*가 현재와 달라 그대로 실행되지 않으므로 **어댑터 작업이 필수**라는 점을 plan에 명시했다.
- 인덱싱 정책 결정 (사용자 확정): filing은 news 옵션 B를 따르지 않고 hc 패턴(PG 적재 + 별도 reindex) 유지. `sync_watchlist_filings(symbol)`에서 reindex 자동 트리거하는 한 줄만 추가. plan 본문 152-164행, 261-275행, 닫힘 기준 갱신 완료.
- plan 본문이 다루지 않은 hc 변경분(docker-compose, requirements, .env.example, memo 문서, validate_watchlist_*)에 대한 채택 방향을 plan에 명시해야 한다 — D 섹션 참고.
