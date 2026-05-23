# 2026-05-23 Stage 4 - Evidence Retrieval Foundation

## 범위

- 토론 Stage 4의 첫 구현으로 `search_evidence()` 더미를 제거했다.
- `news` / `filing` Chroma 컬렉션에서 semantic retrieval 후, PostgreSQL cache 메타데이터와 다시 매핑해 evidence dict로 반환한다.

## 구현 내용

### 1. Retrieval 서비스 추가

- `app/domain/evidence_retrieval.py`
  - `EvidenceRetrievalService`
  - `search_evidence_for_symbol()`
- ChromaDB query는 `symbol` metadata filter를 강제한다.
- `news`와 `filing`을 각각 조회한 뒤 score(distance) 기준으로 합쳐 `top_k`를 반환한다.

### 2. PG 메타 재매핑

- `app/repositories/news_cache_repository.py`
  - `get_by_ids()`
- `app/repositories/filing_cache_repository.py`
  - `get_by_ids()`

이 단계에서 반환하는 evidence payload는 `debate_repo.save_evidence()`와 맞춘다.

- `source_type`
- `source_title`
- `excerpt`
- `source_url`
- `source_label`
- `news_cache_id` 또는 `filing_cache_id`

### 3. 토론 tool 연결

- `app/agents/tools/evidence_tools.py`
  - 기존 더미 `search_evidence()` 제거
  - 실제 retrieval 서비스 호출로 교체

이 변경으로 bull / bear 노드의 ReAct agent는 이제 뉴스/공시 근거를 실제로 검색할 수 있다.

## 현재 설계 판단

- 이번 단계는 retrieval foundation만 우선 구현했다.
- `data_node`는 아직 기존처럼 price/financial/news/filing 컨텍스트를 조합해 프롬프트 입력을 만들고,
  근거 검색은 tool 호출 시점에 수행한다.
- intraday quote, LLM cache, checkpoint, active guard는 Stage 4 후속 범위로 남겨둔다.

## 검증

- `scripts/validate_evidence_retrieval.py`
  - 검증 전용 컬렉션 `news_validate_retrieval`, `filing_validate_retrieval` 사용
  - deterministic embedding으로 news 1건, filing 1건을 upsert
  - retrieval 결과가 두 source를 모두 반환하는지 확인

## 남은 작업

- category별 query template / source quota 세분화
- `data_node`에 retrieval 요약 직접 주입 여부 결정
- intraday quote Redis 캐싱
- LLM cache / rate limit / checkpoint
