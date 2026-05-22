# DART Filing Local Chroma 구현 결과 보고서

## 작업 요약

이번 작업에서는 DART 공시 metadata를 기준으로 공시 본문을 가져와 로컬 ChromaDB에 RAG 검색용 document + embedding으로 저장하는 파이프라인을 구현했다.

구현 범위:

```text
공용 NCP PostgreSQL filing_cache
-> dart_receipt_no 기준 DART document.xml 본문 조회
-> 본문 텍스트 추출
-> HuggingFace embedding 생성
-> 로컬 ChromaDB filing collection upsert
-> Chroma similarity search
-> PostgreSQL metadata와 조합해 EvidenceItem 반환
```

뉴스 본문 수집/뉴스 Chroma 인덱싱은 이번 작업 범위에서 제외했다. 뉴스 담당자는 같은 Chroma wrapper를 사용해 `news` collection에 넣는 방식으로 후속 연동한다.

## 최종 구조

```text
공용 NCP PostgreSQL
  filing_cache
    id
    symbol
    filing_title
    dart_receipt_no
    source_url
    disclosed_at
    content = NULL
    summary = NULL

개발자별 로컬 ChromaDB
  collection: filing
    id = filing:{filing_cache.id}
    document = filing_title + "\n\n" + DART document.xml 본문
    embedding = HuggingFace embedding
    metadata = source_id, symbol, dart_receipt_no, source_url ...
```

PostgreSQL은 metadata truth이고, 본문/embedding은 각 개발자의 로컬 ChromaDB에 저장한다.

## 구현 파일

### 신규 파일

```text
app/external/chroma_client.py
app/external/embedding.py
app/domain/evidence_indexing.py
app/domain/evidence_retrieval.py
scripts/reindex_local_chroma.py
scripts/validate_chroma_connection.py
scripts/validate_filing_evidence_retrieval.py
memo/plans/dart-filing-local-chroma-implementation-plan.md
```

### 수정 파일

```text
app/external/dart.py
app/config.py
.env.example
docker-compose.yml
memo/plans/vector-db-and-evidence-retrieval-plan.md
```

## 구현 내용

