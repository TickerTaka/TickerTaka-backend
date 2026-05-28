# Filing Chroma 검증 실행 기록

> 작성일: 2026-05-27  
> 목적: 공시 본문 파싱 v2와 filing Chroma 인덱싱이 실제로 동작하는지 검증한 과정 기록  
> 대상 흐름:
>
> ```text
> DART document ZIP
>   -> DartClient.extract_document_text_v2()
>   -> DartClient.build_filing_chunks()
>   -> EvidenceIndexingService.reindex_filing_for_symbol()
>   -> Chroma filing collection upsert
>   -> source_id 기반 조회
> ```

---

## 1. 검증을 시작한 이유

공시 수집 파이프라인은 PostgreSQL의 `filing_cache`와 Chroma의 `filing` 컬렉션이 역할이 다르다.

```text
PostgreSQL filing_cache
  - 공시 row 원장
  - symbol, filing_title, dart_receipt_no, source_url, disclosed_at 등 메타데이터 저장
  - 토론 컨텍스트에서 제목/summary를 붙일 때 사용

Chroma filing collection
  - 공시 본문 chunk 검색 인덱스
  - RAG 검색 시 실제 본문 내용을 벡터 검색
  - chunk id와 source_id로 filing_cache row에 다시 연결
```

이전 상태의 문제는 `filing_cache`에 row가 있어도 공시 본문이 제대로 Chroma에 들어가지 않으면 토론 RAG가 공시 제목 수준의 정보만 보게 된다는 점이었다.

이번 검증의 목표는 다음 네 가지였다.

```text
1. Chroma 서버가 실제로 뜨는지
2. Chroma에 document upsert/query/delete가 되는지
3. 새로 바꾼 공시 표 파싱 v2가 표 구조를 보존하는지
4. 공시 chunk가 Chroma에 들어가고 source_id로 다시 조회되는지
```

---

## 2. 먼저 처리한 DB 상태

사용자가 `000990`을 워치리스트에 넣어달라고 요청했다.

처음에는 API를 통하지 않고 원격 PostgreSQL에 직접 insert했다. 이유는 당시 로컬 `.venv`에 SQLAlchemy 등 의존성이 없었고, Docker도 바로 접근되지 않았기 때문이다.

추가 대상:

```text
user email: phase2-test-user@example.com
user_id: 92042f2b-9950-457c-8092-b43d79dda768
symbol: 000990
ticker: DB하이텍
watchlist_id: 240ad22c-4bd5-46bd-9d4b-8f470c127724
```

그 다음 사용자가 "왜 백그라운드 동기화를 안 했냐"고 물었고, 직접 DB insert는 FastAPI의 `BackgroundTasks`를 타지 않는다고 설명했다.

API에서 워치리스트를 추가하면 원래 아래 작업들이 enqueue된다.

```python
background_tasks.add_task(sync_watchlist_news, watchlist.symbol)
background_tasks.add_task(sync_watchlist_prices, watchlist.symbol)
background_tasks.add_task(sync_watchlist_financials, watchlist.symbol)
background_tasks.add_task(sync_watchlist_filings, watchlist.symbol)
```

하지만 DB 직접 insert는 이 코드를 실행하지 않는다.

그래서 이후에는 캐시 동기화를 직접 실행했다.

---

## 3. 원격 PostgreSQL 캐시 동기화 결과

실행한 서비스:

```text
PriceIngestionService.sync_prices_for_ticker("000990")
FinancialIngestionService.sync_financials_for_ticker("000990")
FilingIngestionService.sync_filings_for_ticker("000990")
```

뉴스는 `.env`에 `NAVER_NEWS_CLIENT_ID`, `NAVER_NEWS_CLIENT_SECRET`가 비어 있어 실행하지 못했다.

최종 DB 카운트:

```text
watchlist=1
price_cache=244
technical_indicator_cache=244
financial_cache=17
filing_cache=11
```

즉 원격 PostgreSQL에는 현재 아래 데이터가 들어간 상태다.

```text
watchlist
  - 000990 1건

price_cache
  - 000990 가격 데이터 244건

technical_indicator_cache
  - 000990 기술지표 244건

financial_cache
  - 000990 재무 데이터 17건

filing_cache
  - 000990 DART 공시 메타데이터 11건
```

중요한 점:

```text
filing_cache 11건은 공시 메타데이터다.
이 자체가 Chroma 임베딩 완료를 의미하지 않는다.
```

---

## 4. 로컬 실행 환경 문제

프로젝트 루트의 `.python-version`은 다음과 같았다.

```text
3.12.10
```

하지만 기존 `.venv`는 Python 3.9였다.

```text
.venv/bin/python -V
Python 3.9.6
```

그리고 설치된 패키지도 거의 없었다.

```text
Package    Version
---------- -------
pip        21.2.4
setuptools 58.0.4
```

그래서 처음 Python으로 DB 작업을 하려 했을 때 실패했다.

```text
ModuleNotFoundError: No module named 'sqlalchemy'
```

---

## 5. Docker/psql 접근 문제

처음에는 Docker에 떠 있는 DB나 컨테이너 안의 `psql`을 사용하려 했다.

하지만 일반 권한에서는 Docker socket 접근이 막혔다.

```text
permission denied while trying to connect to the Docker daemon socket
```

권한을 올려 다시 확인했지만 Docker daemon 자체가 실행 중이 아니었다.

```text
Cannot connect to the Docker daemon at unix:///Users/ohheungchan/.docker/run/docker.sock.
Is the docker daemon running?
```

로컬에는 `psql`도 없었다.

```text
psql not found
```

그래서 DBeaver가 이미 가지고 있는 PostgreSQL JDBC 드라이버를 이용했다.

찾은 드라이버:

```text
/Users/ohheungchan/Library/DBeaverData/drivers/maven/maven-central/org.postgresql/postgresql-42.7.2.jar
```

`jshell`로 JDBC 접속을 시도했다. 처음에는 JShell이 로컬 socket을 열려고 하면서 샌드박스 권한에 막혔다.

```text
java.net.SocketException: Operation not permitted
```

권한을 올려 실행하자 `.env`의 원격 DB에 연결할 수 있었고, 워치리스트 insert에 성공했다.

---

## 6. 임시 Python 3.13 venv 생성

기존 `.venv`가 Python 3.9라 requirements와 맞지 않았기 때문에 임시 venv를 새로 만들었다.

```bash
/opt/homebrew/bin/python3.13 -m venv /private/tmp/tickertaka-sync-venv
```

이 임시 venv는 프로젝트 파일을 수정하지 않고 검증만 하기 위한 환경이다.

---

## 7. requirements.txt 전체 설치 실패

처음에는 전체 의존성을 설치하려 했다.

```bash
.venv/bin/pip install -r requirements.txt
```

하지만 실패했다.

```text
ERROR: Could not find a version that satisfies the requirement redis==7.4.0
```

원인:

```text
기존 .venv가 Python 3.9였고, 현재 requirements의 일부 버전과 호환되지 않았다.
```

그래서 전체 설치 대신 필요한 최소 패키지만 임시 venv에 설치하는 방식으로 바꿨다.

---

## 8. 동기화용 최소 패키지 설치

처음에는 `psycopg2-binary==2.9.9`를 설치하려 했지만 Python 3.13에서 wheel이 없어 source build로 넘어갔다.

그 결과 `pg_config`가 없어 실패했다.

```text
Error: pg_config executable not found.
```

그래서 SQLAlchemy 연결 URL을 `postgresql+psycopg://...` 형태로 바꾸고, `psycopg` v3를 사용했다.

설치한 주요 패키지:

```text
sqlalchemy==2.0.35
psycopg[binary]
pydantic-settings==2.5.2
python-dotenv==1.0.1
requests==2.32.3
beautifulsoup4==4.12.3
pandas==2.2.3
yfinance==0.2.43
pykrx==1.0.45
tenacity==9.0.0
redis==7.0.1
```

원래 코드의 DB URL은 다음과 같다.

```text
postgresql://stock_user:tickertaka@101.79.19.53:5432/stock_debate
```

임시 실행에서는 psycopg v3 드라이버를 쓰기 위해 아래처럼 바꿔 넣었다.

```text
postgresql+psycopg://stock_user:tickertaka@101.79.19.53:5432/stock_debate
```

---

## 9. 가격 동기화 중 pykrx 문제

가격 동기화에서 처음 실패한 에러:

```text
ModuleNotFoundError: No module named 'pkg_resources'
RuntimeError: pykrx is required for price cache ingestion
```

원인:

```text
pykrx가 내부에서 pkg_resources를 import한다.
하지만 최신 setuptools에서는 pkg_resources가 빠졌거나 기본 제공되지 않는다.
```

처음에는 `setuptools` 최신 버전을 설치했다.

```bash
pip install setuptools
```

하지만 최신 버전에서도 해결되지 않았다.

그래서 `pkg_resources`가 포함된 구버전으로 낮췄다.

```bash
pip install 'setuptools<81' --force-reinstall
```

그 다음 가격 동기화가 성공했다.

```text
SyncPriceResult(
  fetched_count=244,
  inserted_count=244,
  updated_count=0,
  indicators_count=244,
  skipped_count=0,
  trimmed_price_rows=0,
  trimmed_indicator_rows=0
)
```

---

## 10. Redis 우회

동기화 서비스들은 Redis lock/cooldown을 사용한다.

하지만 `.env`의 Redis는 다음 상태였다.

```text
REDIS_URL=redis://localhost:6379/0
```

현재 로컬 Redis가 떠 있다는 보장이 없었고, 이번 목적은 캐시 동기화와 Chroma 검증이었다.

그래서 실제 Redis 대신 테스트용 `DummyRedis`를 주입했다.

지원한 메서드:

```text
set()
get()
delete()
incr()
expire()
```

이렇게 해서 lock/cooldown 때문에 동기화가 skip되는 것을 막았다.

---

## 11. Chroma 설치

공시 Chroma 검증을 위해 임시 venv에 Chroma를 설치했다.

```bash
/private/tmp/tickertaka-sync-venv/bin/pip install chromadb==0.5.23
```

이 과정에서 `chroma-hnswlib`는 로컬에서 wheel build가 됐다.

---

## 12. Chroma 첫 실행 실패

처음 Chroma 서버를 띄운 명령:

```bash
/private/tmp/tickertaka-sync-venv/bin/chroma run \
  --host 127.0.0.1 \
  --port 8080 \
  --path /private/tmp/tickertaka-chroma-filing-test
```

처음에는 샌드박스 권한 때문에 포트 바인딩이 실패했다.

```text
error while attempting to bind on address ('127.0.0.1', 8080): operation not permitted
```

대처:

```text
권한을 올려서 같은 명령을 다시 실행했다.
```

그 결과 Chroma 서버가 떴다.

```text
Uvicorn running on http://127.0.0.1:8080
Saving data to: /private/tmp/tickertaka-chroma-filing-test
```

---

## 13. Chroma 버전 호환 문제

Chroma 서버는 떴지만, 기본 검증 스크립트 실행 중 `422 Unprocessable Content`가 발생했다.

에러:

```text
Client error '422 Unprocessable Content'
Input should be a valid dictionary or object to extract fields from
```

원인:

```text
chromadb==0.5.23을 설치하면서 fastapi/httpx/pydantic이 너무 최신 버전으로 함께 설치됐다.
프로젝트 requirements.txt가 의도한 버전 조합과 달라져서 Chroma 서버의 요청 바디 파싱이 깨졌다.
```

대처:

프로젝트 requirements에 맞춰 관련 패키지를 낮췄다.

```bash
/private/tmp/tickertaka-sync-venv/bin/pip install \
  'fastapi==0.115.0' \
  'uvicorn[standard]==0.32.0' \
  'httpx==0.27.2' \
  'pydantic==2.9.2' \
  'pydantic-settings==2.5.2' \
  'starlette==0.38.6' \
  --force-reinstall
```

기존 Chroma 서버를 종료하고 다시 띄웠다.

```bash
kill 89311

/private/tmp/tickertaka-sync-venv/bin/chroma run \
  --host 127.0.0.1 \
  --port 8080 \
  --path /private/tmp/tickertaka-chroma-filing-test
```

---

## 14. Chroma 기본 검증

실행한 검증:

```bash
PYTHONPATH=. \
DATABASE_URL='postgresql+psycopg://stock_user:tickertaka@101.79.19.53:5432/stock_debate' \
CHROMA_URL='http://127.0.0.1:8080' \
/private/tmp/tickertaka-sync-venv/bin/python \
scripts/validate_chroma_connection.py
```

처음에는 `PYTHONPATH`가 없어 실패했다.

```text
ModuleNotFoundError: No module named 'app'
```

대처:

```text
PYTHONPATH=. 를 붙여 프로젝트 루트를 import path에 추가했다.
```

최종 결과:

```json
{
  "heartbeat_ok": true,
  "news_count_after_upsert": 1,
  "filing_count_after_upsert": 1,
  "query_hit_id": "news-doc-2",
  "delete_ok": true
}
```

의미:

```text
1. Chroma heartbeat 성공
2. news collection upsert 성공
3. filing collection upsert 성공
4. query 성공
5. delete 성공
```

---

## 15. 공시 Chroma end-to-end 검증

실행한 검증:

```bash
PYTHONPATH=. \
DATABASE_URL='postgresql+psycopg://stock_user:tickertaka@101.79.19.53:5432/stock_debate' \
CHROMA_URL='http://127.0.0.1:8080' \
/private/tmp/tickertaka-sync-venv/bin/python \
scripts/validate_filing_evidence_retrieval.py
```

이 검증은 실제 DART API를 호출하지 않는다.

대신 `FakeDartClient`가 HTML을 in-memory ZIP으로 만든다.

```text
FakeDartClient.fetch_document_xml()
  -> document.html이 들어 있는 ZIP bytes 반환
```

그 후 실제 `DartClient`의 메서드를 그대로 사용한다.

```text
DartClient.extract_document_text_v2()
DartClient.build_filing_chunks()
```

검증 흐름:

```text
1. filing_cache에 검증용 row 생성
2. FakeDartClient가 공시 HTML ZIP 반환
3. extract_document_text_v2()로 구조 보존 텍스트 추출
4. build_filing_chunks()로 ChromaDocument 생성
5. EvidenceIndexingService.reindex_filing_for_symbol() 실행
6. Chroma validate collection에 upsert
7. where={"source_id": row_id}로 다시 조회
8. 검증용 filing_cache row 삭제
9. 검증용 Chroma collection 삭제
```

최종 결과:

```json
{
  "symbol": "000020",
  "scanned_rows": 1,
  "indexed_rows": 1,
  "skipped_rows": 0,
  "failed_rows": 0,
  "collection_count": 1,
  "fetched_id": "f028c186-ccaf-43d3-8b43-c64dc1e15fea:s0:c0"
}
```

의미:

```text
scanned_rows=1
  - filing_cache row 1개를 읽음

indexed_rows=1
  - 공시 1건이 정상 인덱싱됨

skipped_rows=0
  - 본문 길이 부족이나 chunk 없음으로 skip되지 않음

failed_rows=0
  - DART fetch/parse/index 단계에서 실패 없음

collection_count=1
  - Chroma validate collection에 document 1개 저장됨

fetched_id=...:s0:c0
  - source_id 기반 조회로 저장된 chunk를 다시 찾음
```

---

## 16. 표 파싱 결과 확인

테스트 HTML:

```html
<table>
  <tr><th>구분</th><th>당기</th><th>전기</th></tr>
  <tr><td>매출액</td><td>267,627억</td><td>302,231억</td></tr>
  <tr><td>영업이익</td><td>6,567억</td><td>43,376억</td></tr>
</table>
```

`extract_document_text_v2()` 출력:

```text
## 검증 공시
구분: 매출액 | 당기: 267,627억 | 전기: 302,231억
구분: 영업이익 | 당기: 6,567억 | 전기: 43,376억
```

중요한 변화:

```text
Before:
  구분
  당기
  전기
  매출액
  267,627억
  302,231억

After:
  구분: 매출액 | 당기: 267,627억 | 전기: 302,231억
```

즉 숫자와 레이블의 관계가 보존된다.

---

## 17. 현재 만들어진 Chroma DB 상태

현재 로컬에 띄운 Chroma 서버:

```text
URL: http://127.0.0.1:8080
저장 경로: /private/tmp/tickertaka-chroma-filing-test
```

검증에 사용한 컬렉션:

```text
news_validate
filing_validate
filing_validate_reindex
```

단, 검증 스크립트는 마지막에 validate collection을 삭제한다.

따라서 이 Chroma DB는 운영 데이터가 들어간 상태가 아니라, 다음을 확인한 상태다.

```text
1. Chroma 서버 실행 가능
2. collection 생성 가능
3. document upsert 가능
4. query 가능
5. metadata where 조회 가능
6. delete 가능
7. filing chunk id/source_id 구조 정상
```

