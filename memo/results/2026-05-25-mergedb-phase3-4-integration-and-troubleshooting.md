# `mergedb` 수동 병합 이후 Stage 3 완료 및 Stage 4 진행 결과

## 목적

`mergedb` 브랜치에서 `plandb` 기반 구현 위에 `origin/hc`의 filing 구현을 수동 통합한 이후,

- Stage 3.3 filing-cache 완료
- Stage 4 토론 runtime / debate API 구현
- live API smoke 및 end-to-end 토론 실행

까지의 전체 과정과 트러블슈팅을 한 문서에 정리한다.

이 문서는 외부 검증자가 **수동 병합 정합성**, **Stage 3 완료 여부**, **Stage 4 진행도**, **남은 운영 품질 이슈**를 한 번에 확인하는 용도다.

---

## 1. 범위 요약

### 수동 병합 범위

- 기준 브랜치: `mergedb`
- 기준 상태: `plandb`에서 Stage 1~3.2 완료
- 흡수 대상: `origin/hc`의 filing-cache 관련 구현

### 병합 이후 진행 범위

- Stage 3.3 filing-cache 완료
- Stage 4 retrieval / intraday quote / llm cache / checkpoint / runtime guard / debate API 구현
- local validation scripts 재검증
- live `uvicorn + curl` smoke
- OpenRouter live debate 실행 확인

---

## 2. 수동 병합 결과

### 채택 원칙

- `dart`, `watchlist`, `evidence_indexing`, `chroma_client`는 현재 `mergedb` 구조 유지
- `hc`의 filing 도메인 로직은 최대한 그대로 흡수
- 충돌 파일은 현재 공용 인프라를 유지하고 filing 기능만 최소 수정으로 추가

### 핵심 반영 파일

- `app/external/dart/client.py`
- `app/external/dart/__init__.py`
- `app/repositories/filing_cache_repository.py`
- `app/domain/filing_ingestion.py`
- `app/domain/evidence_indexing.py`
- `app/domain/watchlist_service.py`
- `app/api/watchlist.py`
- `scripts/validate_dart_filing_ingestion.py`
- `scripts/validate_filing_evidence_retrieval.py`

### 결과

- `watchlist -> filing ingestion -> filing indexing` 경로 연결 완료
- filing metadata는 PG에 `content=NULL`, `summary=NULL` 정책 유지
- Chroma `filing` 컬렉션 indexing 경로 추가

관련 상세 문서:
- `memo/plans/filing-cache-manual-merge-plan.md`
- `memo/results/2026-05-23-stage3-filing-cache-manual-merge.md`

---

## 3. Stage 3 완료 판단

### 완료된 범위

- 3.1 `price-cache`
- 3.2 `financial-cache`
- 3.3 `filing-cache` 수동 통합 1차

### 확인된 검증

#### filing ingestion

```json
{"symbol":"000020","fetched":3,"inserted":1,"updated":1,"skipped":1,"final_rows":2}
```

#### filing evidence retrieval

```json
{"symbol":"000020","scanned_rows":1,"indexed_rows":1,"skipped_rows":0,"failed_rows":0,"collection_count":1,"fetched_id":"4a792d31-19f2-4624-b825-aca831463b29"}
```

#### watchlist filing background trigger

- `validate_watchlist_flow`에서 `filing_background_trigger` PASS 확인

### 판단

Stage 3은 **핵심 구현 범위 기준 완료**로 본다.

단, filing 전용 scheduler / cleanup은 이번 범위 밖으로 남겨두었다.

---

## 4. Stage 4 구현 범위

### 4.1 Retrieval foundation

- `app/domain/evidence_retrieval.py`
- `app/agents/tools/evidence_tools.py`
- `app/repositories/news_cache_repository.py`
- `app/repositories/filing_cache_repository.py`
- `scripts/validate_evidence_retrieval.py`

구현:
- Chroma `news` / `filing` semantic retrieval
- PG cache row 재매핑
- bull / bear tool의 실제 `search_evidence()` 연결

검증:

```json
{"symbol":"000020","hit_count":2,"source_types":["DART","NEWS"],"top_titles":["분기보고서 제출","테스트 뉴스 제목"]}
```

### 4.2 data_node evidence prefetch

- `data_node`에서 카테고리별 evidence prefetch
- `evidence_context`를 프롬프트에 주입
- retrieval 결과 기반 `news_chunks` 우선 구성

