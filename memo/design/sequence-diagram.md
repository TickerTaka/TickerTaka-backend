# 시퀀스 다이어그램

## 문서 목적

핵심 실행 흐름을 시퀀스 기준으로 설명한다.

## 1. 관심종목 추가 및 데이터 수집

```mermaid
sequenceDiagram
    participant FE as Frontend
    participant API as Watchlist API
    participant WS as WatchlistService
    participant PG as PostgreSQL
    participant BG as BackgroundTasks
    participant NI as NewsIngestion
    participant FI as FinancialIngestion
    participant PI as PriceIngestion
    participant FL as FilingIngestion
    participant VAL as ValuationSync
    participant CH as ChromaDB

    FE->>API: POST /api/watchlists {user_id, symbol, memo}
    API->>WS: create_watchlist(user_id, symbol, memo)
    alt user 없음 / ticker 없음
        WS-->>API: UserNotFound / TickerNotFound
        API-->>FE: 404
    else 이미 등록 (uq_watchlist)
        WS-->>API: WatchlistAlreadyExists / IntegrityError
        API-->>FE: 409
    else 정상
        WS->>PG: insert watchlist (commit)
        PG-->>WS: ok
        WS-->>API: watchlist row
        API->>BG: add_task ×5 (news/financial/price/filing/valuation)
        API-->>FE: 201 {watchlist, sync_enqueued=true}

        Note over BG: FastAPI BackgroundTasks (응답 후 순차 실행, 별도 큐 아님)
        BG->>NI: sync_watchlist_news(symbol)
        NI->>PG: upsert news_cache (source_url unique dedup)

        BG->>FI: sync_watchlist_financials(symbol)
        FI->>PG: upsert financial_cache (fiscal_year/quarter)

        BG->>PI: sync_watchlist_prices(symbol)
        PI->>PG: upsert price_cache + technical_indicator_cache

        BG->>FL: sync_watchlist_filings(symbol)
        FL->>PG: upsert filing_cache (dart_receipt_no unique)
        FL->>CH: index filing chunks (벡터 인덱싱)

        BG->>VAL: sync_watchlist_valuation(symbol)
        VAL->>PG: update financial_cache.per/pbr (DART fallback 보정)
    end
```

> enqueue 자체가 실패하면 `sync_enqueued=false`로 응답하되 watchlist 생성은 유지(`watchlist.py:79–88`). background sync는 본 요청 트랜잭션과 분리.

## 2. 종목 상세 조회

```mermaid
sequenceDiagram
    participant FE as Frontend
    participant API as MarketData API
    participant PG as PostgreSQL

    FE->>API: GET /api/stocks/{symbol}
    API->>PG: read ticker_metadata
    API->>PG: read latest price_cache
    API->>PG: read latest financial_cache
    API->>PG: read latest technical_indicator_cache
    API-->>FE: stock detail response
```

## 3. AI 토론 실행

```mermaid
sequenceDiagram
    participant FE as Frontend
    participant API as Debate API
    participant DS as DebateExecutionService
    participant GD as RuntimeGuard(Redis)
    participant CP as Checkpoint(Redis)
    participant DG as DebateGraph(astream)
    participant DN as DataNode
    participant BR as BullNode (ReAct)
    participant BE as BearNode (ReAct)
    participant MP as Moderator(pre)
    participant MC as Moderator(check)
    participant MS as Moderator(summary)
    participant PG as PostgreSQL
    participant CH as ChromaDB
    participant LLM as Runtime LLM (settings 기반·기본 gpt-4o-mini)

    FE->>API: POST /api/debates
    API->>PG: insert debate_session (status=running)
    API->>DS: run_session(session_id, user_id, symbol, category)

    DS->>GD: try_start_session (SET NX EX 단일비행 락)
    alt 락 거절 (동일 user/symbol 진행중)
        GD-->>DS: allowed=false
        DS-->>API: DebateStartRejectedError → 409
    else 허용 (Redis 장애 시 fail-open 허용)
        GD-->>DS: allowed=true
        DS->>CP: load_checkpoint(session_id)
        alt 체크포인트 있음
            CP-->>DS: 저장된 state (재개)
        else 없음
            DS->>DS: _build_initial_state (max_rounds=3, hallucination_count=0)
        end

        DS->>DG: astream(state, recursion_limit)

        DG->>DN: data_agent
        DN->>PG: 5종 fetch (price/financial/news/filing/event)
        DN->>CH: search_evidence (실패 시 PG fallback)
        DN-->>DG: state(price/financial/evidence_context, initial_evidences)
        DG-->>DS: chunk → merge_state → save_checkpoint

        DG->>MP: moderator_pre
        MP->>LLM: invoke
        MP-->>DG: agenda[3] (논점 3개)
        DG-->>DS: chunk → save_checkpoint

        loop 주제별 4턴 × 3주제 (_NUM_TOPICS=3)
            Note over DG,MC: turn 1·3 → bull, turn 2·4 → bear
            DG->>BR: bull_agent
            BR->>LLM: invoke (+ search_evidence tool)
            BR-->>DG: bull statement (+evidences)
            DG->>MC: moderator_check
            MC->>LLM: invoke
            MC-->>DG: moderator_flag = ok | intervene | end
            Note over DG: _router 분기<br/>intervene→직전 화자 재발언<br/>hallucination_count≥2→강제 summary<br/>topic≥3 or flag=end→summary
            DG-->>DS: chunk → save_checkpoint
        end

        DG->>MS: moderator_summary
        MS->>LLM: invoke
        MS->>PG: save statements + moderator_summary
        Note over MS: asyncio.create_task → RAGAS 사후평가 (4절, 비차단)
        MS-->>DG: summary_content + key_points
        DG-->>DS: final state

        alt 그래프 예외
            DS->>PG: update_session_status(failed, error)
            DS-->>API: raise
        else 정상
            DS->>GD: end_session (락 해제)
            DS-->>API: completed state
        end
        API->>PG: read session + statements + summary
        API-->>FE: 201 + DebateSessionResponse
    end
```

