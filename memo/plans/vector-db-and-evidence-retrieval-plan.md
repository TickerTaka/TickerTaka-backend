# Vector DB (ChromaDB) + Evidence Retrieval 계획

## 목표

토론 단계에서 cache 테이블들(`news_cache`, `filing_cache`, 추후 `financial_cache`)에 적재된 텍스트를 **의미 기반(semantic)으로 검색**하여 evidence 후보를 추출한다.

핵심 원칙 (옵션 B 채택 반영, `news-cache-policy-revision-plan.md` 결정 사항):
- **PostgreSQL은 메타데이터 truth, ChromaDB가 본문 + 검색 SOT** — 본문(content)은 ChromaDB document에만 보관, PG `news_cache.content`/`filing_cache.content`는 항상 NULL
- PostgreSQL `news_cache.id` (UUID) ↔ ChromaDB `document.id` 1:1 매핑 — 본문 참조는 cache id로 ChromaDB lookup
- 토론은 카테고리(technical/financial/market) × 3 라운드 × Bull/Bear/Judge 구조 → 각 발언당 evidence 1개 이상 강제 → 검색 품질이 토론 품질 직결.
- BM25 같은 키워드 검색은 종목명이 너무 자주 등장하는 한국 기사 특성상 약하므로 dense embedding 우선.
- 영구성/대용량 운영은 후속 단계, 초기 구현은 단일 ChromaDB 인스턴스로 시작.
- ChromaDB가 본문 SOT이므로 백업/복구 절차 확보가 plan 닫힘 기준에 포함.

## 검증 완료 내용

확인 완료:
- `docker-compose.yml`에 `chromadb/chroma:latest` 컨테이너 정의 (port 8080, volume `chromadata`)
- `.env.example`에 `CHROMA_URL` 환경 변수 있음
- 영구 저장은 `chromadata` 볼륨에 위임 (compose에 정의됨)
- ChromaDB 0.5+ 기준 client 사용 가능

추가 검증 필요:
- 클라우드 운영 시 ChromaDB 호스팅 결정 (self-host vs Chroma Cloud vs 대안 — Qdrant/Weaviate)
- 본 plan은 self-host (docker-compose) 기준

## 아키텍처 (옵션 B)

```
PostgreSQL (메타데이터 truth)        ChromaDB (본문 + 검색 SOT)
─────────────────────────────       ─────────────────────────────
news_cache    (content = NULL) ───► collection: news
  id, title, summary, source_url,     id = news_cache.id (UUID)
  symbol, published_at, ttl_until     document = 본문 텍스트
                                      embedding = 1536차원 벡터
                                      metadata = {symbol, source_id, ...}

filing_cache  (content = NULL) ───► collection: filing
  id, filing_title, source_url, ...   동일 구조

financial_cache (수치 데이터)        (임베딩 대상 아님, 후속 검토)
```

원칙:
- Cache row 적재 시 → 본문 추출 성공한 row만 INSERT + 동시에 ChromaDB upsert
- Cache row 삭제 시 → 같은 ID로 ChromaDB delete
- 본문 참조: 토론 evidence 검색 결과의 `id`로 ChromaDB `get(ids=[...])` 호출 → document 텍스트 retrieve
- UI 카드 표시는 PG의 `title` + `summary` + `source_url` (redirection)

## 임베딩 대상

| Cache | Document 텍스트 | 우선순위 |
|---|---|---|
| NewsCache | `title` + `\n\n` + 추출된 본문 (PG에는 NULL, ChromaDB document에 저장) | 1순위 |
| FilingCache | `filing_title` + `\n\n` + 추출된 본문 | 1순위 |
| FinancialCache | 핵심 수치를 자연어 직렬화 (예: "2024Q3 매출 X억, 영업이익 Y억...") | 후속 |
| PriceCache / TechnicalIndicatorCache | 임베딩 대상 아님 — 수치 검색은 SQL이 더 적합 | — |