### 4.3 intraday quote cache

- `app/external/quote_client.py`
- `app/domain/intraday_quote.py`
- `scripts/validate_intraday_quote.py`

검증:

```json
{"symbol":"000660","initial_source":"fake","cache_hit_calls":1,"stale_refresh_calls":2,"ttl_keys":["quote:latest:000660"]}
```

### 4.4 LLM cache

- `app/core/llm_cache.py`
- `app/core/llm_factory.py`
- `scripts/validate_llm_cache.py`

검증:

```json
{"cache_hit":true,"inner_calls":1,"stored_keys":1,"ttl":3600}
```

### 4.5 checkpoint

- `app/agents/debate_checkpoint.py`
- `test_debate.py`
- `scripts/validate_debate_checkpoint.py`

검증:

```json
{"session_id":"session-123","saved":true,"round_order":1,"cleared":true}
```

### 4.6 active guard / rate limit

- `app/core/debate_runtime_guard.py`
- `scripts/validate_debate_runtime_guard.py`

검증:

```json
{"active_guard":"active_session","daily_tokens":50,"daily_debates":1,"rate_limit_reason":"daily_debate_limit"}
```

### 4.7 debate service / debate API

- `app/domain/debate_service.py`
- `app/api/debate.py`
- `app/schemas/debate.py`
- `app/main.py`
- `scripts/validate_debate_service.py`
- `scripts/validate_debate_api.py`

검증:

```json
{"session_id":"session-1","summary":"요약","statement_count":1}
```

---

## 5. live API smoke / end-to-end 토론 실행

### 5.1 health check

`GET /health`는 `200 OK` 확인.

### 5.2 debate API create

실사용 유저 UUID와 symbol로 `POST /api/debates` 호출.

초기에는 여러 실패가 있었으나, 최종적으로:

- `201 Created`
- `session_id`
- `summary_content`
- `key_points`
- `statements`

까지 반환되는 상태를 확인했다.

즉 **debate API end-to-end 경로는 실제로 동작 확인됨**.

---

## 6. 트러블슈팅 기록

### A. debate enum 저장값 불일치

증상:
- DB enum 컬럼에 `FINANCIAL`, `RUNNING` 같은 enum name이 저장되려 하면서 실패

원인:
- SQLAlchemy enum이 DB value가 아니라 Python enum name을 쓰고 있었음

수정:
- `app/models/debate.py`
- `values_callable` 추가로 DB에 `financial`, `running` 저장되도록 수정

### B. `OPENROUTER_API_KEY` 미설정 시 500

증상:
- live debate 실행 시 내부 예외가 500으로 노출

수정:
- `app/core/llm_factory.py`
- `app/api/debate.py`
- 키 미설정 시 `503` + `session_id` + 실패 사유 반환하도록 수정

### C. OpenRouter 모델 ID 404

증상:
- `deepseek/deepseek-r1:free`에 대해 `No endpoints found`

원인:
- 현재 계정/시점에서 해당 free endpoint 미가용

조치:
- `.env` 모델을 `openrouter/auto`로 교체

### D. Redis 미기동

증상:
- `Error 111 connecting to localhost:6379`

원인:
- debate runtime guard / cache / checkpoint가 Redis를 요구

조치:
- 로컬 Redis 컨테이너 기동 후 `PONG` 확인

### E. debate graph recursion limit

증상:
- `GRAPH_RECURSION_LIMIT`

원인:
- 초기에 bull/bear agent 오류가 반복되며 그래프가 종료 조건 없이 오래 순환
- 기본 recursion limit 25가 너무 낮았음

수정:
- `app/config.py`에 `DEBATE_GRAPH_RECURSION_LIMIT`
- `app/domain/debate_service.py`
- `test_debate.py`
- live 실행 경로에 명시적으로 recursion limit 전달

### F. `CachedChatModel`에 `bind_tools` 없음

증상:
- `CachedChatModel` object has no attribute `bind_tools`

원인:
- `create_react_agent()`가 도구 바인딩 지원을 기대

수정:
- `app/core/llm_cache.py`
- `bind_tools()` 및 `__getattr__()` 추가

### G. `CachedChatModel`이 LangChain Runnable이 아님

증상:
- `Expected a Runnable, callable or dict`

