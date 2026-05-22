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
