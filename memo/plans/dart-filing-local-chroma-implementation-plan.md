# DART Filing Local Chroma 구현 계획

## 목표

현재 구현할 범위는 **DART 공시 metadata를 기준으로 공시 본문을 가져와 로컬 ChromaDB에 RAG용 document + embedding으로 저장하는 것**이다.

뉴스는 다른 담당자가 구현한다. 이 계획에서는 `filing_cache`와 `filing` collection만 다룬다.

```text
공용 NCP PostgreSQL
  filing_cache metadata
        |
        | dart_receipt_no 기준 본문 조회
        v
로컬 ChromaDB
  collection: filing
  document: 공시 제목 + 공시 본문
  embedding: HuggingFace sentence-transformers
```

## 현재 상태

이미 되어 있는 것:

- NCP PostgreSQL `stock_debate` DB 사용 중
- `watchlist` 테이블 존재
- `filing_cache` 테이블 존재
- DART API key 설정 완료
- `DartClient`에서 다음 기능 구현 완료
  - `corpCode.xml` 조회
  - stock code -> corp code 매핑
  - `list.json`으로 공시 목록 조회
  - DART viewer `source_url` 생성
- `FilingIngestionService`에서 장바구니 종목 기준 공시 metadata 수집 가능
- `filing_cache`에 다음 값 저장 확인
  - `symbol`
  - `filing_title`
  - `dart_receipt_no`
  - `source_url`
  - `disclosed_at`
- 같은 공시를 다시 수집하면 `dart_receipt_no` 기준 upsert 확인
- `docker-compose.yml`에 로컬 ChromaDB 서비스 정의 있음

아직 안 된 것:

- `--force`, `--reset` 재생성 시나리오 추가 검증
- 뉴스 담당자가 구현할 `news` collection과 최종 retrieval 통합
- LangGraph 토론 agent context 연결

이번 구현으로 완료된 것:

- DART `document.xml`로 공시 본문 가져오기
- ZIP 내부 XML/HTML 본문 추출 및 공백 정리
- HuggingFace local embedding 생성
- OpenAI embedding 선택 지원
- 로컬 ChromaDB wrapper 구현
- `filing_cache` row를 Chroma `filing` document로 변환
- `scripts/reindex_local_chroma.py` 구현
- Chroma similarity search 검증
- 공시 evidence retrieval 검증

## 구현 대상

이번 작업에서 구현할 파일:

```text
app/external/chroma_client.py
app/external/embedding.py
app/domain/evidence_indexing.py
app/domain/evidence_retrieval.py
scripts/reindex_local_chroma.py
scripts/validate_chroma_connection.py
scripts/validate_filing_evidence_retrieval.py
```

수정할 파일:

```text
app/external/dart.py
app/config.py
.env.example
requirements.txt
docker-compose.yml
memo/plans/vector-db-and-evidence-retrieval-plan.md
```

## 구현 완료 현황

### 1. ChromaDB 연결

구현 파일:

```text
app/external/chroma_client.py
```

완료 내용:

```text
CHROMA_URL 기준 HttpClient 연결
heartbeat 확인
collection 생성/조회
document upsert
document 조회
similarity query
id 기준 delete
symbol 기준 delete
collection count
기존 document id 확인
```

로컬 ChromaDB는 Docker Compose로 실행한다.

```bash
docker compose up -d chroma
```

### 2. Embedding wrapper

구현 파일:

```text
app/external/embedding.py
```

완료 내용:

```text
기본 provider = huggingface
기본 model = jhgan/ko-sroberta-multitask
OpenAI embedding 선택 지원
batch embedding 지원
query embedding 지원
빈 문자열 방어
OpenAI 사용 시 retry/backoff 적용
```

기본 설정:

```env
EMBEDDING_PROVIDER=huggingface
EMBEDDING_MODEL=jhgan/ko-sroberta-multitask
```

OpenAI를 쓰는 경우에만 다음 설정이 필요하다.

```env
EMBEDDING_PROVIDER=openai
OPENAI_API_KEY=...
EMBEDDING_MODEL=text-embedding-3-small
```

### 3. DART 공시 본문 추출

수정 파일:

```text
app/external/dart.py
```

완료 내용:

```text
fetch_document_xml(receipt_no)
extract_document_text(zip_bytes)
fetch_filing_text(receipt_no)
```

처리 방식:

```text
DART document.xml API 호출
ZIP 응답 해제
.html/.htm 우선 선택, 없으면 .xml 선택
여러 파일이 있으면 가장 큰 파일 선택
BeautifulSoup으로 태그 제거
줄 단위 공백 정리
본문이 너무 짧으면 indexing 실패로 처리
```

### 4. Filing indexing

구현 파일:

```text
app/domain/evidence_indexing.py
```

완료 내용:

```text
symbol 기준 filing_cache row 조회
이미 Chroma에 있는 document skip
force=true면 재조회/upsert
DART 본문 추출
document 생성
embedding 생성
filing collection upsert
indexed/skipped/failed/errors 결과 반환
```

PostgreSQL에는 본문을 저장하지 않는다.

```text
filing_cache.content = NULL 유지
filing_cache.summary = NULL 유지
```

### 5. Filing retrieval

구현 파일:

```text
app/domain/evidence_retrieval.py
```

완료 내용:

```text
query embedding 생성
Chroma filing collection 검색
where={"symbol": symbol} metadata filter 적용
source_id 기준 PostgreSQL FilingCache metadata 조회
EvidenceItem 반환
score = 1.0 - cosine_distance
```

반환 구조:

```text
source_id
source_type
symbol
title
source_url
text
score
```

### 6. 검증 스크립트

구현 파일:

```text
scripts/reindex_local_chroma.py
scripts/validate_chroma_connection.py
scripts/validate_filing_evidence_retrieval.py
```

검증 완료 명령:

```bash
python -m scripts.validate_chroma_connection
python -m scripts.validate_filing_evidence_retrieval --symbol 005930 --query "매출 영업이익 실적"
```

검증 결과:

```text
[OK] ChromaDB heartbeat
[OK] HuggingFace embedding 생성
[OK] Chroma test document upsert/query/delete
[REINDEX] source=filing symbol=005930 rows=10 indexed=10 skipped=0 failed=0
[OK] chroma filing count=10
[PASS] validate_filing_evidence_retrieval
```

## 데이터 저장 원칙

PostgreSQL `filing_cache`:

```text
metadata만 저장
content = NULL 유지
summary = NULL 유지
```

로컬 ChromaDB `filing` collection:

```text
공시 본문 저장
embedding 저장
metadata 저장
```

즉, 공시 본문은 PostgreSQL에 넣지 않는다.

## Chroma document 규칙

collection:

```text
filing
```

document id:

```text
filing:{filing_cache.id}
```

document:

```text
{filing_title}

{DART document.xml에서 추출한 공시 본문}
```

metadata:

```text
source_id = filing_cache.id
source_type = filing
symbol = filing_cache.symbol
dart_receipt_no = filing_cache.dart_receipt_no
filing_title = filing_cache.filing_title
source_url = filing_cache.source_url
published_at = filing_cache.disclosed_at ISO string
chunk_idx = 0
```

본문이 길어서 청킹할 경우:

```text
filing:{filing_cache.id}:chunk:{idx}
```

초기 구현은 단일 document 우선으로 한다.

## 구현 단계

### Phase 1. 설정값 추가

`app/config.py`에 추가:

```text
CHROMA_URL
CHROMA_TOKEN
EMBEDDING_PROVIDER
EMBEDDING_MODEL
```

`.env.example`에 추가:

```env
CHROMA_URL=http://localhost:8080
CHROMA_TOKEN=
EMBEDDING_PROVIDER=huggingface
EMBEDDING_MODEL=jhgan/ko-sroberta-multitask
```

### Phase 2. ChromaDB wrapper 구현

파일:

```text
app/external/chroma_client.py
```

구현 기능:

```text
heartbeat
get_or_create_collection
upsert_documents
get_documents
query
delete_by_ids
delete_by_symbol
```

검증 스크립트:

```text
scripts/validate_chroma_connection.py
```

검증 내용:

```text
1. 로컬 ChromaDB heartbeat 확인
2. filing collection 생성
3. 테스트 document upsert
4. 테스트 query
5. 테스트 document delete
```

### Phase 3. Embedding wrapper 구현

파일:

```text
app/external/embedding.py
```

구현 기능:

```text
embed_texts(texts: list[str])
embed_query(query: str)
batch embedding
HuggingFace local embedding
OpenAI embedding 선택 지원
retry/backoff
빈 문자열 방어
```

초기 모델:

```text
jhgan/ko-sroberta-multitask
```

주의:

```text
기본값은 HuggingFace local embedding이므로 OpenAI API key 없이 동작한다.
`EMBEDDING_PROVIDER=openai`로 바꾼 경우에만 `OPENAI_API_KEY`가 필요하다.
```