원인:
- 캐시 래퍼가 LangChain 내부 Runnable 계약을 완전히 만족하지 않음

수정:
- `app/core/llm_factory.py`
- `get_llm(..., cached=False)` 추가
- `app/agents/nodes/bull_node.py`
- `app/agents/nodes/bear_node.py`
- tool 기반 ReAct agent 경로에서는 원본 `ChatOpenAI` 사용

### H. filing Chroma 차원 충돌

증상:
- `Embedding dimension 768 does not match collection dimensionality 64`

의미:
- `filing` 컬렉션에 과거 64차원 검증 데이터가 남아 있음
- 현재 실사용 임베딩은 768차원

현재 상태:
- `_safe_query_collection()`로 감싸 전체 토론은 계속 진행되지만
- filing evidence retrieval은 live 경로에서 누락될 수 있음

후속 조치 필요:
- `filing` 컬렉션 reset + 768차원 기준 재색인

### I. 한국 종목 yfinance fallback 실패

증상:
- `000020` quote data unavailable

원인:
- 내부 symbol `000020`를 외부 yfinance 포맷으로 변환하지 않고 그대로 전달

의미:
- `.KS` / `.KQ` suffix 매핑 필요

현재 상태:
- price cache가 비어 있으면 quote fallback이 빈약함

후속 조치 필요:
- KRX symbol -> yfinance symbol adapter 추가

---

## 7. 현재 완료/미완 상태

### 완료

- `mergedb`에서 hc filing 수동 병합 1차 완료
- Stage 3 핵심 범위 완료
- `price_cache` sync 직후 `financial_cache` 최신 분기 row의 `PER/PBR` 거래일 1회 재계산 경로 추가
- Stage 4 runtime 인프라 1차 완료
- `uvicorn + curl` 기준 debate API end-to-end 동작 확인

### 남은 품질 보정

- `filing` Chroma 차원 충돌 정리
- 한국 종목 quote fallback suffix 매핑
- bull/bear 프롬프트 품질 보정
  - 근거 없는 추정 발언 억제
  - moderator 반복 개입 감소

---

## 8. 검증자가 먼저 볼 문서

1. `memo/process/plan-implementation-order.md`
2. `memo/plans/filing-cache-manual-merge-plan.md`
3. `memo/results/2026-05-23-stage3-filing-cache-manual-merge.md`
4. `memo/results/2026-05-23-stage3-price-financial-cache.md`
5. `memo/results/2026-05-23-stage4-evidence-retrieval-foundation.md`
6. `memo/results/2026-05-25-mergedb-phase3-4-integration-and-troubleshooting.md`

---

## 9. 검증자가 중점 확인할 파일

### filing 수동 병합

- `app/external/dart/client.py`
- `app/domain/filing_ingestion.py`
- `app/repositories/filing_cache_repository.py`
- `app/domain/watchlist_service.py`
- `app/api/watchlist.py`
- `app/domain/evidence_indexing.py`

### Stage 4 retrieval / runtime / API

- `app/domain/evidence_retrieval.py`
- `app/agents/nodes/data_node.py`
- `app/core/llm_cache.py`
- `app/core/llm_factory.py`
- `app/core/debate_runtime_guard.py`
- `app/agents/debate_checkpoint.py`
- `app/domain/debate_service.py`
- `app/api/debate.py`
- `app/models/debate.py`

### 검증 스크립트

- `scripts/validate_dart_filing_ingestion.py`
- `scripts/validate_filing_evidence_retrieval.py`
- `scripts/validate_watchlist_flow.py`
- `scripts/validate_evidence_retrieval.py`
- `scripts/validate_intraday_quote.py`
- `scripts/validate_llm_cache.py`
- `scripts/validate_debate_checkpoint.py`
- `scripts/validate_debate_runtime_guard.py`
- `scripts/validate_debate_service.py`
- `scripts/validate_debate_api.py`

---

## 10. 최종 판단

`mergedb` 기준으로:

- `hc` 수동 병합은 filing 범위에서 성공적으로 흡수됨
- Stage 3은 핵심 범위 완료
- Stage 4는 **구현 및 live API 경로 검증까지 완료**
- 현재 남은 것은 구조 미완이 아니라 **live 품질 보정 단계**

즉, 외부 검증 관점에서는:

- "수동 병합이 구조적으로 안전했는가"
- "Stage 3/4가 계획서 대비 어디까지 닫혔는가"
- "live debate가 실제 동작하는가"

