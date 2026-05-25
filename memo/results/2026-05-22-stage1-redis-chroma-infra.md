# 2026-05-22 Stage 1 Redis/Chroma 공용 인프라 구현

## 범위

`memo/process/plan-implementation-order.md`의 Stage 1만 구현:

1. `app/core/redis.py`
2. `app/external/chroma_client.py`
3. `app/external/embedding.py`
4. `scripts/validate_chroma_connection.py`

## 구현 내용

### 1. 공용 Redis 헬퍼

- `app/core/redis.py`
  - `build_redis_client(redis_url)`
  - `get_redis()`
  - `make_key(domain, purpose, *parts)`
- 기존 `news_ingestion`, `news_cache_scheduler`가 새 헬퍼를 사용하도록 최소 연결

### 2. ChromaDB 클라이언트 래퍼

- `app/external/chroma_client.py`
  - `ChromaClient`
  - `heartbeat()`
  - `get_or_create_collection()`
  - `upsert()`
  - `query()`
  - `get()`
  - `delete()`
  - `count()`
- 컬렉션명 상수
  - `news`
  - `filing`

### 3. 임베딩 래퍼

- `app/external/embedding.py`
  - `OpenAIEmbeddingClient`
  - `HuggingFaceEmbeddingClient`
  - `DeterministicEmbeddingClient`
  - `get_embedding_client()`
- 기본 provider는 `huggingface`
- 기본 model은 `jhgan/ko-sroberta-multitask`
- OpenAI 경로는 대체 provider 용도로 유지
- deterministic 경로는 Stage 1 로컬 검증용

### 4. 연결 검증 스크립트

- `scripts/validate_chroma_connection.py`
  - heartbeat
  - collection 생성/재생성
  - 검증 전용 컬렉션 `news_validate`, `filing_validate` upsert
  - symbol metadata filter query
  - delete 검증

## 설정 변경

- `app/config.py`
  - `EMBEDDING_PROVIDER`
  - `OPENAI_API_KEY`
  - `EMBEDDING_MODEL`
- `.env.example` 동일 항목 추가
- `docker-compose.yml`
  - 로컬 Chroma 이미지를 `chromadb/chroma:0.5.23`로 고정
  - telemetry 비활성화 환경 변수 추가
  - `0.5.x` 계열 기본 포트(`8000`)를 사용하고, 호스트 `8080 -> 컨테이너 8000`으로 매핑

## 의도적 비범위

- Stage 2의 `news-cache option B` 전환은 아직 안 함
- ingestion 시점 ChromaDB direct upsert도 아직 안 함
- `reindex_local_chroma.py`는 Stage 2에서 구현 예정

## 기대 효과

- Redis/Chroma 공용 진입점을 먼저 고정
- `news`와 `filing`이 같은 로컬 ChromaDB 인스턴스를 공유하되 컬렉션만 분리하는 구조 마련
- 이후 Stage 2/3에서 도메인 로직만 얹을 수 있는 상태 확보

## 검증 상태

- `python3 -m compileall app scripts` 통과
- `python -m scripts.validate_chroma_connection` 통과
  - `heartbeat_ok = true`
  - 검증 전용 컬렉션 `news_validate` upsert/query/delete 정상
  - 검증 전용 컬렉션 `filing_validate` upsert 정상
  - symbol metadata filter query 정상 (`query_hit_id = news-doc-2`)

## 운영 메모

- Python `chromadb` client와 Docker `chroma` server는 같은 계열 버전으로 유지한다.
- `latest` 태그 사용 시 `KeyError: '_type'` 같은 schema drift가 날 수 있어 로컬 개발에서도 pinning 유지.
- 현재 프로젝트 `venv` 기준 client는 `0.5.23` 계열이므로, Docker 서버도 `chromadb/chroma:0.5.23`으로 맞춘다.
- `0.5.x` 이미지는 `1.x` CLI의 `run --host --port`를 쓰지 않는다. compose에서 별도 command 없이 기본 entrypoint를 사용하고, 외부 노출 포트만 `8080:8000`으로 맞춘다.

---

## 검증/보완 메모 (2026-05-23, plan 대조)

본 보고서가 명시한 산출물(`app/core/redis.py`, `app/external/chroma_client.py`, `app/external/embedding.py`, `scripts/validate_chroma_connection.py`)이 모두 존재하고, plan-implementation-order.md의 Stage 1 닫힘 기준(vector-db Phase 1 + debate-runtime Phase 0)을 충족한다. 다만 plan 문서들과 코드를 대조하면 다음 항목이 추가 보완 대상이다.

### 정합성 확인 (OK)