청킹 정책:
- **초기 구현은 단일 청크 우선** — 한국 기사/공시 대부분 1만자 이내, 단일 청크로 표현
- 단일 청크면 `document.id = cache_row.id` 1:1 매핑 — cleanup/lookup 단순화
- 본문이 1만자 초과 시에만 분할 — `{cache_row_uuid}:chunk:{idx}` + metadata에 `chunk_idx` 표기
- 단일 청크 정책이 우선이고, 청크 분할은 예외 케이스로 처리

## 임베딩 모델

후보:
- **OpenAI `text-embedding-3-small`** (1536 차원)
  - 한국어 품질 양호, 비용 저렴 (\$0.02 / 1M tokens)
  - OpenRouter 경유 가능 여부 확인 필요 (기본은 OpenAI 직접)
- **BGE-M3** (1024 차원, 다국어 강함)
  - self-host 가능 → 비용 0, latency 통제 가능
  - GPU 또는 대용량 RAM 필요
- **ko-sroberta-multitask** (한국어 특화 sentence-transformers)
  - CPU 가능, 한국어 단문 강함
  - 다국어 약함

초기 선택: **OpenAI `text-embedding-3-small`** — 개발 속도 + 한국어 수용 가능
운영 비용 부담 시 BGE-M3로 self-host 이전.

환경 변수 추가:
- `EMBEDDING_MODEL=openai/text-embedding-3-small` (또는 `bge-m3`)
- `OPENAI_API_KEY` (이미 OpenRouter 키 있음 — 임베딩은 별도 OpenAI 직접 호출 필요 여부 확인)

## Collection 구조

```
collection_name = "news" | "filing" | "financial"
embedding_function = OpenAIEmbeddings(...) or BGEEmbeddings(...)

document fields:
  id        = "{cache_row_uuid}" 또는 "{cache_row_uuid}:chunk:{idx}"
  document  = text 본문
  metadata  = {
    symbol: str
    source_id: str (cache_row_uuid)
    source_type: "news" | "filing" | ...
    published_at: ISO 8601 string
    source_url: str
    chunk_idx: int (청킹 시)
  }
```

설계 결정:
- **per-type collection** (news/filing 분리) — symbol 필터로는 collection을 분리하지 않음
- symbol은 metadata filter로 검색 시 적용 (`where={"symbol": "005930"}`)
- collection을 symbol 단위로 너무 잘게 쪼개면 collection 관리 비용↑

## 인덱싱 시점

옵션:
- **A. 동기 임베딩** — cache sync 함수 마지막에 embedding upsert 수행
  - 장점: 항상 최신, 별도 sweep 불필요
  - 단점: sync 함수가 늦어짐 (OpenAI 호출 + 네트워크)
- **B. 비동기 sweep** — 매 시간 신규 row 임베딩 (cache_row에 `embedded_at` 필드 추가 또는 ChromaDB id 존재 확인)
  - 장점: cache sync 함수 빠름
  - 단점: 토론 시점에 갓 적재된 evidence 누락 가능
- **C. 토론 시점 lazy** — 미임베딩 row를 그때 임베딩
  - 토론 latency 직접 영향 → 비추천

초기 선택: **옵션 A** — cache sync는 이미 background task로 분리되어 응답 latency 외 영향 없음.

**Migration path (동기 → 비동기 reindex)**:
- watchlist 등록 직후 뉴스/가격/재무/공시가 병렬로 늘어나면 동기 임베딩이 background task 시간을 길게 만들 수 있음
- 신호 (sync 함수 p95 > 60초, OpenAI rate limit hit) 감지 시 옵션 B로 이전
- 이전 시 추가 도입: `embedded_at` 컬럼 또는 ChromaDB id 존재 확인 + 별도 sweep cron
- 본 plan은 옵션 A로 시작, 이전 절차는 후속 plan에서 정의