에 대해 **예라고 답할 수 있는 상태**다.

---

## 검증/보완 메모 (2026-05-25, 외부 검증 종합)

본 문서에서 참조된 모든 결과 문서 / plan / 핵심 구현 파일 / 검증 스크립트를 한 번에 대조했다. 개별 보고서 하단에는 영역별 세부 검증이 추가되어 있고, 본 메모는 검증자가 요청한 다섯 가지 질문에 대한 응답을 정리한다.

### 1. 가장 큰 리스크 / 문제점

| # | 항목 | 영향 | 분류 |
|---|---|---|---|
| 1 | `filing` Chroma 컬렉션의 64d/768d 차원 충돌 | live 토론에서 filing evidence가 매번 누락됨 (검증컬렉션은 영향 없음) | **운영 품질** (reset + 재색인 스크립트로 해결 가능) |
| 2 | 한국 종목 yfinance suffix 미보정 | price_cache가 비어 있는 짧은 윈도우에서 fallback 빈약 | **보완 완료, live 재검증만 남음** |
| 3 | `try_start_session(estimated_tokens=0)` 디폴트 | daily_token cap이 사후 적용 → 첫 초과 세션은 통과 | **보완 완료, 추정값 튜닝만 남음** |
| 4 | bull/bear ReAct agent가 `cached=False`로 우회 (`llm_factory.py:51-52`) | LLM cache 히트율이 ReAct 경로에서 0 → 비용/지연 절감 미실현 | **구조 절충**, LangChain Runnable 정식 상속으로 정리 권장 |
| 5 | filing scheduler / cleanup 미구현 | TTL 만료 row가 자동 삭제 안 됨, 정정 공시 감지 없음 | **plan 범위 밖** (Stage 3.3 수동 통합 1차의 의도된 비범위) |

가장 빠르게 운영 품질을 끌어올릴 수 있는 항목은 #1 (filing 컬렉션 reset + 재색인). #2, #3은 이미 코드 반영이 끝났고 live 재검증만 남아 있다.

#### 해결 방안

##### #1. `filing` Chroma 컬렉션 차원 충돌

원인: 과거 검증 단계에서 64d deterministic embedding으로 실컬렉션 `filing`에 직접 upsert한 row가 남아 있고, 현재 운영은 768d HuggingFace/OpenAI embedding 사용. Chroma collection은 최초 upsert 시점의 dim을 고정하므로 이후 768d upsert/query 시 `Embedding dimension 768 does not match collection dimensionality 64` 발생.

**조치 절차 (운영 영향 없음, 5-10분)**:

1. 백업/확인 (선택)
   ```bash
   docker exec -it chroma chroma list  # 또는
   python -c "from app.external.chroma_client import ChromaClient; c = ChromaClient(); print(c.count('filing'))"
   ```
2. 실컬렉션 reset 스크립트 작성 — 일회성이므로 `scripts/reset_filing_collection.py` 신규
   ```python
   from app.external.chroma_client import ChromaClient, FILING_COLLECTION_NAME

   def main() -> None:
       chroma = ChromaClient()
       before = chroma.count(FILING_COLLECTION_NAME)
       chroma.delete_collection(FILING_COLLECTION_NAME)
       chroma.get_or_create_collection(FILING_COLLECTION_NAME)
       print({"before": before, "after": chroma.count(FILING_COLLECTION_NAME)})

   if __name__ == "__main__":
       main()
   ```
3. watchlist 전 종목 재인덱스
   ```python
   # scripts/reindex_all_filings.py
   from app.core.db import session_scope
   from app.domain.evidence_indexing import EvidenceIndexingService
   from app.models import Watchlist
   from sqlalchemy import select

   with session_scope() as session:
       symbols = list(session.scalars(select(Watchlist.symbol).distinct()))
       for symbol in symbols:
           result = EvidenceIndexingService(session).reindex_filing_for_symbol(symbol)
           print({"symbol": symbol, "indexed": result.indexed_rows, "failed": result.failed_rows})
   ```
4. 검증
   ```bash
   python -m scripts.validate_filing_evidence_retrieval  # 768d 재확인
   # live debate 1회 호출 후 filing evidence가 응답에 포함되는지 확인
   ```