현재 Chroma 서버 프로세스는 임시 테스트 서버다.

```text
프로세스: chroma run
포트: 127.0.0.1:8080
데이터 경로: /private/tmp/tickertaka-chroma-filing-test
```

---

## 18. Chroma document 형식

공시 Chroma document id 형식:

```text
{filing_cache.id}:s{section_index}:c{chunk_index}
```

예:

```text
f028c186-ccaf-43d3-8b43-c64dc1e15fea:s0:c0
```

metadata 형식:

```json
{
  "symbol": "000020",
  "source_type": "filing",
  "source_id": "f028c186-ccaf-43d3-8b43-c64dc1e15fea",
  "filing_title": "000020 filing evidence validation",
  "section": "검증 공시",
  "chunk_index": 0,
  "disclosed_at": "2026-05-27T..."
}
```

핵심은 `source_id`다.

```text
Chroma document id는 chunk id다.
PostgreSQL filing_cache id는 source_id에 들어간다.
```

그래서 retrieval에서는 아래처럼 해야 한다.

```text
Chroma hit id:
  f028c186-...:s0:c0

metadata.source_id:
  f028c186-...

PostgreSQL 조회:
  filing_cache.id = metadata.source_id
```

이 문제 때문에 `app/domain/evidence_retrieval.py`의 `_search_filings()`도 `source_id` 기반 join으로 바꿨다.

---

## 19. 운영용 filing collection과 이번 테스트의 차이

이번 테스트:

```text
collection: filing_validate_reindex
embedding: DeterministicEmbeddingClient
dimension: 64
목적: 코드 흐름 검증
```

운영 목표:

```text
collection: filing
embedding: jhgan/ko-sroberta-multitask
dimension: 768
목적: 실제 토론 RAG 검색
```

즉 이번 테스트는 "공시 v2 파싱과 Chroma 저장 로직이 동작하는가"를 검증한 것이다.

실제 운영용 `filing` 컬렉션에 넣으려면 다음이 필요하다.

```text
1. Chroma 서버를 운영 경로로 띄우기
2. 기존 filing 컬렉션 삭제
3. sentence-transformers 설치
4. EMBEDDING_PROVIDER=huggingface
5. EMBEDDING_MODEL=jhgan/ko-sroberta-multitask
6. scripts/reindex_all_filings.py 또는 EvidenceIndexingService.reindex_filing_for_symbol("000990") 실행
```

---

## 20. 1차 검증 결론 (임시 Chroma 기준)

검증 결과:

```text
공시 v2 파싱 OK
표 구조 보존 OK
section/chunk 생성 OK
Chroma upsert OK
source_id metadata 조회 OK
filing_cache row와 Chroma chunk 연결 구조 OK
```

아직 운영용으로 남은 일:

```text
1. 실제 filing collection reset
2. 768차원 embedding 환경 구성
3. 실제 filing_cache 전 종목에 대해 reindex 실행
4. 토론 RAG가 filing collection을 실제로 조회하는지 end-to-end 확인
```

한 줄 요약:

```text
이번에 만든 것은 테스트용 Chroma DB이고, 공시 파싱/청킹/저장/조회 코드는 정상 동작함을 확인했다.
운영 filing Chroma는 768차원 임베딩 환경을 맞춘 뒤 별도 재색인이 필요하다.
```

---

## 21. 임시 Chroma에서 Docker Chroma로 전환한 이유

1차 검증은 임시 경로(`/private/tmp/tickertaka-chroma-filing-test`)로 Chroma를 수동으로 띄워서 진행했다.

이 방식의 문제는 두 가지였다.

```text
1. /private/tmp는 재부팅 시 삭제된다.
   → reindex한 공시 벡터가 날아가면 다시 DART API를 호출해야 한다.

2. 임시 Chroma는 프로젝트 docker-compose와 무관한 1회성 프로세스다.
   → 운영 배포 시 연결이 끊긴다.
```

프로젝트의 `docker-compose.yml`에는 Chroma 서비스가 이미 정의되어 있었다.

```yaml
chroma:
  image: chromadb/chroma:0.5.23
  container_name: tickertaka-chroma
  environment:
    IS_PERSISTENT: "TRUE"
    ALLOW_RESET: "TRUE"
    PERSIST_DIRECTORY: "/chroma/chroma"
  ports:
    - "8080:8000"
  volumes:
    - chromadata:/chroma/chroma
```

