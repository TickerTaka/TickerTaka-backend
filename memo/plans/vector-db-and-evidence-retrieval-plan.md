# Vector DB (Local ChromaDB) + Evidence Retrieval 계획

## 목표

토론 단계에서 `filing_cache`를 중심으로 공시 본문을 의미 기반으로 검색해 evidence 후보를 만든다. `news_cache`는 다른 담당자가 수집/본문 처리하는 영역으로 두고, 이 계획의 1차 구현 범위에서는 공시 RAG 경로를 먼저 완성한다.

현재 프로젝트 전제는 **배포 없음**이다. 따라서 운영용 공용 ChromaDB 서버를 별도로 구축하지 않고, 다음 구조를 사용한다.

```text
공용 NCP PostgreSQL
= 팀 공용 메타데이터 truth

개발자별 로컬 Redis
= lock / cooldown / 임시 상태

개발자별 로컬 ChromaDB
= 개인 RAG 본문/embedding 인덱스
```

핵심 원칙:

- PostgreSQL은 팀 공용 메타데이터 truth다.
- `filing_cache.content`는 기본적으로 `NULL`로 둔다.
- `news_cache` 수집/본문 처리는 이 계획의 1차 구현 범위 밖이다.
- 본문 텍스트와 embedding은 각자 로컬 ChromaDB에 저장한다.
- ChromaDB는 운영 공용 SOT가 아니라 **개발자 개인 RAG 인덱스**다.
- 같은 PostgreSQL row를 기준으로 reindex하면 각자 로컬 Chroma를 재생성할 수 있어야 한다.
- Redis는 개인 로컬 인스턴스를 사용한다. 팀 전체 중복 실행 방지는 약하지만, PostgreSQL unique/upsert로 최종 중복 row를 방어한다.

## 현재 상태

확인 완료:

- NCP PostgreSQL `stock_debate` DB 사용 중
- DART 공시 metadata 적재 성공
- `filing_cache`에 `symbol`, `filing_title`, `dart_receipt_no`, `source_url`, `disclosed_at` 저장 성공
- 같은 DART 적재 재실행 시 `dart_receipt_no` 기준 upsert 확인
- `docker-compose.yml`에 로컬 Redis / ChromaDB 서비스 정의 있음
- `.env.example`에 `REDIS_URL`, `CHROMA_URL` 정의 있음

현재 검증 결과:

```text
DART 1차 적재: fetched=10 inserted=10 updated=0
DART 2차 적재: fetched=10 inserted=0 updated=10
watchlist API smoke test: PASS
```

## 아키텍처

```text
                           공용 NCP PostgreSQL
                           ─────────────────
                           app_user
                           watchlist
                           ticker_metadata
                           news_cache      (다른 담당 영역)
                           filing_cache    (content = NULL)
                           price_cache
                           financial_cache
                                      ▲
                                      │ metadata/source_url/receipt_no
                                      │
개발자 A PC                          │                           개발자 B PC
───────────                          │                           ───────────
FastAPI ──────────────── DATABASE_URL┘                           FastAPI
Redis(localhost:6379)                                            Redis(localhost:6379)
Chroma(localhost:8080)                                           Chroma(localhost:8080)
  collection: news                                                 collection: news
  collection: filing                                               collection: filing
```

역할 분리:

| 저장소 | 위치 | 역할 |
| --- | --- | --- |
| PostgreSQL | NCP 공용 | watchlist, filing metadata, source_url, 중복 방지 |
| Redis | 각자 로컬 | sync lock, cooldown, 일일 API counter, 임시 상태 |
| ChromaDB | 각자 로컬 | RAG용 document text + embedding + metadata |

## 데이터 흐름

전체 흐름은 두 단계로 나눈다.

```text
1차 수집 단계
= 장바구니 종목 기준으로 DART 공시를 가져와 PostgreSQL filing_cache 테이블에 metadata 저장

2차 RAG 인덱싱 단계
= PostgreSQL filing_cache row를 기준으로 공시 본문을 가져와 로컬 ChromaDB에 document + embedding 저장
```