### 1. ChromaDB wrapper

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
count
get_existing_ids
```

특징:

- `CHROMA_URL`을 읽어 `chromadb.HttpClient` 생성
- token이 있으면 Authorization Bearer header 전달
- collection 생성 시 cosine space 사용
- `get_existing_ids`로 이미 인덱싱된 document skip 가능

### 2. Embedding wrapper

파일:

```text
app/external/embedding.py
```

구현 기능:

```text
HuggingFace local embedding
OpenAI embedding 선택 지원
batch embedding
query embedding
빈 문자열 방어
OpenAI 사용 시 retry/backoff
```

기본 설정:

```env
EMBEDDING_PROVIDER=huggingface
EMBEDDING_MODEL=jhgan/ko-sroberta-multitask
```

HuggingFace 기본값을 사용하므로 `OPENAI_API_KEY` 없이도 embedding 생성이 가능하다.

OpenAI를 쓰고 싶으면 다음처럼 바꾸면 된다.

```env
EMBEDDING_PROVIDER=openai
EMBEDDING_MODEL=text-embedding-3-small
OPENAI_API_KEY=...
```

### 3. DART 공시 본문 추출

파일:

```text
app/external/dart.py
```

추가 메서드:

```python
fetch_document_xml(receipt_no)
extract_document_text(zip_bytes)
fetch_filing_text(receipt_no)
```

동작:

```text
1. DART document.xml API 호출
2. ZIP 응답 열기
3. 내부 .html/.htm 파일 우선 선택, 없으면 .xml 선택
4. 여러 파일 중 file_size가 가장 큰 파일 선택
5. BeautifulSoup으로 텍스트 추출
6. 줄 단위 strip + 빈 줄 제거
7. 본문이 너무 짧으면 DartApiError 발생
```

### 4. Evidence indexing

파일:

```text
app/domain/evidence_indexing.py
```

구현 클래스:

```python
EvidenceIndexer
IndexingResult
```

주요 함수:

```text
index_filing_rows(rows, force=False)
reindex_symbol(symbol, force=False)
reset_symbol(symbol)
```

공시 document 규칙:

```text
collection = filing
document_id = filing:{filing_cache.id}
document = filing_title + "\n\n" + extracted_dart_text
```

metadata:

```text
source_id
source_type = filing
symbol
dart_receipt_no
filing_title
source_url
published_at
chunk_idx = 0
```

skip / fail 처리:

```text
이미 Chroma에 존재하고 force=false -> skipped
dart_receipt_no 없음 -> skipped
DART 본문 조회 실패 -> failed
본문 길이 부족 -> failed
embedding/upsert 실패 -> failed
```

### 5. Evidence retrieval

파일:

```text
app/domain/evidence_retrieval.py
```

구현 클래스:

```python
EvidenceRetriever
EvidenceItem
```

주요 함수:

```text
retrieve_filings(symbol, query, limit=5)
```

검색 흐름:

```text
1. query embedding 생성
2. Chroma filing collection query
3. where={"symbol": symbol} filter 적용
4. Chroma metadata의 source_id로 PostgreSQL filing_cache 조회
5. title/source_url 보완
6. EvidenceItem 반환
```

score:

```text
score = 1.0 - cosine_distance
```

### 6. CLI / 검증 스크립트

파일:

```text
scripts/reindex_local_chroma.py
```

사용법:

```bash
conda run -n tickertaka311 python -m scripts.reindex_local_chroma --symbol 005930
conda run -n tickertaka311 python -m scripts.reindex_local_chroma --symbol 005930 --force
conda run -n tickertaka311 python -m scripts.reindex_local_chroma --symbol 005930 --reset
```

파일:

```text
scripts/validate_chroma_connection.py
```

검증 내용:

```text
Chroma heartbeat
embedding 생성
test collection upsert
query
delete
```

파일:

```text
scripts/validate_filing_evidence_retrieval.py
```

검증 내용:

```text
filing_cache row 존재 확인
reindex 실행
Chroma filing count 확인
query 검색
검색 결과 source_url DART viewer URL 확인
```

## Docker 변경

`docker-compose.yml`의 ChromaDB 이미지를 고정했다.

기존:

```yaml
image: chromadb/chroma:latest
command: ["run", "--host", "0.0.0.0", "--port", "8080"]
```

변경:

```yaml
image: chromadb/chroma:0.5.23
command: ["--host", "0.0.0.0", "--port", "8080"]
```

변경 이유:

```text
Python client chromadb 버전이 0.5.23인데 Docker image latest를 쓰면
client/server 응답 스키마가 맞지 않아 collection 생성 중 KeyError('_type') 발생.
따라서 로컬 개발 재현성을 위해 서버 이미지를 0.5.23으로 고정.
```

## 테스트 결과

### 1. Python 문법 체크

명령:

```bash
PYTHONPYCACHEPREFIX=/private/tmp/codex_pycache python3 -m compileall \
  app/external/chroma_client.py \
  app/external/embedding.py \
  app/external/dart.py \
  app/domain/evidence_indexing.py \
  app/domain/evidence_retrieval.py \
  scripts/reindex_local_chroma.py \
  scripts/validate_chroma_connection.py \
  scripts/validate_filing_evidence_retrieval.py
```

결과:

```text
PASS
```

### 2. import 검증

명령:

```bash
conda run -n tickertaka311 python -c "
from app.external.chroma_client import ChromaClient
from app.external.embedding import EmbeddingClient
from app.domain.evidence_indexing import EvidenceIndexer
from app.domain.evidence_retrieval import EvidenceRetriever
print('imports ok')
"
```

결과:

```text
imports ok
PASS
```

### 3. HuggingFace embedding 생성 검증

명령:

```bash
conda run -n tickertaka311 python -c "
from app.external.embedding import EmbeddingClient
e = EmbeddingClient(provider='huggingface')
v = e.embed_texts(['삼성전자 분기보고서 매출 영업이익'])[0]
print(len(v))
"
```

결과:

```text
768
PASS
```

의미:

```text
jhgan/ko-sroberta-multitask 모델 기준 768차원 embedding 생성 성공
```

### 4. Chroma wrapper 수동 검증

검증 내용:

```text
manual_test collection 생성
document upsert
count 확인
existing id 확인
query 확인
delete 확인
```

결과:

```text
count = 1
existing_ids = {'a'}
query result = [['a']]
count after delete = 0
PASS
```

### 5. Chroma 연결 검증 스크립트

명령:

```bash
conda run -n tickertaka311 python -m scripts.validate_chroma_connection
```

결과:

```text
[OK] ChromaDB heartbeat=1779457906291774221
[OK] embedding dim=768
[OK] upsert
[OK] query
[OK] delete
[PASS] validate_chroma_connection
```

### 6. DART filing reindex + retrieval 검증

명령:

```bash
conda run -n tickertaka311 python -m scripts.validate_filing_evidence_retrieval \
  --symbol 005930 \
  --query "매출 영업이익 실적"