### Phase 4. DART 공시 본문 추출 구현

수정 파일:

```text
app/external/dart.py
```

추가 메서드:

```python
def fetch_document_xml(self, receipt_no: str) -> bytes:
    ...

def extract_document_text(self, document_xml_zip: bytes) -> str:
    ...

def fetch_filing_text(self, receipt_no: str) -> str:
    ...
```

처리 흐름:

```text
1. DART document.xml API 호출
2. ZIP 응답 열기
3. XML 파일 선택
4. XML/HTML tag 제거
5. 공백 정리
6. 텍스트 반환
```

skip 조건:

```text
DART API 응답 실패
ZIP 파일 아님
XML 파일 없음
본문이 비어 있음
본문 길이가 너무 짧음
```

### Phase 5. Evidence indexing 구현

파일:

```text
app/domain/evidence_indexing.py
```

구현 역할:

```text
filing_cache row 조회
DART 본문 추출
Chroma document 생성
embedding 생성
filing collection upsert
```

예상 인터페이스:

```python
class EvidenceIndexer:
    def index_filing_rows(self, rows, force: bool = False) -> IndexingResult:
        ...

    def reindex_symbol(self, symbol: str, force: bool = False) -> IndexingResult:
        ...

    def reset_symbol(self, symbol: str) -> None:
        ...
```

결과 객체:

```text
total
indexed
skipped
failed
errors
```

skip 조건:

```text
이미 Chroma에 document가 있음 + force=false
dart_receipt_no 없음
본문 추출 실패
본문 길이 너무 짧음
```

### Phase 6. 로컬 reindex 스크립트 구현

파일:

```text
scripts/reindex_local_chroma.py
```

명령:

```bash
python -m scripts.reindex_local_chroma --symbol 005930
python -m scripts.reindex_local_chroma --symbol 005930 --force
python -m scripts.reindex_local_chroma --symbol 005930 --reset
```

동작:

```text
1. 공용 PostgreSQL 연결
2. symbol 기준 filing_cache row 조회
3. 로컬 ChromaDB 연결 확인
4. reset 옵션이면 해당 symbol의 filing document 삭제
5. DART 본문 추출
6. embedding 생성
7. Chroma upsert
8. 처리 결과 출력
```

출력 예:

```text
[REINDEX] source=filing symbol=005930 rows=10 indexed=8 skipped=1 failed=1
[SKIP] filing:... already exists
[FAIL] receipt_no=202605... reason=document.xml empty
```

### Phase 7. Retrieval 구현

파일:

```text
app/domain/evidence_retrieval.py
```

구현 역할:

```text
query 생성
query embedding 생성
Chroma filing collection 검색
source_id 기준 PostgreSQL filing_cache metadata 조회
EvidenceItem 반환
```

예상 인터페이스:

```python
class EvidenceRetriever:
    def retrieve_filings(self, symbol: str, query: str, limit: int = 5):
        ...
```

반환:

```text
source_id
source_type
symbol
title
source_url
text
score
```

### Phase 8. 검증 스크립트 구현

파일:

```text
scripts/validate_filing_evidence_retrieval.py
```

검증 흐름:

```text
1. symbol=005930 기준 filing_cache row 존재 확인
2. reindex 실행
3. Chroma filing collection count 확인
4. "삼성전자 매출 영업이익" 같은 query로 검색
5. 검색 결과의 source_id로 PostgreSQL metadata 조회
6. source_url이 DART viewer URL인지 확인
```

## 실행 순서

로컬 ChromaDB 실행:

```bash
docker compose up -d chroma
```

Chroma 연결 확인:

```bash
python -m scripts.validate_chroma_connection
```

공시 reindex:

```bash
python -m scripts.reindex_local_chroma --symbol 005930
```

검색 검증:

```bash
python -m scripts.validate_filing_evidence_retrieval --symbol 005930
```

## 다른 뉴스 담당자와의 연결점

이 구현에서 공통으로 제공할 것:

```text
app/external/chroma_client.py
app/external/embedding.py
Chroma document 규칙
metadata key 규칙
```

뉴스 담당자는 같은 wrapper를 사용해서 `collection_name="news"`로 넣으면 된다.

```text
filing collection = 이 계획에서 구현
news collection = 뉴스 담당자가 같은 규칙으로 구현
```

## 팀에 공유해야 할 것

Git에 올려서 팀원이 같이 써야 하는 것:

```text
docker-compose.yml
.env.example
app/config.py
app/external/chroma_client.py
app/external/embedding.py
app/external/dart.py
app/domain/evidence_indexing.py
app/domain/evidence_retrieval.py
scripts/reindex_local_chroma.py
scripts/validate_chroma_connection.py
scripts/validate_filing_evidence_retrieval.py
memo/plans/dart-filing-local-chroma-implementation-plan.md
memo/plans/vector-db-and-evidence-retrieval-plan.md
memo/results/2026-05-22-dart-filing-local-chroma-implementation-result.md
```

공시 metadata 수집 코드가 아직 팀 브랜치에 없다면 함께 공유해야 하는 것:

```text
app/domain/filing_ingestion.py
app/repositories/filing_cache_repository.py
scripts/validate_dart_filing_ingestion.py
memo/plans/dart-filing-cache-ingestion-plan.md
memo/plans/dart-filing-content-ingestion-plan.md
memo/results/2026-05-21-dart-filing-ingestion-test-troubleshooting.md
```

팀 공통 실행 규칙:

```text
PostgreSQL = NCP 공용 DB
Redis = 각자 로컬 Docker
ChromaDB = 각자 로컬 Docker
```

팀 공통 환경 변수 예시:

```env
CHROMA_URL=http://localhost:8080
CHROMA_TOKEN=
EMBEDDING_PROVIDER=huggingface
EMBEDDING_MODEL=jhgan/ko-sroberta-multitask
```

뉴스 담당자와 맞춰야 하는 Chroma 규칙:

```text
ChromaDB 인스턴스는 개발자별로 하나만 띄운다.
공시는 collection_name="filing"에 저장한다.
뉴스는 collection_name="news"에 저장한다.
source별로 ChromaDB 서버를 따로 띄우지 않는다.
```

공통 metadata 필수 필드:

```text
source_id
source_type
symbol
source_url
published_at
chunk_idx
```

source별 title 필드:

```text
filing: filing_title
news: title
```

retrieval에서는 둘 다 `EvidenceItem.title`로 맞춘다.

## 공유하지 않아야 할 것

Git에 올리면 안 되는 것:

```text
.env
실제 OPENAI_API_KEY
실제 DART_API_KEY
실제 DATABASE_URL 비밀번호
로컬 ChromaDB volume 데이터
로컬 Redis volume 데이터
HuggingFace model cache
실행 로그 중 API key나 DB URL이 들어간 파일
```

로컬에만 있어야 하는 것:

```text
각자 PC의 ChromaDB 실제 vector/document 데이터
각자 PC의 Redis lock/counter/cooldown 상태
각자 PC의 sentence-transformers 다운로드 캐시
```

주의:

```text
ChromaDB에 들어간 공시 본문과 embedding은 재생성 가능한 개인 로컬 RAG 인덱스다.
따라서 ChromaDB volume 자체를 공유하지 않는다.
팀원은 공용 PostgreSQL의 filing_cache metadata를 기준으로 각자 reindex를 실행한다.
```

## 커밋 전 확인 사항

```bash
git status --short
python -m scripts.validate_chroma_connection
python -m scripts.validate_filing_evidence_retrieval --symbol 005930 --query "매출 영업이익 실적"
```

커밋에 포함할지 한 번 더 확인할 파일:

```text
requirements.txt
app/api/watchlist.py
app/domain/watchlist_service.py
scripts/validate_watchlist_api.py
scripts/validate_watchlist_flow.py
```

위 파일들은 DART 공시 metadata 수집/장바구니 연동까지 포함한 변경일 수 있다. 이번 커밋을 "DART filing local Chroma RAG"로 좁힐지, "DART filing ingestion + RAG"로 묶을지에 따라 포함 범위를 결정한다.

## 닫힘 기준

- [x] 로컬 ChromaDB heartbeat 성공
- [x] filing collection 생성 성공
- [x] HuggingFace embedding 생성 성공
- [x] DART `document.xml` 본문 추출 성공
- [x] `filing_cache.content`는 계속 `NULL`
- [x] `scripts/reindex_local_chroma.py --symbol 005930` 성공
- [x] Chroma `filing` collection에 document upsert 확인
- [ ] 같은 reindex 재실행 시 중복 document 없음
- [ ] `--force`로 재생성 가능
- [ ] `--reset`으로 symbol 단위 삭제 후 재생성 가능
- [x] retrieval query로 filing evidence 검색 가능
- [x] 검색 결과에서 PostgreSQL metadata와 Chroma document를 함께 조합 가능