- `make_key(domain, purpose, *parts)` 시그니처가 `<domain>:<purpose>:<identifier>` 컨벤션과 일치, 기존 `news-sync:lock:{symbol}` 형식도 호환 → news_ingestion이 새 헬퍼로 잘 이전됨
- `ChromaClient.upsert / query / get / delete / count / heartbeat / get_or_create_collection / delete_collection` 인터페이스가 vector-db Phase 1 요구를 모두 충족
- 컬렉션명 상수 `NEWS_COLLECTION_NAME / FILING_COLLECTION_NAME`이 코드에 박혀 있어 Stage 2/3.3 양쪽에서 재사용 가능 — vector-db plan의 "per-type collection" 결정과 정합
- `chroma_token` 헤더 (`X-Chroma-Token`) 처리는 운영 환경의 token authn 도입 시 추가 코드 변경 없음 (vector-db plan 운영 섹션과 정합)
- 검증 스크립트가 prod 컬렉션(`news`/`filing`)을 건드리지 않고 별도 `news_validate`/`filing_validate`만 다룬 점은 안전한 정책

### 보완 필요 / 누락

1. **임베딩 wrapper의 batch / retry 정책이 provider별로 비대칭** — vector-db plan Phase 1은 "batch embedding + retry/backoff (rate limit 429/5xx 대응) + fail-soft"를 wrapper 단에서 처리하라고 권고. 현재 코드는 `OpenAIEmbeddingClient`만 `tenacity` 적용(`stop_after_attempt(3)`, exponential backoff). `HuggingFaceEmbeddingClient`는 로컬 모델이라 retry는 무관하지만, `OpenAIEmbeddingClient`도 입력 길이 기반 chunking 로직이 없어 매우 긴 텍스트 리스트 한 번에 호출 시 token limit 초과 위험. Stage 2 reindex가 단일 종목 ~10~20건 규모라 당장 문제는 없으나, 다종목 일괄 reindex 시 보강 필요.
   **권장**: `OpenAIEmbeddingClient._embed_batch`에 input 길이 기반 chunking 추가, 또는 batch size 상수화.

2. **`get_redis()`의 `@lru_cache` 캐싱이 테스트/멀티스레드 환경에서 stale client를 잡을 수 있음** — lru_cache로 단일 클라이언트 재사용. `get_settings()` 변경 후에도 이전 클라이언트가 반환됨. validate 시나리오는 대부분 `FakeRedis`를 명시 inject 하므로 현재 무관하지만, lru_cache reset 헬퍼가 없다는 점은 후속 phase에서 testability 비용을 만들 수 있음.
   **권장**: `get_redis.cache_clear()` 호출용 helper 또는 fixture 도입 검토.

3. **`ChromaClient.delete_collection`이 예외를 통째로 swallow** — idempotent 의도는 명확하지만, 운영 환경에서 권한/네트워크 오류도 동일하게 무시됨. 현재는 validate/setup 스크립트 한정이라 OK. 운영 진입 시 별도 plan에서 로깅 추가 필요.

4. **requirements.txt 핀 누락 (사용자 메모리 `requirements_pinning` 정책 위반)** — 본 Stage에서 처음 활성화되는 핵심 모듈에 버전 핀(`==`)이 없음:
   - `chromadb` (운영 메모에서 `0.5.23`으로 핀 권고했지만 requirements는 핀 없음)
   - `sentence-transformers`
   - `tenacity`
   - 그 외 `langchain-*` 계열, `slowapi`, `sse-starlette`, `asyncpg`, `celery[redis]`, `pyjwt`, `rank-bm25`도 동일

   본 운영 메모(94행 "schema drift" 방지)와 사용자 메모리 정책이 일치하므로, 본 Stage가 적어도 `chromadb==0.5.23`, `sentence-transformers==3.x`, `tenacity==9.x` 정도는 핀해 두는 게 정합.
   **권장**: Stage 2 이전에 별도 PR로 핀 정리 작업.

5. **plan 문서에 없는 추가물 1건** — `app/external/dart/__init__.py`는 본 Stage 보고서엔 없지만 (Stage 3 생성물). 본 Stage에 영향은 없음 — 참고.

6. **검증 스크립트가 `DeterministicEmbeddingClient` 사용** — 보고서 86~88행 통과 결과는 deterministic embedding 기준이라 *의미 검색 품질*을 보장하지 않음. Stage 1 닫힘 기준(연결/upsert/delete 확인)으로는 충분. 실제 임베딩 품질(한국어 ko-sroberta) 검증은 Stage 2 라이브 reindex에서 처음 노출됨 → Stage 2 라이브 검증이 사실상 Stage 1 임베딩 품질의 첫 검증 지점.

### 다음 단계 권고

- Stage 2 진행 전 requirements.txt 핀 정리 작업을 별도 PR로 분리 권장 (사용자 메모리 정책 우선)
- `OpenAIEmbeddingClient` batch chunking 보강은 Stage 2 reindex 라이브 검증 시 token limit hit 발생하면 그 시점에 추가

### 반영 완료

- `app/core/redis.py`
  - `clear_redis_cache()` helper 추가
- `app/external/embedding.py`
  - `OpenAIEmbeddingClient`에 `max_batch_size` 기반 batch chunking 추가
- `requirements.txt`
  - Stage 1 핵심 의존성 포함 일부 패키지 버전 핀 추가
  - 특히 `chromadb==0.5.23`, `sentence-transformers==3.2.1`, `tenacity==9.0.0`