즉, 장바구니 추가는 공시 수집을 트리거한다. Chroma 인덱싱은 그 다음 단계다. 뉴스 수집/뉴스 본문 인덱싱은 다른 담당 영역으로 분리한다.

### 1. 장바구니 추가

```text
POST /api/watchlists
-> 공용 PostgreSQL watchlist row 생성
-> background task로 DART 공시 sync 실행
```

현재 구현 기준:

```python
background_tasks.add_task(sync_watchlist_filings, watchlist.symbol)
```

참고:

```text
sync_watchlist_news도 현재 코드에 있을 수 있지만, 뉴스 수집/본문 처리 구현은 이 계획의 담당 범위에서 제외한다.
```

### 2. DART 공시 수집 및 PostgreSQL metadata 적재

DART 공시 sync는 공용 PostgreSQL에 metadata를 저장한다.

공시 수집:

```text
filing_cache:
  symbol
  filing_title
  filing_type
  dart_receipt_no
  source_url
  disclosed_at
  retrieved_at
  ttl_until
  content = NULL
  summary = NULL
```

공시 수집 세부 흐름:

```text
1. watchlist.symbol을 받음
2. DART corpCode.xml에서 stock_code -> corp_code 매핑
3. DART list.json으로 최근 공시 목록 조회
4. filing_cache에 metadata upsert
5. source_url은 DART viewer URL로 저장
6. content/summary는 저장하지 않음
```

공시 저장 예:

```text
symbol = 005930
filing_title = 분기보고서 (2026.03)
dart_receipt_no = 20260515002181
source_url = https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260515002181
content = NULL
summary = NULL
```

이 단계의 목적:

```text
대시보드 목록 표시
DART 원문 페이지 리다이렉션
중복 수집 방지
RAG 인덱싱 대상 row 확보
```

### 3. 개인 로컬 Chroma RAG 인덱싱

각 개발자는 자기 PC에서 reindex 스크립트를 실행한다.

```bash
conda run -n tickertaka311 python -m scripts.reindex_local_chroma --symbol 005930
```

동작:

```text
1. 공용 PostgreSQL에서 filing_cache row 조회
2. 로컬 ChromaDB에 해당 id가 있는지 확인
3. filing_cache는 dart_receipt_no로 DART document.xml 조회
4. 본문 텍스트 추출
5. embedding 생성
6. 로컬 ChromaDB에 upsert
7. PostgreSQL content 컬럼은 계속 NULL 유지
```

공시 기준 Chroma 저장:

```text
collection = filing
id = filing:{filing_cache.id}
document = filing_title + "\n\n" + DART document.xml에서 추출한 본문
metadata.source_id = filing_cache.id
metadata.dart_receipt_no = filing_cache.dart_receipt_no
metadata.source_url = filing_cache.source_url
metadata.symbol = filing_cache.symbol
```

정리:

```text
PostgreSQL filing_cache
= 공시 껍데기, 링크, 중복 방지, 대시보드용

Local Chroma filing
= 공시 본문, embedding, RAG 검색용
```

## ChromaDB Collection 구조

collection은 데이터 타입별로 분리한다.

```text
news       # 다른 담당 영역, 1차 구현 제외
filing
financial  # 후속
```

symbol별 collection은 만들지 않는다. symbol은 metadata filter로 처리한다.

### document id 정책

초기에는 단일 청크를 우선한다.

```text
filing:{filing_cache.id}
```

본문이 너무 길어 분할이 필요하면 다음 형식을 사용한다.

```text
filing:{filing_cache.id}:chunk:{idx}
```

### filing collection 예시

```json
{
  "id": "filing:05997754-f761-4272-b74b-a6bab3adc0f7",
  "document": "분기보고서 (2026.03)\n\n...DART document.xml에서 추출한 본문...",
  "metadata": {
    "symbol": "005930",
    "source_id": "05997754-f761-4272-b74b-a6bab3adc0f7",
    "source_type": "filing",
    "dart_receipt_no": "20260515002181",
    "filing_title": "분기보고서 (2026.03)",
    "source_url": "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260515002181",
    "published_at": "2026-05-15T00:00:00+09:00",
    "chunk_idx": 0
  }
}
```