구현 위치:
- `app/external/chroma_client.py` — ChromaDB wrapper (HttpClient + token authn)
- `app/external/embedding.py` — OpenAI 임베딩 호출 wrapper
  - **batch embedding 지원** (`client.embeddings.create(input=[text1, text2, ...])`) — 여러 row를 한 번에 처리해 latency/비용 절감
  - **retry + exponential backoff** (rate limit 429 / 5xx 대응) — `tenacity` 또는 직접 구현
  - **fail-soft** — ChromaDB upsert 실패해도 PG 적재는 성공으로 처리, 후속 reconcile 스크립트로 backfill
- `app/domain/evidence_indexing.py` — cache row → document 변환 + upsert
- `NewsIngestionService.sync_news_for_ticker` 끝에 `evidence_indexer.upsert_news(rows)` 호출
- `FilingIngestionService.sync_filings_for_ticker`도 동일

## Retrieval 흐름 (토론 시점)

```
1. 토론 세션 시작
   user_id, symbol, category ∈ {technical, financial, market}

2. 카테고리별 query 생성
   technical: "{name_kr} 기술적 분석 차트 이동평균 RSI 거래량 추세"
   financial: "{name_kr} 매출 영업이익 재무 부채 현금흐름"
   market: "{name_kr} 시장 업황 경쟁 거시 외부 요인"

3. ChromaDB collection별 top-K 검색
   news     → top 5
   filing   → top 3
   financial(선택) → top 2

4. metadata filter
   {"symbol": "005930"}
   {"published_at": {"$gte": <7 days ago>}}  # 카테고리에 따라 윈도우 조정

5. Source mix / quota 정책 (category별):
   - technical: news 우선 (3+) + financial 보조
   - financial: filing + financial_cache 우선 (3+) + news 보조
   - market: news 우선 (4+) — filing은 거의 안 나옴, 부족 시 news fallback
   - 카테고리별 source 부족 시 다른 source로 fallback (예: market에서 filing 0건이면 news 추가)
   - 출처 quota는 후속 phase에서 운영 신호 보고 조정

6. 결과 후처리
   - 중복 source 제거 (같은 source_id 여러 청크 → 1개)
   - 출처 다양성 ranking (news clustering 등)
   - LangGraph context에 주입

7. 발언 시 evidence_id (= cache_row.id)로:
   - **PG에서 메타데이터 lookup** (title, summary, source_url) — UI 카드 표시용
   - **ChromaDB에서 본문 텍스트 retrieve** — LLM evidence 컨텍스트 주입용 (`collection.get(ids=[evidence_id])`)
```

## TTL / 삭제 동기화

PostgreSQL과 ChromaDB는 1:1이어야 함. 동기화 지점:

- **삭제 동기화**:
  - cache row 삭제 (TTL 만료, row 상한 초과) → 같은 ID로 `collection.delete(ids=[...])`
  - cleanup sweep에서 SQL delete 직후 동일 ID 리스트로 ChromaDB delete 호출
  - repository의 `delete_expired_rows_returning_ids` / `trim_rows_for_symbol_returning_ids` 인터페이스로 삭제된 ID 회수 → scheduler가 ChromaDB delete 호출
- **fail 정책**:
  - PG delete 성공 + ChromaDB delete 실패 → **fail-soft** (로그 기록 후 진행, drift 발생 가능)
  - drift 복구는 별도 `scripts/reconcile_chroma_news_cache.py` 운영 스크립트로 처리
    - PG row 없는데 ChromaDB document 남은 케이스 (orphan): ChromaDB delete
    - ChromaDB document 없는데 PG row 있는 케이스 (옵션 B에선 거의 없어야 함, sync 실패 시 발생): 본 plan은 fail-soft라 후속 sync에서 자연 복구
  - reconcile 스크립트는 cron으로 일 1회 실행 또는 수동
- **content 갱신**:
  - 정정 공시나 본문 보강으로 content가 바뀌면 ChromaDB document upsert로 덮어쓰기 (옵션 B에선 PG content는 항상 NULL이므로 변경 없음, ChromaDB만 갱신)

## 환경 변수 / 외부 의존성