```

결과:

```text
[REINDEX] source=filing symbol=005930 rows=10 indexed=10 skipped=0 failed=0
[OK] chroma filing count=10
- score=0.4252 title=최대주주등소유주식변동신고서 url=https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260508801105
- score=0.4034 title=동일인등출자계열회사와의상품ㆍ용역거래변경 url=https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260515002812
- score=0.4034 title=동일인등출자계열회사와의상품ㆍ용역거래변경 url=https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260515002790
[PASS] validate_filing_evidence_retrieval
```

의미:

```text
공용 PostgreSQL filing_cache에서 삼성전자 10개 공시 row 조회 성공
DART document.xml 본문 추출 성공
HuggingFace embedding 생성 성공
로컬 ChromaDB filing collection에 10개 document upsert 성공
query 검색 성공
검색 결과의 source_url이 DART viewer URL임을 확인
```

## 실행 방법

로컬 ChromaDB 실행:

```bash
docker compose up -d chroma
```

Chroma 연결 검증:

```bash
conda run -n tickertaka311 python -m scripts.validate_chroma_connection
```

공시 reindex:

```bash
conda run -n tickertaka311 python -m scripts.reindex_local_chroma --symbol 005930
```

공시 retrieval 검증:

```bash
conda run -n tickertaka311 python -m scripts.validate_filing_evidence_retrieval --symbol 005930
```

강제 재생성:

```bash
conda run -n tickertaka311 python -m scripts.reindex_local_chroma --symbol 005930 --force
```

삭제 후 재생성:

```bash
conda run -n tickertaka311 python -m scripts.reindex_local_chroma --symbol 005930 --reset
```

## 확인된 이슈 / 주의점

### 1. Chroma telemetry warning

검증 중 다음 warning이 출력됨:

```text
Failed to send telemetry event ClientStartEvent: capture() takes 1 positional argument but 3 were given
```

현재 기능에는 영향 없음.

### 2. XMLParsedAsHTMLWarning

DART `document.xml` 내부 XML을 BeautifulSoup `html.parser`로 처리하면서 다음 warning이 출력됨:

```text
XMLParsedAsHTMLWarning
```

현재 본문 추출과 검증은 성공했다. 더 정확한 XML 파싱이 필요하면 후속으로 `lxml` 도입을 검토한다.

### 3. 검색 품질

검증 query `"매출 영업이익 실적"`에서 반환된 상위 결과가 반드시 분기보고서가 아니었다.

가능한 원인:

```text
초기 구현이 단일 document 방식이라 공시 전체가 하나의 embedding으로 들어감
쿼리와 공시 본문 간 의미 매칭이 세밀하지 않음
삼성전자 최근 10개 공시 중 실적 관련 공시가 충분히 없을 수 있음
```

후속 개선:

```text
보고서/사업보고서 우선 필터
본문 청킹
query tuning
filing_type 기반 필터
rerank 단계 추가
```

## 현재 상태 결론

구현 완료:

```text
DART filing metadata -> DART document.xml 본문 추출
본문 -> HuggingFace embedding
embedding/document -> 로컬 ChromaDB filing collection
Chroma 검색 결과 -> PostgreSQL metadata 조합
```

검증 완료:

```text
Chroma 연결 PASS
HuggingFace embedding PASS
삼성전자 filing 10건 indexing PASS
filing evidence retrieval PASS
```

남은 작업:

```text
검색 품질 개선
본문 청킹 적용
뉴스 담당 구현물과 news collection 연동
LangGraph 토론 agent context에 EvidenceItem 연결
```