## 임베딩 대상

| Source | PostgreSQL | Chroma document |
| --- | --- | --- |
| News | 다른 담당 영역 | 1차 구현 제외 |
| Filing | `filing_cache` metadata, `content=NULL` | `filing_title + DART 본문 텍스트` |
| Financial | 후속 | 핵심 수치 자연어 직렬화 |
| Price/Technical | SQL 조회 | 임베딩하지 않음 |

본문 소스:

- Filing: DART `document.xml` API 결과

## 청킹 정책

초기 구현은 단일 document를 우선한다.

```text
본문 1만자 이하 -> 단일 document
본문 1만자 초과 -> 약 500 토큰 청크 + 50 토큰 overlap
```

단일 document를 우선하는 이유:

- `cache_row.id`와 Chroma document id를 1:1로 맞추기 쉽다.
- cleanup / lookup / evidence display가 단순하다.
- 초기 데이터 규모와 시연 목적에는 충분하다.

## 임베딩 모델

초기 선택:

```text
HuggingFace jhgan/ko-sroberta-multitask
```

이유:

- 로컬에서 동작해 API key와 비용이 필요 없다.
- 한국어 문장 임베딩에 맞춰져 있다.
- 개발자별 로컬 ChromaDB 구조와 잘 맞는다.

환경 변수:

```env
EMBEDDING_PROVIDER=huggingface
EMBEDDING_MODEL=jhgan/ko-sroberta-multitask
```

후속 대안:

- BGE-M3: self-host 가능, 다국어 강함
- ko-sroberta-multitask: 한국어 단문에 강함
- OpenAI text-embedding-3-small: 품질/안정성은 좋지만 API key와 비용 필요
- Qdrant/pgvector 이전: 운영 규모가 커질 때 검토

## Redis 사용 위치

Redis는 ChromaDB와 경쟁하는 저장소가 아니다.

Redis 역할:

```text
sync lock
cooldown
일일 API 호출량 counter
토론 임시 상태
rate limit
intraday quote cache
```

현재 실제 사용 중:

- `NewsIngestionService`
  - `news-sync:lock:{symbol}`
  - `news-sync:last-sync:{symbol}`
  - `naver-api-count:{YYYY-MM-DD}`
- `NewsCacheScheduler`
  - sweep last-run 기록

후속 적용 예정:

- `filing-sync:lock:{symbol}`
- `filing-sync:last-sync:{symbol}`
- `dart-api-count:{YYYY-MM-DD}`

로컬 개발에서는 각자 Redis를 쓰므로 팀 전체 lock은 보장하지 않는다.

```text
A PC Redis lock != B PC Redis lock
```

따라서 동시에 같은 종목을 수집할 수는 있다. 대신 PostgreSQL의 unique/upsert로 최종 row 중복은 방어한다.

## Chroma 인덱싱 시점

현재 전제에서는 공용 ChromaDB가 없으므로, cache sync 직후 Chroma upsert를 항상 강제하지 않는다.

초기 구현 방식:

```text
수동 reindex 스크립트 우선
```

예시:

```bash
python -m scripts.reindex_local_chroma --symbol 005930
python -m scripts.reindex_local_chroma --source filing --symbol 005930
```

후속으로 가능:

```text
watchlist background sync 종료 후 로컬 Chroma upsert
```

단, 이 경우에도 각자 PC에서 실행 중인 API 프로세스의 로컬 Chroma에만 저장된다.

## Retrieval 흐름

토론 시점에는 로컬 ChromaDB에서 evidence를 검색한다.

```text
1. 사용자/종목/카테고리 입력
2. 카테고리별 query 생성
3. 로컬 Chroma filing collection 검색
4. metadata filter로 symbol 제한
5. Chroma 결과의 source_id로 공용 PostgreSQL metadata 조회
6. LLM에는 Chroma document text 제공
7. UI에는 PostgreSQL title/source_url 제공
```

카테고리별 검색 후보:

```text
technical: price/technical SQL 보조, news evidence는 다른 담당 구현 후 연동
financial: filing 중심 + financial_cache SQL 보조
market: news evidence는 다른 담당 구현 후 연동
```