추가 환경 변수:
- `CHROMA_URL=http://localhost:8080` (이미 있음 — 운영 시 NCP 서버 IP로 교체)
- `CHROMA_TOKEN=` (토큰 인증 사용 시)
- `EMBEDDING_MODEL=openai/text-embedding-3-small`
- `OPENAI_API_KEY=...` (임베딩 직접 호출용 — OpenRouter는 임베딩 미지원이면 추가 필요)

requirements.txt 추가:
- `chromadb-client==0.5.x` (서버 분리, client만)
- `openai==1.x.x` (이미 있을 수 있음 — 확인 필요)
- (옵션 BGE-M3 도입 시) `sentence-transformers==3.x.x` + `torch`

## 운영 환경 배치 (NCP 서버 + Docker 셀프 호스트)

### 배치 결정

- ChromaDB도 Redis와 같은 패턴 — **NCP 일반 서버 인스턴스 위에 Docker로 셀프 호스트**
- Chroma Cloud(매니지드)는 베타 단계 + NCP와 다른 리전이라 latency↑로 채택 안 함
- 별도 매니지드 vector DB(Qdrant Cloud / Pinecone / Weaviate Cloud)는 코드 변경 필요해 채택 안 함
- Redis와 같은 NCP 서버에 docker compose로 함께 띄우는 것을 1순위 — 트래픽 작은 초기에 운영 일원화
- 트래픽/메모리 압박 보이면 후속에 ChromaDB만 별도 서버로 분리

### 운영 docker-compose 예시 (인프라 팀 참고용)

Redis와 같은 서버에 함께 띄울 경우:

```yaml
services:
  redis:
    # debate-runtime-infrastructure-plan.md 참고

  chroma:
    image: chromadb/chroma:latest
    container_name: tickertaka-chroma-prod
    restart: unless-stopped
    command: ["run", "--host", "0.0.0.0", "--port", "8080"]
    ports:
      - "8080:8080"  # 사설망 운영이면 bind 사설 IP만
    volumes:
      - /var/lib/tickertaka/chroma:/chroma/chroma
    environment:
      - IS_PERSISTENT=TRUE
      - PERSIST_DIRECTORY=/chroma/chroma
      # 토큰 인증 (운영 권장)
      - CHROMA_SERVER_AUTHN_PROVIDER=chromadb.auth.token_authn.TokenAuthenticationServerProvider
      - CHROMA_SERVER_AUTHN_CREDENTIALS=${CHROMA_TOKEN}
    healthcheck:
      # ChromaDB 0.6+ 부터 v1 API deprecated → v2 사용
      test: ["CMD", "curl", "-f", "http://localhost:8080/api/v2/heartbeat"]
      interval: 30s
      timeout: 5s
      retries: 3
```

### 운영 시 결정 항목

1. **영속 볼륨**
   - ChromaDB 0.5+ 는 SQLite + parquet 조합으로 디스크 저장
   - 호스트 디렉토리 마운트(`/var/lib/tickertaka/chroma`) 필수
   - 컨테이너 재기동 시 데이터 유지
2. **인증**
   - ChromaDB 기본은 인증 없음 → 공인망 노출 시 위험
   - 사설망(VPC 내부)이면 인증 생략 가능 (관행)
   - 공인망 노출 시 **token authn 필수** (`CHROMA_SERVER_AUTHN_PROVIDER` + `CHROMA_SERVER_AUTHN_CREDENTIALS`)
   - 클라이언트는 `chromadb.HttpClient(host=..., headers={"X-Chroma-Token": "..."})` 형태로 전달
3. **메모리 / 디스크 추정**
   - 본 프로젝트 추정:
     - News + Filing 본문 약 종목당 ~30 문서 × 500 토큰 × 1536차원(float32) = ~6KB/문서
     - 100종목 = 3,000 문서 × 6KB ≈ 18MB 벡터 데이터
     - 메타데이터 + 인덱스 + 본문 텍스트 별도 ≈ 100~200MB
   - **메모리 1GB / 디스크 5GB 인스턴스로 충분** (여유 포함)
   - 1만 종목 확장 시 ~2GB 벡터 + ~2GB 인덱스 예상 → 메모리 4GB로 증설