> **그래프 엣지(`debate_graph.py:60–66`)**: `START → data_agent → moderator_pre → bull_agent → moderator_check`, 그리고 `bear_agent → moderator_check`. `moderator_check`에서만 `add_conditional_edges(_router)`로 분기하고, `moderator_summary → END`.
> **`_router`(`:22–47`) 분기 우선순위**: ① `hallucination_count ≥ 2` → summary(강제종료) → ② `moderator_flag == "end"` → summary → ③ `current_topic_index ≥ 3` → summary → ④ `flag == "intervene"` → 직전 화자 재발언 → ⑤ 평상시 `turn∈{2,4}` → bear, `{1,3}` → bull.
> **상태 키**: `moderator_flag`(ok/intervene/end), `hallucination_count`, `current_turn`, `current_topic_index`, `agenda`, `statements`, `summary_content`, `key_points` (`debate_service._build_initial_state`).
> **복원력**: 매 노드 청크마다 `merge_state` 후 `save_checkpoint`(Redis 24h TTL, fail-soft) → 중단 시 `load_checkpoint`로 재개. 가드는 fail-open(Redis 장애 시 토론 허용).
> **LLM**: `llm_factory.get_llm(role)`이 role별 `settings.{bull,bear,moderator,fallback}_model`을 선택(기본 `gpt-4o-mini`). 현재 공급자는 OpenAI 직접(`openai_api_key`, base_url 미지정) — sLLM/OpenRouter 전환은 이 base_url 재배선이 진입점이라, 다이어그램은 특정 모델에 고정하지 않는다.

## 4. 토론 사후 평가

> ⚠️ **이 절은 RAGAS 1차 구현 기준이며 유동적이다.** 평가 모델·메트릭·영속화 방식이 바뀔 수 있다(현재 메트릭 2종, 결과 로그만, 배치/리포트 미완). 아래 모델명/메트릭은 *현재값*이며 확정 사양이 아니다.

```mermaid
sequenceDiagram
    participant MD as Moderator(summary)
    participant EV as DebateEvaluation
    participant RAGAS as RAGAS
    participant LLM as Eval LLM (현재 OpenRouter sLLM)

    MD->>EV: asyncio.create_task(summary eval)
    MD->>EV: asyncio.create_task(evidence eval)
    EV->>RAGAS: evaluate(faithfulness / context_precision)
    RAGAS->>LLM: judge calls (run_in_executor)
    RAGAS-->>EV: score
    EV-->>MD: log result (결과 영속화는 미완)
```

- 현재값(변경 가능): 평가 LLM = `openai/gpt-oss-120b:free`(OpenRouter, `debate_evaluation.py:45`), 메트릭 = `faithfulness`·`context_precision`.

## 현재 시퀀스 상 주의 포인트

- watchlist background sync는 병렬 큐 시스템이 아니라 FastAPI background task 기반
- debate API는 현재 요청-응답형 완료 경로
- SSE streaming은 추후 별도 시퀀스로 확장 예정
- **RAGAS 평가는 1차 구현 단계** — 현재 로그 기반 사후 평가이며 결과 영속화·배치·리포트는 미완(메트릭/모델 변경 가능)
