# Vector DB (ChromaDB) + Evidence Retrieval 계획

## 목표

토론 단계에서 cache 테이블들(`news_cache`, `filing_cache`, 추후 `financial_cache`)에 적재된 텍스트를 **의미 기반(semantic)으로 검색**하여 evidence 후보를 추출한다.

핵심 원칙:
- PostgreSQL은 *원본* 저장, ChromaDB는 *의미 검색* 색인 — 둘은 1:1 동기화.
- 토론은 카테고리(technical/financial/market) × 3 라운드 × Bull/Bear/Judge 구조 → 각 발언당 evidence 1개 이상 강제 → 검색 품질이 토론 품질 직결.
- BM25 같은 키워드 검색은 종목명이 너무 자주 등장하는 한국 기사 특성상 약하므로 dense embedding 우선.
- 영구성/대용량 운영은 후속 단계, 초기 구현은 단일 ChromaDB 인스턴스로 시작.

## 검증 완료 내용

확인 완료:
- `docker-compose.yml`에 `chromadb/chroma:latest` 컨테이너 정의 (port 8080, volume `chromadata`)
- `.env.example`에 `CHROMA_URL` 환경 변수 있음
- 영구 저장은 `chromadata` 볼륨에 위임 (compose에 정의됨)
- ChromaDB 0.5+ 기준 client 사용 가능

추가 검증 필요:
- 클라우드 운영 시 ChromaDB 호스팅 결정 (self-host vs Chroma Cloud vs 대안 — Qdrant/Weaviate)
- 본 plan은 self-host (docker-compose) 기준

## 아키텍처

```
PostgreSQL                          ChromaDB
─────────────                       ───────────────
news_cache    ───[adapter]───►      collection: news
filing_cache  ───[adapter]───►      collection: filing
financial_cache (선택)──────►       collection: financial
                                    
                                    (각 document = 1개 cache row, id = cache row UUID)
```

원칙:
- Cache row 적재 시 → 동기 또는 비동기 임베딩 upsert
- Cache row 삭제 시 → 같은 ID로 ChromaDB delete
- Cache row content NULL 처리(보유 상한 초과) → ChromaDB document도 삭제

## 임베딩 대상

| Cache | Document 텍스트 | 우선순위 |
|---|---|---|
| NewsCache | `title` + `\n\n` + `content`(있으면) 또는 `summary` | 1순위 |
| FilingCache | `filing_title` + `\n\n` + `content`(있으면) | 1순위 |
| FinancialCache | 핵심 수치를 자연어 직렬화 (예: "2024Q3 매출 X억, 영업이익 Y억...") | 후속 |
| PriceCache / TechnicalIndicatorCache | 임베딩 대상 아님 — 수치 검색은 SQL이 더 적합 | — |

청킹 정책:
- News/Filing 본문이 짧으면 (예: 1500자 이하) 단일 document
- 그 이상이면 ~500 토큰 청크 + 50 토큰 overlap
- 청크 ID는 `{cache_row_uuid}:chunk:{idx}` — 다중 vector / 동일 source 표현

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
비용/속도 검증 후 옵션 B로 이전 검토.

구현 위치:
- `app/external/chroma_client.py` — ChromaDB wrapper
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

5. 결과 후처리
   - 중복 source 제거
   - 출처 다양성 ranking (news clustering 등)
   - LangGraph context에 주입

6. 발언 시 evidence_id로 PostgreSQL 원본 lookup → 화면에 표시
```

## TTL / 삭제 동기화

PostgreSQL과 ChromaDB는 1:1이어야 함. 동기화 지점:

- **삭제**:
  - cache row 삭제 (TTL 만료, row 상한 초과) → 같은 ID로 `collection.delete(ids=[...])`
  - cleanup sweep에서 SQL delete 직후 동일 ID 리스트로 ChromaDB delete 호출
- **content NULL 처리**:
  - cache row의 `content`가 NULL이 되어도 메타데이터는 남음
  - 그러나 본문이 없으면 의미 검색 가치가 없음 → ChromaDB document는 삭제
  - 다음 sync에서 본문 보강되면 다시 임베딩
- **content 갱신**:
  - 정정 공시나 본문 보강으로 content가 바뀌면 ChromaDB document upsert로 덮어쓰기

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
      test: ["CMD", "curl", "-f", "http://localhost:8080/api/v1/heartbeat"]
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
   - `/api/v1/heartbeat` 엔드포인트 ping (healthcheck에 이미 포함)
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

목표:
- 토론 도메인이 호출하는 `retrieve_evidence(symbol, category, k)` 함수
- LangGraph node에서 사용

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

확정 내용:
- ChromaDB self-host (docker-compose), 1:1 동기화로 PostgreSQL 보완
- collection 단위는 데이터 타입(news/filing), symbol은 metadata filter
- 임베딩 모델 OpenAI `text-embedding-3-small`로 시작
- 청킹은 1500자 초과 시 ~500 토큰 + 50 overlap
- 인덱싱은 cache sync 직후 동기 upsert (옵션 A)
- TTL/삭제는 cache cleanup과 동시 ChromaDB delete
- 검색은 카테고리별 query → top-K → metadata filter → LangGraph context 주입
- Phase 1~5 순차 구현, Phase 4부터는 토론 도메인 plan과 연계