## 동기화 / 재생성 정책

각자 로컬 Chroma는 공용 SOT가 아니다. 언제든 재생성 가능해야 한다.

재생성 기준:

```text
공용 PostgreSQL cache row
+ source_url / dart_receipt_no
+ 현재 본문 추출 코드
+ 현재 embedding model
```

필요 스크립트:

```bash
python -m scripts.reindex_local_chroma --symbol 005930
python -m scripts.reindex_local_chroma --reset --symbol 005930
```

삭제 정책:

- PostgreSQL row가 삭제되었는데 로컬 Chroma에는 남아 있을 수 있다.
- 로컬 개발 환경에서는 큰 문제가 아니다.
- `--reset` 옵션으로 collection 또는 symbol 단위 삭제 후 재생성한다.

## 로컬 개발 실행 방식

각 개발자 PC에서 실행:

```bash
docker compose up -d redis chroma
```

`.env` 또는 `.env.local`:

```env
DATABASE_URL=postgresql://stock_user:tickertaka@101.79.19.53:5432/stock_debate
REDIS_URL=redis://localhost:6379/0
CHROMA_URL=http://localhost:8080
CHROMA_TOKEN=
```

검증:

```bash
python -m scripts.validate_redis_integration
python -m scripts.validate_chroma_connection
python -m scripts.reindex_local_chroma --symbol 005930
python -m scripts.validate_evidence_retrieval --symbol 005930
```

## 구현 상세 설계

이번 구현의 핵심은 **공용 PostgreSQL에는 metadata만 유지하고, 본문/embedding은 로컬 ChromaDB에 재생성 가능하게 저장**하는 것이다.

구현은 다음 순서로 진행한다.

```text
1. 로컬 ChromaDB 연결 wrapper
2. embedding 생성 wrapper
3. DART 공시 본문 추출
4. PostgreSQL cache row -> Chroma document 변환
5. 로컬 reindex 스크립트
6. 토론 evidence retrieval 함수
```

### 1. ChromaDB wrapper

파일:

```text
app/external/chroma_client.py
```

역할:

```text
CHROMA_URL을 읽어서 로컬 ChromaDB에 연결
collection 생성/조회
document upsert
document 조회
similarity query
symbol/source 단위 delete
heartbeat 검증
```

예상 인터페이스:

```python
class ChromaClient:
    def heartbeat(self) -> bool: ...
    def get_or_create_collection(self, name: str): ...
    def upsert_documents(self, collection_name: str, documents: list[ChromaDocument]) -> None: ...
    def get_documents(self, collection_name: str, ids: list[str]) -> list[ChromaDocument]: ...
    def query(
        self,
        collection_name: str,
        query_embedding: list[float],
        where: dict,
        limit: int,
    ) -> list[ChromaSearchResult]: ...
    def delete_by_ids(self, collection_name: str, ids: list[str]) -> None: ...
    def delete_by_symbol(self, collection_name: str, symbol: str) -> None: ...
```

주의:

```text
ChromaDB는 각자 로컬 Docker이므로 실패해도 PostgreSQL metadata 적재를 망가뜨리면 안 된다.
reindex 스크립트에서는 실패를 명확히 출력하고 종료한다.
watchlist background task에 붙이는 경우에는 fail-soft 처리한다.
```

### 2. Embedding wrapper

파일:

```text
app/external/embedding.py
```

역할:

```text
EMBEDDING_PROVIDER / EMBEDDING_MODEL 읽기
텍스트 batch embedding 생성
HuggingFace local embedding 기본 사용
OpenAI embedding 선택 지원
429 / 5xx retry
긴 본문 truncate 또는 chunking 전처리
```

예상 인터페이스:

```python
class EmbeddingClient:
    def embed_texts(self, texts: list[str]) -> list[list[float]]: ...
    def embed_query(self, query: str) -> list[float]: ...
```

초기 모델:

```text
jhgan/ko-sroberta-multitask
```

환경 변수:

```env
EMBEDDING_PROVIDER=huggingface
EMBEDDING_MODEL=jhgan/ko-sroberta-multitask
```

### 3. DART 공시 본문 추출

파일:

```text
app/external/dart.py
```

현재 상태:

```text
corpCode.xml 조회 가능
list.json으로 공시 목록 조회 가능
DART viewer source_url 생성 가능
document.xml 본문 조회는 아직 없음
```

추가할 기능:

```python
class DartClient:
    def fetch_document_xml(self, receipt_no: str) -> bytes: ...
    def extract_document_text(self, document_xml_zip: bytes) -> str: ...
    def fetch_filing_text(self, receipt_no: str) -> str: ...
```

처리 흐름:

```text
1. dart_receipt_no로 DART document.xml API 호출
2. 응답 ZIP 파일 열기
3. 내부 XML 파일 읽기
4. XML/HTML tag 제거
5. 공백 정리
6. 너무 짧거나 비어 있으면 indexing skip
```

저장 위치:

```text
PostgreSQL filing_cache.content에는 저장하지 않음
로컬 ChromaDB document에만 저장
```

### 4. Evidence indexing domain

파일:

```text
app/domain/evidence_indexing.py
```

역할:

```text
PostgreSQL cache row를 Chroma document 형식으로 변환
본문 추출
청킹
embedding 생성
Chroma upsert
```

예상 인터페이스:

```python
class EvidenceIndexer:
    def index_filing_rows(self, rows: list[FilingCache]) -> IndexingResult: ...
    def reindex_symbol(self, symbol: str, source: str | None = None, force: bool = False) -> IndexingResult: ...
    def reset_symbol(self, symbol: str, source: str | None = None) -> None: ...
```

공시 document 생성 규칙:

```text
collection = filing
document_id = filing:{filing_cache.id}
document = filing_title + "\n\n" + extracted_dart_text
metadata.source_id = filing_cache.id
metadata.source_type = filing
metadata.symbol = filing_cache.symbol
metadata.dart_receipt_no = filing_cache.dart_receipt_no
metadata.filing_title = filing_cache.filing_title
metadata.source_url = filing_cache.source_url
metadata.published_at = disclosed_at ISO string
metadata.chunk_idx = 0
```

skip 조건:

```text
본문 추출 실패
본문 길이 너무 짧음
source_url / dart_receipt_no 없음
이미 같은 document_id가 있고 force=false
```

### 5. 로컬 reindex 스크립트

파일:

```text
scripts/reindex_local_chroma.py
```

목적:

```text
각 개발자가 공용 PostgreSQL metadata를 기준으로 자기 로컬 ChromaDB를 재생성한다.
```

명령 예시:

```bash
python -m scripts.reindex_local_chroma --symbol 005930
python -m scripts.reindex_local_chroma --symbol 005930 --source filing
python -m scripts.reindex_local_chroma --symbol 005930 --force
python -m scripts.reindex_local_chroma --symbol 005930 --reset
```

동작:

```text
1. DATABASE_URL로 공용 PostgreSQL 연결
2. symbol 기준 filing_cache row 조회
3. 로컬 ChromaDB heartbeat 확인
4. DART 공시 본문 추출
5. embedding 생성
6. Chroma upsert
7. inserted / skipped / failed 카운트 출력
```

출력 예:

```text
[REINDEX] source=filing symbol=005930 rows=10 indexed=8 skipped=1 failed=1
[SKIP] filing:... already exists
[FAIL] receipt_no=... document.xml empty
```

### 6. Retrieval domain

파일:

```text
app/domain/evidence_retrieval.py
```

역할:

```text
토론 agent가 사용할 evidence 후보를 로컬 ChromaDB에서 검색하고,
PostgreSQL metadata를 붙여서 반환한다.
```

예상 인터페이스:

```python
class EvidenceRetriever:
    def retrieve(
        self,
        symbol: str,
        category: str,
        limit: int = 5,
    ) -> list[EvidenceItem]: ...
```

반환 구조:

```python
class EvidenceItem:
    source_id: str
    source_type: str
    symbol: str
    title: str
    source_url: str
    text: str
    score: float
```

검색 흐름:

```text
1. category에 맞는 query 생성
2. query embedding 생성
3. filing collection 검색
4. where={"symbol": symbol} filter 적용
5. Chroma result의 source_id로 PostgreSQL metadata 조회
6. LLM에는 text 전달
7. UI에는 title/source_url 전달
```

