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

### 4. data_node evidence prefetch 추가

- `app/agents/nodes/data_node.py`
  - 카테고리별 query template 생성
  - 토론 시작 시 `top_k=4` evidence를 미리 검색
  - `evidence_context` 문자열로 요약해 state에 넣음
  - `news_chunks`도 retrieval 결과 기반으로 우선 구성

- `app/agents/state.py`
  - `evidence_context` 필드 추가

- `app/agents/prompts/prompts.py`
  - bull / bear / moderator prompt에 `evidence_context` 주입

이 단계로 토론 프롬프트 자체가 최신 뉴스/공시 근거를 기본 문맥으로 갖게 됐다.

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
- intraday quote Redis 캐싱
- LLM cache / rate limit / checkpoint

## 후속 진행 - Intraday Quote

- `app/external/quote_client.py`
  - `YFinanceQuoteClient`
  - `QuoteSnapshot`
- `app/domain/intraday_quote.py`
  - Redis key `quote:latest:{symbol}`
  - 장중 5분 / 장후 30분 / 주말 24시간 TTL
- `app/agents/tools/market_tools.py`
  - `get_stock_price()`가 direct yfinance 대신 `IntradayQuoteService`를 통해 시세 조회
- `app/agents/nodes/data_node.py`
  - DB에 가격 데이터가 없을 때 intraday quote + yfinance 기술지표 폴백 조합 사용
- `scripts/validate_intraday_quote.py`
  - Fake Redis / Fake QuoteClient로 cache hit + stale refresh 검증