4. **포트 / 네트워크**
   - 사설망: `bind` 사설 IP만 listen
   - 공인 IP: NCP ACG에서 API 서버 IP만 허용
   - 운영 시 HTTPS 종결(nginx 등)을 앞에 둘 수도 있으나 초기는 사설망 평문 권장
5. **백업**
   - `/var/lib/tickertaka/chroma` 디렉토리 통째로 백업
   - **운영 중 백업 시 ChromaDB 일시 정지 권장** (SQLite write lock 회피)
   - 또는 ChromaDB 자체 snapshot API 활용 (0.5+ 일부 버전)
   - 백업 대상: NCP Object Storage 등 외부 저장소
6. **모니터링**
   - `/api/v2/heartbeat` 엔드포인트 ping (healthcheck에 이미 포함, v1은 0.6+ deprecated)
   - 디스크 사용량 추적 (성장 곡선)
   - 컬렉션별 document 수 (`collection.count()`)
   - 임베딩 호출 횟수 / 비용

### 로컬 개발 환경과의 분리

- 로컬은 기존 `docker-compose.yml`의 `chromadb/chroma:latest` 그대로 사용 (인증 없음, 영속화는 `chromadata` 볼륨)
- 운영 서버는 위의 운영 compose로 띄움 (인증 + 영속 디렉토리 + healthcheck)
- API 서버는 `.env`(운영) / `.env.local`(로컬 개발) 중 어느 한 곳의 `CHROMA_URL`/`CHROMA_TOKEN`을 읽어 자동 분기

### `.env` 운영/로컬 분리 예시

```bash
# 로컬 개발 (.env.local — git 무시)
CHROMA_URL=http://localhost:8080

# 운영 서버 (.env — git 무시, 운영 서버에만 배치)
CHROMA_URL=http://10.0.x.x:8080
CHROMA_TOKEN=STRONG_RANDOM_TOKEN
```

### 코드 측 영향

거의 없음. ChromaDB Python client는 다음과 같이 환경 변수에서 URL/토큰을 받아 처리:

```python
import chromadb
client = chromadb.HttpClient(
    host=settings.chroma_url,
    headers={"X-Chroma-Token": settings.chroma_token} if settings.chroma_token else None,
)
```

`app/config.py`에 `chroma_token: str = Field(default="", alias="CHROMA_TOKEN")` 한 줄만 추가하면 됨.

### 인프라 팀에 요청할 정보

- ChromaDB 호스트(IP/도메인) / 포트
- 토큰 (또는 인증 없는 사설망 운영 여부)
- 사설망 내부 IP인지 공인 IP인지
- 영속 디렉토리 마운트 경로 (백업 운영용)
- Redis와 같은 서버 공존 여부 (자원 분배)

## 구현 단계

### Phase 1. ChromaDB wrapper + 임베딩 API

목표:
- `app/external/chroma_client.py` — `get_collection(name)` / `upsert(documents)` / `query(query_text, where, k)` / `delete(ids)`
- `app/external/embedding.py` — OpenAI 임베딩 호출 + 재시도

산출물:
- `scripts/validate_chroma_connection.py` — chroma ping + collection 생성 + upsert/query/delete

### Phase 2. NewsCache adapter

목표:
- `NewsIngestionService.sync_news_for_ticker` 끝에 신규/갱신 row를 ChromaDB에 upsert
- `news_repo.delete_*` / cleanup 호출 직후 ChromaDB delete

산출물:
- `app/domain/evidence_indexing.py`
- `scripts/validate_evidence_indexing_news.py` — sync 후 collection 내 document 수 확인

### Phase 3. FilingCache adapter

Phase 2와 동일 구조, collection만 `filing`.

### Phase 4. Retrieval API