**재발 방지**: `EvidenceIndexingService` 또는 `ChromaClient.get_or_create_collection`에서 collection metadata에 `embedding_model`/`dim`을 기록해 두고, upsert 시 mismatch면 즉시 reset 또는 raise. 단 본 조치는 1회성 데이터 정리이므로 별도 plan 작성 없이 즉시 수행 가능.

##### #2. 한국 종목 yfinance suffix 어댑터

원인: `data_node._yfinance_fallback` (`data_node.py:101-128`)이 내부 6자리 KRX 코드를 yfinance에 그대로 전달. yfinance는 `005930.KS`(KOSPI) / `247540.KQ`(KOSDAQ) 같은 suffix를 요구. `TickerMetadata.market`(`ticker.py:49-52`)에 KOSPI/KOSDAQ enum이 이미 있어 분기 가능.

**조치 (어댑터 함수 신설 + 호출부 교체, 30분)**:

1. `app/external/yfinance_symbol.py`에 헬퍼 추가하고, `quote_client` / `data_node`가 공통으로 사용
   ```python
   # app/external/yfinance_symbol.py (신규)
   from app.models import MarketType

   _SUFFIX_MAP = {
       MarketType.KOSPI: ".KS",
       MarketType.KOSDAQ: ".KQ",
   }

   def to_yfinance_symbol(symbol: str, market: MarketType | str | None) -> str:
       """KRX 6자리 코드를 yfinance 형식으로 변환. NASDAQ/NYSE/AMEX는 그대로 반환."""
       if not market:
           return symbol
       market_value = market.value if hasattr(market, "value") else str(market).upper()
       suffix = _SUFFIX_MAP.get(MarketType(market_value)) if market_value in MarketType._value2member_map_ else None
       if suffix and not symbol.endswith(suffix):
           return f"{symbol}{suffix}"
       return symbol
   ```
2. `data_node._yfinance_fallback` 호출부 및 `YFinanceQuoteClient` 변경
   ```python
   from sqlalchemy import select
   from app.core.db import session_scope
   from app.external.yfinance_symbol import to_yfinance_symbol
   from app.models import TickerMetadata

   def _yfinance_fallback(symbol):
       try:
           with session_scope() as session:
               ticker = session.get(TickerMetadata, symbol)
               market = ticker.market if ticker else None
           yf_symbol = to_yfinance_symbol(symbol, market)
           # ... 기존 yf.Ticker(yf_symbol) 로 변경
   ```
   `IntradayQuoteService` / `YFinanceQuoteClient`도 같은 변환을 거치도록 통일.
3. 검증
   ```bash
   python -c "from app.external.yfinance_symbol import to_yfinance_symbol; \
   print(to_yfinance_symbol('005930', 'KOSPI'))"  # → '005930.KS'
   python -c "from app.external.yfinance_symbol import to_yfinance_symbol; \
   print(to_yfinance_symbol('247540', 'KOSDAQ'))"  # → '247540.KQ'
   ```
4. **구현 상태**: `app/external/yfinance_symbol.py`, `app/external/quote_client.py`, `app/agents/nodes/data_node.py`까지 반영 완료.  
   남은 것은 price_cache가 비어 있는 KOSPI/KOSDAQ 종목으로 토론 시작 → `data_agent` 로그에서 yfinance fallback이 성공하는지 live 재검증하는 일이다.

**주의**: yfinance는 한국 거래소 데이터 무료 지연 제공이라 latency / fields가 일관 보장 안 됨. 본 어댑터는 *price_cache fallback* 보조 경로만 보강하는 것이며, 핵심은 watchlist 등록 직후 `sync_watchlist_prices` background task가 정상 완료되어 PG에 1년치 데이터가 적재되는 것이다.

##### #3. `estimated_tokens=0` 디폴트 → daily_token cap 우회

원인: `debate_service.run_session` 시그니처에 `estimated_tokens: int = 0` (`debate_service.py:34`), `api/debate.py:34-41`도 0 전달. `DebateRuntimeGuard.try_start_session`(`debate_runtime_guard.py:73`)은 `daily_tokens + estimated_tokens > cap` 체크라 0이면 항상 통과. 결과: 사용자가 daily_token 한도를 *이미 초과한 상태*에서 새 토론 시작 가능 → 한 세션 더 실행되어 cap을 추가로 초과한 후에야 차단.

**조치 (사전 추정값 도입, 1-2시간)**:

1. 평균 토큰 사용량 측정 — `DebateRuntimeGuard.get_usage`로 최근 N세션의 `session_input_tokens + session_output_tokens` 평균 계산해 보고, 1차 디폴트를 결정 (예: 카테고리별 평균 8k-15k).
2. `app/config.py`에 카테고리별 추정 토큰 기본값 추가.
3. `app/api/debate.py`에서 `settings.estimated_tokens_for_category(payload.category.value)`를 주입.
   ```python
   # debate_service.py
   def _estimate_tokens(category: str) -> int:
       settings = get_settings()
       per_category = settings.estimated_tokens_per_category or {}
       return per_category.get(category, settings.default_estimated_tokens_per_debate)

   # api/debate.py: run_session 호출
   await DebateExecutionService().run_session(
       ...,
       estimated_tokens=_estimate_tokens(payload.category.value),
   )
   ```
4. **구현 상태**: 설정값 및 API 주입까지 반영 완료.  
   남은 것은 한 사용자가 daily_token cap에 근접한 상태에서 토론 시도 → `daily_token_limit` 사유로 409 반환되는지 live 검증하는 일이다.

**대안 / 보완**: 매 LLM 응답 직후 `_record_usage`에서 daily_tokens가 cap을 넘으면 ReAct 루프 다음 노드 진입 전에 abort. 단 LangGraph 그래프 본체 수정 필요라 1차 인프라 범위 초과 — 사전 추정만 먼저.

##### #4. ReAct agent가 `cached=False`로 우회 → LLM cache 히트율 0

원인: 트러블슈팅 G에서 `CachedChatModel`이 LangGraph `create_react_agent`의 Runnable 계약을 깨뜨림 → 임시로 `bull_node.py:34`, `bear_node.py`에서 `get_llm("bull", temperature=0.7, cached=False)`로 우회. 결과: 토론의 비용/지연 주요 발생 경로(ReAct 5-10회 LLM 호출)에서 LLM cache 효과 없음. moderator 노드만 cache 혜택.

**조치 (LangChain 정식 상속, 0.5-1일)**:

1. `CachedChatModel`을 `BaseChatModel` 상속으로 재작성 — 위임 패턴 대신 정식 Runnable.
   ```python
   # app/core/llm_cache.py (수정)
   from langchain_core.language_models import BaseChatModel
   from langchain_core.outputs import ChatResult

   class CachedChatModel(BaseChatModel):
       inner: BaseChatModel
       role: str
       temperature_value: float  # BaseChatModel.temperature와 충돌 회피
       prompt_version: str
       cache_policy: LLMCachePolicy
       # ... pydantic fields

       def _generate(self, messages, stop=None, **kwargs) -> ChatResult:
           # 캐시 lookup → 미스면 self.inner._generate 호출 → 저장
           ...

       async def _agenerate(self, messages, stop=None, **kwargs) -> ChatResult:
           ...

       def bind_tools(self, tools, **kwargs):
           bound = self.inner.bind_tools(tools, **kwargs)
           return self.__class__(inner=bound, role=self.role, ...)

       @property
       def _llm_type(self) -> str:
           return "cached"
   ```
2. `app/core/llm_factory.get_llm`에서 `cached=True`가 ReAct 경로에서도 안전하게 동작하는지 검증.
3. `bull_node.py` / `bear_node.py`에서 `cached=False` 인자 제거 → 디폴트 사용.
4. 검증
   - `scripts/validate_llm_cache.py`가 여전히 통과
   - `python -c "from langgraph.prebuilt import create_react_agent; from app.core.llm_factory import get_llm; agent = create_react_agent(get_llm('bull'), [])"` 가 예외 없이 실행
   - live 토론 2회 실행 후 두 번째 실행 시 `llm-cache:*` 키 히트율 확인 (Redis MONITOR 또는 `INFO stats`).

**대안 (간이)**: ReAct agent 경로에서는 *response 단위 캐시* 대신 *tool 호출 결과 캐시*(같은 query/symbol 조합 → 같은 evidence)를 도입. `search_evidence` tool 자체에 Redis lookup 추가. 구조 변경이 적어 1-2시간이지만 LLM 추론 캐시는 여전히 누락.

##### #5. filing scheduler / cleanup 미구현