카테고리별 우선순위:

```text
technical = price/technical SQL 보조, news는 다른 담당 구현 후 연동
financial = filing 우선 + financial_cache SQL 보조
market = news는 다른 담당 구현 후 연동
```

## 구현 순서

1차 구현 범위:

```text
Chroma wrapper
Embedding wrapper
DART filing 본문 추출
filing 전용 reindex
filing retrieval smoke test
```

2차 구현 범위:

```text
뉴스 담당 구현물과 filing retrieval 연동
category별 source mix
LangGraph agent context 연결
```

3차 구현 범위:

```text
watchlist background sync 종료 후 선택적 로컬 Chroma upsert
reset/reconcile 스크립트 보강
검색 품질 튜닝
```

## 뉴스 담당자에게 요청할 내용

뉴스는 이 계획의 1차 구현 범위가 아니지만, 최종 RAG 검색에서는 `filing` collection과 `news` collection을 함께 조회해야 한다. 따라서 뉴스 담당자는 아래 인터페이스와 규칙을 맞춰 구현한다.

### 1. 같은 로컬 ChromaDB를 사용

```text
ChromaDB 서버를 source별로 따로 띄우지 않는다.
개발자별 로컬 ChromaDB 인스턴스 하나를 사용한다.
뉴스는 같은 ChromaDB 안의 news collection에 저장한다.
```

구조:

```text
localhost:8080 ChromaDB
├─ collection: filing   # 공시 담당 구현
└─ collection: news     # 뉴스 담당 구현
```

### 2. 공통 wrapper 사용

뉴스 담당자는 별도 Chroma 연결 코드를 새로 만들지 않고, 공통 wrapper를 사용한다.

공통 파일:

```text
app/external/chroma_client.py
app/external/embedding.py
```

사용 형태:

```python
chroma.upsert_documents(
    collection_name="news",
    documents=news_documents,
)
```

### 3. news collection document 규칙

뉴스 document id:

```text
news:{news_cache.id}
```

청킹 시:

```text
news:{news_cache.id}:chunk:{idx}
```

document 본문:

```text
news_cache.title + "\n\n" + 뉴스 본문 텍스트
```

metadata 필수 필드:

```text
source_id = news_cache.id
source_type = news
symbol = news_cache.symbol
title = news_cache.title
source_url = news_cache.source_url
published_at = news_cache.published_at ISO string
chunk_idx = 0
```

예시:

```json
{
  "id": "news:2ad25a4d-8c8a-4fd8-8e52-3f4b0b0f2d10",
  "document": "삼성전자, 반도체 실적 개선 기대\n\n...뉴스 본문...",
  "metadata": {
    "source_id": "2ad25a4d-8c8a-4fd8-8e52-3f4b0b0f2d10",
    "source_type": "news",
    "symbol": "005930",
    "title": "삼성전자, 반도체 실적 개선 기대",
    "source_url": "https://...",
    "published_at": "2026-05-22T09:00:00+09:00",
    "chunk_idx": 0
  }
}
```

### 4. 뉴스 담당자가 제공해야 할 함수

최소 제공 함수:

```python
def index_news_rows(rows: list[NewsCache], force: bool = False) -> IndexingResult:
    ...
```

또는 서비스 형태:

```python
class NewsEvidenceIndexer:
    def index_news_rows(self, rows: list[NewsCache], force: bool = False) -> IndexingResult: ...
    def reindex_symbol(self, symbol: str, force: bool = False) -> IndexingResult: ...
```

필요 동작:

```text
1. news_cache row 조회
2. source_url 기준 뉴스 본문 추출
3. title + 본문으로 document 생성
4. embedding 생성
5. collection_name="news"로 Chroma upsert
6. 이미 같은 document_id가 있으면 force=false일 때 skip
```

### 5. Retrieval 연동을 위한 반환 계약

최종 retrieval에서 `filing`과 `news` 결과를 합치려면 source metadata key가 맞아야 한다.