**호출자는 a543ff1 커밋에서 이미 준비됨** — `app/agents/tools/evidence_tools.py`의 `search_evidence(query, symbol, top_k)` 함수가 현재 더미(`return []`). 본 Phase는 그 더미를 실제 ChromaDB 검색으로 대체하는 작업.

목표:
- 토론 도메인이 호출하는 `retrieve_evidence(symbol, category, k)` 함수
- 또는 기존 `evidence_tools.search_evidence`의 더미 자리에 직접 채워 넣기 (시그니처 호환 유지)
- LangGraph bull/bear 노드에서 사용 (현재 노드 코드 무수정으로 적용 가능)

기존 구현 활용:
- `evidence_tools.search_evidence`의 시그니처(`query, symbol, top_k=3`)는 그대로 유지
- 반환 형태는 `list[dict]` — `{source_type, excerpt, source_url, news_cache_id, filing_cache_id, ...}` 등 evidence 영구화 페이로드와 호환
- `app/repositories/debate_repo.py`의 `save_evidence`가 다음 외래 키들을 받음:
  - `news_cache_id`, `filing_cache_id`, `price_cache_id`, `financial_cache_id`, `technical_indicator_cache_id`
  - 즉 retrieval 결과의 metadata에 cache row id를 담아주면 evidence 영구화가 자동 동작

retrieval 결과 형식 (debate_repo와 호환):
```python
{
    "source_type": "NEWS",                  # SourceType ENUM
    "excerpt": "본문 청크 (ChromaDB document)",
    "source_url": "https://...",
    "source_title": "기사 제목",
    "source_label": "언론사명",
    "news_cache_id": "uuid",                # ChromaDB metadata.source_id
    # filing의 경우 filing_cache_id 등
}
```

## 검증/보완 메모 (2026-05-22)

1. 현재 문서는 여전히 옵션 A 흔적이 남아 있다. `News/Filing` 본문이 PostgreSQL에 있다는 가정 대신, 최신 결정은 `ChromaDB가 본문 SOT`이므로 `news-cache-policy-revision-plan.md` 기준으로 아키텍처 설명을 조정해야 한다.
2. ChromaDB collection id 정책은 `cache_row_uuid` 또는 `uuid:chunk:n` 두 방식을 모두 열어두고 있는데, 옵션 B에서 "본문 보유 기사 10건"을 단일 청크 우선으로 운영한다면 초기 구현은 `row id = document id` 1:1로 단순화하는 것이 구현/cleanup 모두 쉽다.
3. embedding provider를 OpenAI direct로 적어두었는데, 비용/운영 관점에서 `batch embedding` 지원 여부와 rate limit 실패 시 retry/backoff 정책이 빠져 있다. 초기 구현 전 이 부분을 wrapper 설계에 넣는 편이 좋다.
4. `동기 임베딩`을 초기 선택으로 둔 것은 현재 background task 구조와 맞지만, watchlist 등록 직후 뉴스/가격/재무/공시가 병렬로 늘어나면 task 시간이 길어질 수 있다. 따라서 "동기 upsert + 추후 비동기 reindex 전환 가능" 정도의 migration path 메모가 있으면 좋다.
5. retrieval 단계의 `top 5 / top 3 / top 2`는 출발점으로 좋지만, category별 source mix를 강제할지 여부가 아직 없다. 예를 들어 market category에서 filing이 거의 안 나올 수 있으므로, source quota를 둘지 후처리에서 fallback을 둘지 결정이 필요하다.
6. Chroma 백업을 운영 항목으로 적어둔 것은 맞지만, 옵션 B 채택 후에는 이 백업이 단순 권장이 아니라 필수다. 구현 plan의 닫힘 기준에도 "백업/복구 검증 1회"를 포함하는 게 현실적이다.

산출물:
- `app/domain/evidence_retrieval.py`
- `scripts/validate_evidence_retrieval.py` — 알려진 기사로 의미 검색 품질 점검

### Phase 5. Cleanup 동기화