원인: `filing-cache-manual-merge-plan.md`이 1차 통합 범위에서 scheduler를 의도적으로 제외. 결과: `FilingCache.ttl_until` 만료 row가 자동 삭제 안 되고, watchlist 등록 직후 한 번만 적재 후 정정/신규 공시는 다음 watchlist 재등록 또는 수동 호출 시까지 누락.

**조치 (별도 plan 또는 후속 단계, 1-2일)**:

1. `app/domain/filing_cache_scheduler.py` 신설 — `price_cache_scheduler` / `financial_cache_scheduler` 패턴 재사용
   - `refresh sweep`: watchlist 종목별 `sync_filings_for_ticker(symbol, lookback_days=7)` + 직후 `reindex_filing_for_symbol(symbol)`
   - `cleanup sweep`: `repo.delete_expired_rows()` (이미 구현됨, `filing_cache_repository.py:83-101`) + Chroma metadata filter delete
2. `scripts/run_filing_cache_scheduler.py` 신설 — `--mode refresh|cleanup` 진입점
3. `app/main.py` 또는 별도 worker 프로세스에 cron / APScheduler 등록 (단 졸프 단계는 worker 분리 보류 → 수동 호출)
4. DART 일일 한도 보호: refresh sweep 시 종목당 호출은 `list.json` 1회 + 신규 receipt만 `document.xml` → API 호출량은 watchlist 종목 수 × (1 + 신규 평균). 100종목 가정 시 일 ~150회 정도로 한도 20,000 대비 안전.

**판정**: 본 항목은 plan 자체에서 비범위로 분리됐기 때문에 *지금 막아야 하는 리스크는 아니다*. 운영 진입 시점에 별도 `filing-cache-scheduler-plan.md` 작성 후 진행 권장.

---

##### 우선순위 요약 (해결 방안 기준)

1. **즉시 (당일)**: #1 filing 컬렉션 reset + 전 watchlist symbol 재인덱스 — live 토론의 filing evidence 즉시 복구
2. **당일 또는 다음 날**: #2 yfinance suffix live 재검증 + #3 daily_token cap live 재검증
3. **단기 (1-2일)**: #4 `CachedChatModel`의 `BaseChatModel` 상속 재작성
4. **운영 진입 직전 (별도 plan)**: #5 filing scheduler + NCP 인프라 이전

### 2. 계획 대비 완료 판정

| plan | 시작 | mergedb 시점 | 비고 |
|---|---|---|---|
| `news-cache-policy-revision` | 100% | 100% | Stage 2 |
| `price-cache-ingestion` | 100% | 100% | live 검증은 watchlist 트리거 시점에 가려져 부분 검증 |
| `financial-cache-ingestion` | 100% (Phase 0-3) | 100% | Phase 4(PER/PBR)는 의도된 비범위 |
| `filing-cache-ingestion` | — | 100% (Phase 0-3) | hc 수동 흡수, scheduler 비범위 |
| `filing-cache-manual-merge-plan` | 100% | 100% | 닫힘 기준 1-8 충족 |
| `vector-db-and-evidence-retrieval` | 75% | **100%** | Phase 4 retrieval 닫힘 |
| `debate-runtime-infrastructure` | 25% | **100% (1차)** | Phase 1-5 1차 구현 + live 동작 |
| 토론 도메인 plan (backfill) | 50% | **100% (1차)** | API endpoint + live 검증 |

`plan-implementation-order.md` 매트릭스 마지막 컬럼("단계 4 후 / 100%") 도달.

### 3. 구조적으로 잘된 점

1. **plan 채택 원칙이 코드에 100% 반영됨** — `app/external/dart/` 패키지 유지, `__init__.py` export 보강, `_get` retry + dart-api-count 통합, hc의 `get_corp_code_by_stock_code` 시그니처 어댑팅, `sync_watchlist_filings` 자동 reindex 트리거. plan 보완 메모(A.1~A.4, B.3, B.7, G)에서 권장한 어댑팅이 코드에 일관 적용됨.
2. **fail-soft 경계가 정확** — `_safe_query_collection`(차원 충돌 흡수), `try_start_session`의 redis_error_fail_open, `update_session_status(..., "failed", ...)` + `end_session` finally. 한 부분의 외부 의존 실패가 토론 전체를 멈추지 않음.
3. **검증 스크립트가 컬렉션 분리** — `news_validate_retrieval`, `filing_validate_retrieval`, `filing_validate_reindex` 별도 컬렉션 + 검증 끝에 `delete_collection`. 실 데이터/실 컬렉션 오염 위험 없음.
4. **`models/debate.py`의 `values_callable=_enum_values`** — Python enum name 대신 DB value 저장. 트러블슈팅 A의 fix가 SQLAlchemy 정석 패턴.
5. **`watchlist sync_enqueued` 응답 필드** — enqueue 실패 시 클라이언트가 알 수 있게 노출. 백그라운드 작업 가시성.