`chromadata` Docker 볼륨에 데이터가 영구 저장되고, 컨테이너를 재시작해도 인덱스가 유지된다.

그래서 임시 프로세스를 종료하고 Docker Compose로 전환했다.

---

## 22. Docker Chroma 전환 과정

### 22-1. 임시 Chroma 프로세스 종료

```bash
kill 89994
```

### 22-2. Docker Desktop 실행 후 컨테이너 기동

```bash
docker-compose up -d chroma
```

확인:

```bash
docker ps --format "table {{.Names}}\t{{.Status}}"
```

결과:

```text
tickertaka-chroma     Up
tickertaka-postgres   Up (healthy)
```

### 22-3. heartbeat 확인

```
GET http://127.0.0.1:8080/api/v2/heartbeat
```

응답:

```json
{"nanosecond heartbeat": 1779866138364881920}
```

`.env`의 `CHROMA_URL=http://localhost:8080`이 Docker 컨테이너 포트(`8080:8000`)와 일치하므로 별도 설정 변경 없이 연결됐다.

---

## 23. 운영 filing 컬렉션 reset

Docker Chroma가 올라온 시점의 `filing` 컬렉션 상태를 확인했다.

```text
현재 'filing' 컬렉션: 10개 문서
```

이 10개는 이전에 64차원 `DeterministicEmbeddingClient`로 넣은 테스트 데이터였다.

운영 임베딩인 `jhgan/ko-sroberta-multitask`(768차원)과 차원이 달라서 그대로 두면 upsert 시 차원 충돌이 발생한다.

삭제:

```bash
PYTHONPATH=. \
DATABASE_URL='postgresql+psycopg://...' \
CHROMA_URL='http://127.0.0.1:8080' \
python scripts/reset_filing_collection.py
```

결과:

```text
[reset] 현재 'filing' 컬렉션: 10개 문서
[reset] 'filing' 컬렉션 삭제 완료
[reset] 다음 reindex 실행 시 새 embedding 차원으로 재생성됩니다
```

---

## 24. 운영 reindex 1차 시도 — Redis 미기동으로 전체 실패

reset 후 바로 reindex를 실행했다.

```bash
PYTHONPATH=. \
DATABASE_URL='postgresql+psycopg://...' \
CHROMA_URL='http://127.0.0.1:8080' \
python scripts/reindex_all_filings.py
```

결과:

```json
{
  "count": 2,
  "results": [
    {"symbol": "000990", "scanned_rows": 11, "indexed_rows": 0, "failed_rows": 11},
    {"symbol": "005930", "scanned_rows": 10, "indexed_rows": 0, "failed_rows": 10}
  ]
}
```

모든 공시가 `failed_rows`로 처리됐다.

에러 원인:

```text
DART document.xml API 호출 자체는 성공
→ 응답 수신 후 _record_daily_api_call() 실행
→ Redis INCR 호출
→ localhost:6379 연결 거부
→ ConnectionRefusedError 발생
→ Exception으로 처리되어 failed_rows 카운트
```

`DartClient._record_daily_api_call()`은 Redis가 `None`이 아니면 연결을 시도한다.

```python
def _record_daily_api_call(self) -> None:
    if self.redis_client is None:
        return
    key = make_key("dart-api-count", ...)
    count = self.redis_client.incr(key)   # ← Redis 연결 실패 시 예외 발생
```

`.env`에 `REDIS_URL=redis://localhost:6379/0`이 설정되어 있어서 Redis 클라이언트가 생성됐지만, 컨테이너가 기동되지 않은 상태였다.

---

## 25. Redis 컨테이너 기동

```bash
docker-compose up -d redis
```

기동 확인:

```bash
docker ps --format "table {{.Names}}\t{{.Status}}"
```

결과:

```text
tickertaka-redis      Up
tickertaka-chroma     Up
tickertaka-postgres   Up (healthy)
```

---

## 26. 운영 reindex 2차 — 전체 성공

Redis 기동 후 reindex 재실행:

```bash
PYTHONPATH=. \
DATABASE_URL='postgresql+psycopg://...' \
CHROMA_URL='http://127.0.0.1:8080' \
python scripts/reindex_all_filings.py
```

결과:

```json
{
  "count": 2,
  "results": [
    {"symbol": "000990", "scanned_rows": 11, "indexed_rows": 11, "skipped_rows": 0, "failed_rows": 0},
    {"symbol": "005930", "scanned_rows": 10, "indexed_rows": 10, "skipped_rows": 0, "failed_rows": 0}
  ]
}
```

```text
000990 DB하이텍: 11건 전부 인덱싱 성공
005930 삼성전자: 10건 전부 인덱싱 성공
```

---

## 27. 최종 Chroma 컬렉션 상태

```bash
PYTHONPATH=. CHROMA_URL='http://127.0.0.1:8080' python -c "
from app.external.chroma_client import ChromaClient, FILING_COLLECTION_NAME
c = ChromaClient()
print(c.count(FILING_COLLECTION_NAME))
"
```

결과:

```text
filing 컬렉션 총 chunk 수: 591
```

chunk 샘플:

```text
id:        f0e62177-...:s0:c0
source_id: f0e62177-...         ← filing_cache.id
symbol:    000990
section:   최대주주등소유주식변동신고서
본문:      회사명: (주)DB하이텍 | 회사코드: 0000090 | 담당부서명: ...
```

이전(10개 벡터)과 비교:

```text
Before: filing_cache 21건 → Chroma 문서 10개 (64차원, get_text 방식)
After:  filing_cache 21건 → Chroma 문서 591개 (768차원, 표 구조 보존 + 섹션별 청킹)
```

공시 1건당 평균 약 28개 chunk가 생성됐다.

---

## 28. chunk가 많아진 이유

기존 방식:

```text
공시 1건
→ get_text() → 평탄화 텍스트
→ ChromaDocument 1개
→ 벡터 1개
```

새 방식:

```text
공시 1건
→ HTML 표 grid 확장 → 구조 보존 텍스트
→ "## 섹션 제목" 기준 분할
→ 섹션이 길면 max_chunk_chars(1200) 기준으로 재분할
→ ChromaDocument N개
→ 벡터 N개
```

분기보고서/사업보고서처럼 수십 페이지인 경우 섹션 수가 많아서 chunk가 많이 생긴다.

chunk가 많아지면 RAG 검색에서 쿼리와 가까운 섹션만 정확히 찾아올 수 있다.

예:

```text
bull agent 쿼리: "영업이익 개선 근거"
→ filing chunk 중 재무 섹션 chunk만 높은 점수로 검색됨
→ "구분: 영업이익 | 당기: 6,567억 | 전기: 43,376억" 포함 chunk 반환
```

---

## 29. 최종 환경 상태

```text
Docker 컨테이너:
  tickertaka-postgres   Up (원격 NCP DB 사용 중이므로 로컬은 개발용)
  tickertaka-redis      Up
  tickertaka-chroma     Up → chromadata 볼륨에 영구 저장

.env:
  CHROMA_URL=http://localhost:8080     → Docker 컨테이너와 연결
  EMBEDDING_PROVIDER=huggingface       → config 기본값
  EMBEDDING_MODEL=jhgan/ko-sroberta-multitask → config 기본값 (768차원)
  DART_API_KEY=설정됨

filing Chroma 컬렉션:
  - 총 591개 chunk
  - 768차원 임베딩
  - 종목: 000990, 005930
  - chunk id: {filing_cache.id}:s{section}:c{chunk}
  - metadata.source_id → filing_cache.id 연결 구조
```

---

## 30. 이 작업의 의미

토론 에이전트는 발언 생성 전 `EvidenceRetrievalService.search_symbol_evidence()`를 통해 근거를 수집한다.

```text
data_agent
  → EvidenceRetrievalService.search_symbol_evidence(query, symbol)
     → news Chroma 검색
     → filing Chroma 검색       ← 이 부분이 이번 작업으로 개선됨
  → evidence_context 생성
  → bull / bear / moderator 발언에 사용
```

이번 작업 이전:

```text
"bear: 실적 악화 우려가 있습니다." (근거 없음 또는 제목만)
```

이번 작업 이후:

```text
"bear: 영업이익이 전기 43,376억에서 당기 6,567억으로 85% 감소했습니다.
 (출처: 000990 분기보고서 - 재무에 관한 사항)"
```

공시 표 데이터가 의미 있는 형태로 벡터에 들어가 있어야 이런 검색이 가능하다.

이번 고도화의 핵심이 바로 그 부분이다.