목표:
- Cache cleanup sweep과 ChromaDB delete가 1:1 동기화

산출물:
- 각 `*CacheSchedulerService.run_*_cleanup`이 삭제된 ID 리스트를 evidence_indexer에 전달

## 관측성과 로그

최소 구조화 로그:
- collection별 document 수 (주기적)
- 임베딩 호출 건수 / 비용 추정
- 청크 평균 크기 / 청크 수
- 검색 응답 latency (p50/p95)
- delete 동기화 누락 건수 (PostgreSQL row 없는데 ChromaDB에는 남은 케이스)

추가 운영 지표:
- 토론 evidence로 채택된 document 분포 (어떤 source가 자주 선택되는지)
- 검색 결과 빈 응답 비율 (cache가 비어 있는 종목)

## 향후 확장 후보

- 하이브리드 검색: BM25(키워드) + dense embedding 결합 + rerank
- Cross-encoder rerank: top-K를 LLM이나 reranker로 재정렬
- 다국어 모델 도입: 영문 기사 / 해외 종목 지원
- 임베딩 모델 self-host 이전 (비용)
- relevance feedback (사용자가 평가한 evidence를 가중치로 활용)
- 시간 가중 검색: 최근 기사에 가산점
- collection 단위 partition: 종목 수가 1만 이상으로 늘면 symbol prefix 단위로 collection 분할

## 결론

확정 내용 (옵션 B 채택 반영):
- ChromaDB self-host (NCP 서버 + Docker), **본문 + 검색 SOT** 역할
- PostgreSQL은 메타데이터 truth, `content`는 항상 NULL
- `news_cache.id` (UUID) ↔ ChromaDB `document.id` 1:1 매핑 (단일 청크 우선)
- collection 단위는 데이터 타입(news/filing), symbol은 metadata filter
- 임베딩 모델 OpenAI `text-embedding-3-small`로 시작, batch + retry/backoff 필수
- 청킹은 단일 청크 우선, 1만자 초과 시에만 분할
- 인덱싱은 cache sync 직후 동기 upsert (옵션 A) — 부담 시 비동기 reindex로 migration
- TTL/삭제는 cache cleanup과 동시 ChromaDB delete (fail-soft + reconcile 스크립트)
- 검색은 카테고리별 query → top-K → metadata filter → source quota/fallback → LangGraph context 주입
- 본문 retrieval은 ChromaDB `get(ids=[evidence_id])` — PG에는 본문 없음
- Phase 1~5 순차 구현, Phase 4부터는 토론 도메인 plan과 연계

## 닫힘 기준 (plan 종료 시점에 검증되어야 할 항목)

본 plan은 코드 구현만으로 닫히지 않는다 — 옵션 B 채택으로 ChromaDB가 본문 SOT가 되었기 때문:

1. `app/external/chroma_client.py` + `app/external/embedding.py` 구현 + 단위 검증
2. `app/domain/evidence_indexing.py` 구현 + News/Filing 적재 흐름 통합
3. 카테고리별 retrieval 함수 + source quota/fallback 검증
4. cleanup 동기화 + reconcile 스크립트 1회 실행 확인
5. **ChromaDB 백업/복구 절차 1회 검증** (운영 핵심 — 셀프 호스트 SOT):
   - `/var/lib/tickertaka/chroma` 디렉토리를 NCP Object Storage에 백업
   - 별도 환경에서 복구 → 컬렉션 document 수/검색 결과 동일성 확인
6. 라이브 시나리오: SK하이닉스 등 watchlist 등록 → ChromaDB upsert 확인 → 검색 결과 의미 일관성 점검
7. **`evidence_tools.search_evidence` 더미 제거 + 토론 라이브에서 evidence 흐름 검증**:
   - bull/bear 노드가 evidence를 받아 발언
   - moderator_check가 evidence 부재로 환각 판정하지 않음
   - moderator_summary가 `debate_repo.save_evidence`로 영구화 (`news_cache_id` 등 외래 키 포함)