### 4. 남은 보완 우선순위

1. **즉시 (10-30분)**: filing Chroma 컬렉션 reset + watchlist 종목 재인덱스. `chromadb-utils` 또는 `chroma_client.delete_collection("filing")` 후 `EvidenceIndexingService.reindex_filing_for_symbol`. 검증컬렉션이 아니라 **실컬렉션** `filing`을 대상으로.
2. **즉시 (5분)**: `scripts/reset_filing_collection.py` + `scripts/reindex_all_filings.py`로 실컬렉션 `filing` 복구.
3. **단기 (1-2일)**: bull/bear ReAct agent의 cached 우회 정리. `CachedChatModel`을 LangChain `BaseChatModel` 정식 상속으로 재작성하거나, ReAct agent를 cache wrapper와 분리해 별도 cache 경로(예: 동일 도구 호출 → 동일 결과 캐싱) 도입.
4. **운영 진입 직전**: filing scheduler + cleanup (TTL 만료 row 삭제), `sync_financials_for_ticker(mode="refresh")` 호출 범위 축소(2~3분기), daily_token 사전 추정, LangGraph 공식 Redis checkpointer 도입.
5. **운영 진입 시 별도 plan**: NCP 셀프호스트 이전, Chroma 백업/복구, 인증/TLS, fail-soft + reconcile 자동화. `plan-implementation-order.md` 마지막 행("후속 production-deployment-plan")으로 자연 이월.

### 5. 단계별 판정

| 단계 | 판정 |
|---|---|
| Stage 3 | **완료** — 핵심 범위(price + financial + filing 수동 통합)가 plan 닫힘 기준 모두 충족, live 검증 1회씩 확보 |
| Stage 4 구현 | **1차 완료** — retrieval/intraday/llm cache/checkpoint/runtime guard/debate API가 모두 코드 + 검증 + live 경로 동작 확인 |
| Stage 4 live | **품질 보정 단계 진입** — 구조 미완 없음(약한 항목 1건만 어댑터 1줄). `filing` 차원 충돌과 yfinance suffix는 *코드 구조의 결함이 아니라 데이터 상태 / 경계 어댑터 누락*이라 별도 plan 작성 없이 일과 내 처리 가능 |

검증자 입장에서의 한 줄 요약: **"Stage 3 완료 + Stage 4 1차 구현 완료, 남은 작업은 live 품질 보정"이라는 본 보고서의 자기 판정은 코드/검증 스크립트/plan과 모두 정합한다.**

---

## 추가 반영 (2026-05-30, PER/PBR A안 보완)

Stage 3의 PER/PBR A안 구현 후 검토 메모에서 지적된 값 정확성 이슈를 바로 반영했다.

- 적자/자본잠식 종목에서 pykrx가 반환하는 `per/pbr == 0`은 이제 `None`으로 정규화된다. 따라서 토론/프론트에 `PER 0 = 초저평가` 같은 오해 소지가 있는 값이 그대로 노출되지 않는다.
- financial 재적재가 `per/pbr`를 `NULL`로 덮어쓰던 문제는 `FinancialCacheRepository.upsert_many()`의 update 절에서 `per/pbr`를 제거해 해결했다. 현재 valuation 값은 price sync 경로 전용으로 보존된다.
- valuation 동기화는 fundamental fetch뿐 아니라 `list_recent()` / `update_latest_valuation()`까지 모두 best-effort 예외 격리로 감싸 가격 적재 본체를 깨지 않도록 정리했다.
- 검증 스크립트 `python -m scripts.validate_price_ingestion`가 happy path와 함께 적자 케이스(`loss_case_per=None`, `loss_case_pbr=None`)까지 출력하도록 확장됐다.

판정은 유지된다.

- Stage 3: 완료
- Stage 4: 1차 구현 완료
- 남은 작업: live 품질 보정 단계