반드시 맞출 필드:

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
news: title
filing: filing_title
```

retrieval에서 통일할 때는 둘 다 `EvidenceItem.title`로 매핑한다.

### 6. 뉴스 담당자에게 전달할 요약

```text
ChromaDB는 따로 만들지 말고 같은 로컬 ChromaDB를 사용해주세요.
collection_name만 news로 넣으면 됩니다.
document_id는 news:{news_cache.id} 형식으로 맞춰주세요.
metadata에는 source_id, source_type, symbol, source_url, published_at, chunk_idx를 반드시 넣어주세요.
embedding wrapper와 Chroma wrapper는 공통 코드를 사용해주세요.
뉴스 본문 추출과 news_cache -> news document 변환은 뉴스 담당 쪽에서 구현해주세요.
```

## 구현 단계

### Phase 1. ChromaDB wrapper + embedding wrapper

파일:

- `app/external/chroma_client.py`
- `app/external/embedding.py`

기능:

- Chroma heartbeat
- collection 생성/조회
- upsert
- query
- get by id
- delete
- HuggingFace/OpenAI embedding batch 호출
- retry/backoff

검증:

```bash
python -m scripts.validate_chroma_connection
```

### Phase 2. 본문 추출 adapter

News:

- `source_url`에서 본문 스크래핑
- 기존 `article_scraper` 재사용

Filing:

- DART `document.xml` 호출
- ZIP 내부 XML 파싱
- plain text 추출

파일:

- `app/external/dart.py`
- `app/domain/evidence_indexing.py`

### Phase 3. 로컬 Chroma reindex 스크립트

파일:

- `scripts/reindex_local_chroma.py`

기능:

- PostgreSQL에서 cache row 조회
- Chroma에 이미 있는 id는 skip
- `--force`면 재조회/upsert
- `--reset`이면 symbol/source 단위 삭제 후 재생성
- News/Filing source 선택 가능

### Phase 4. Retrieval API / domain 함수

파일:

- `app/domain/evidence_retrieval.py`
- `scripts/validate_evidence_retrieval.py`

기능:

- symbol/category 기준 query 생성
- Chroma similarity search
- source quota/fallback
- PostgreSQL metadata lookup
- LLM context용 evidence text 반환

### Phase 5. 정리/검증

검증 항목:

- 로컬 Chroma에 filing document upsert
- `symbol=005930` metadata filter 검색
- 검색 결과의 `source_url`이 DART viewer URL인지 확인
- 같은 PostgreSQL row 기준 reindex 재실행 시 중복 document가 생기지 않음
- `--reset` 후 재생성 가능

## FAISS / pgvector / Chroma 비교 결론

| 선택지 | 판단 |
| --- | --- |
| FAISS | 벡터 검색은 빠르지만 metadata/document/persistence/delete 동기화를 직접 구현해야 해서 현재 목적에는 과함 |
| pgvector | PostgreSQL 하나로 단순하지만, 현재는 PG에 본문을 두지 않고 개인별 RAG 인덱스를 쓰는 구조라 우선순위 낮음 |
| ChromaDB | document + embedding + metadata를 같이 다룰 수 있어 RAG 개발에 가장 편함 |
| Qdrant | 운영/성능은 좋지만 배포 없는 현재 단계에서는 인프라가 무거움 |

결론:

```text
배포 없는 현재 단계:
공용 PostgreSQL + 개인 로컬 ChromaDB + 개인 로컬 Redis

운영 배포가 필요해지는 후속 단계:
공용 ChromaDB 또는 Qdrant 서버로 이전 검토
```

## 닫힘 기준

- [ ] 로컬 ChromaDB heartbeat 검증
- [ ] HuggingFace embedding wrapper 검증
- [ ] DART filing 본문 추출 검증
- [ ] `filing_cache.content`는 계속 `NULL` 유지
- [ ] filing document가 로컬 Chroma `filing` collection에 upsert됨
- [ ] `symbol` metadata filter 검색 가능
- [ ] 검색 결과에서 PostgreSQL metadata와 Chroma document를 함께 조합 가능
- [ ] `scripts/reindex_local_chroma.py`로 개인 로컬 Chroma를 재생성 가능
