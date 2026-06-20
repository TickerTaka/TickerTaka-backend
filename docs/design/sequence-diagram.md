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

> **그래프 엣지(`debate_graph.py:58–73`)**: `START → data_agent → moderator_pre → bull_agent → moderator_check`, 그리고 `bear_agent → moderator_check`. `moderator_check`에서만 `add_conditional_edges(_router)`로 분기하고, **`judge_agent → moderator_summary → END`**.
> **`_router`(`:27–52`) 분기 우선순위**: ① `hallucination_count ≥ 2` → 종결 → ② `moderator_flag == "end"` → 종결 → ③ `current_topic_index ≥ 3` → 종결 → ④ `flag == "intervene"` → 직전 화자 재발언 → ⑤ 평상시 `turn∈{2,4}` → bear, `{1,3}` → bull.
> **종결 분기(`_final_node` `:23–24`)**: 종결 시 `decision_agent == "judge"`면 **`judge_agent`**(승패/판정, `moderator_node.py:224`) → `moderator_summary`, 기본(`moderator`)이면 `moderator_summary` 단독. `decision_agent`는 토론 시작 요청 바디에서 선택(`POST /api/debates`·`/sessions`).
> **상태 키**: `moderator_flag`(ok/intervene/end), `hallucination_count`, `current_turn`, `current_topic_index`, `agenda`, `statements`, `summary_content`, `key_points` (`debate_service._build_initial_state`).
> **복원력**: 매 노드 청크마다 `merge_state` 후 `save_checkpoint`(Redis 24h TTL, fail-soft) → 중단 시 `load_checkpoint`로 재개. 가드는 fail-open(Redis 장애 시 토론 허용).
> **LLM**: `llm_factory.get_llm(role)`이 role별 `settings.{bull,bear,moderator,fallback}_model`을 선택(기본 `gpt-4o-mini`). 현재 공급자는 OpenAI 직접(`openai_api_key`, base_url 미지정) — sLLM/OpenRouter 전환은 이 base_url 재배선이 진입점이라, 다이어그램은 특정 모델에 고정하지 않는다.

## 4. 토론 사후 평가

> ⚠️ **이 절의 평가 모델은 유동적이다**(OpenRouter sLLM 모델명 변경 가능). 단 **결과는 `debate_eval_result` 테이블에 영속화**되고 배치(`run_ragas_eval.py`)·리포트(`ragas-<sha>.json`)·회귀테스트(`test_ragas_regression.py`)까지 구현됨. 현재 메트릭 **3종**(faithfulness·answer_relevancy·context_precision).

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
    EV->>RAGAS: (배치) run_ragas_eval.py → ragas-<sha>.json
    EV-->>MD: 결과를 debate_eval_result에 저장(지표별 1행)
```

- 현재값(변경 가능): 평가 LLM = `openai/gpt-oss-120b:free`(OpenRouter, `debate_evaluation.py:45`), 메트릭 = `faithfulness`·`answer_relevancy`·`context_precision`. 결과는 `debate_eval_result`에 저장.

## 5. 토론 결과 Notion 발행 (MCP)

```mermaid
sequenceDiagram
    participant FE as Frontend
    participant API as Debate API
    participant PG as PostgreSQL
    participant NM as notion_mcp (MCP client)
    participant MS as Notion MCP server (stdio)
    participant NT as Notion

    FE->>API: POST /api/debates/{id}/publish/notion (버튼)
    API->>PG: SELECT debate_session ... FOR UPDATE (세션 락)
    alt 세션 없음
        API-->>FE: 404
    else completed 아님 / 요약 없음
        API-->>FE: 409
    else 이미 발행됨 (notion_page_* 존재)
        API-->>FE: 200 기존 notion_page_url (멱등 — 새 생성 없음)
    else 정상 발행
        API->>PG: read summary + statements(+evidence)
        API->>NM: publish_debate(payload)
        NM->>MS: spawn(stdio) → initialize (newline-delimited JSON)
        NM->>MS: tools/call API-post-page {parent, properties, children}
        MS->>NT: create page (Notion REST 프록시)
        NT-->>MS: page id / url
        MS-->>NM: result(content/text)
        NM-->>API: page_id, page_url (응답 파싱)
        alt 발행 실패 (NotionMcpError)
            API->>PG: rollback
            API-->>FE: 502 (실제 오류 메시지, 토론 본체 보존)
        else 성공
            API->>PG: save notion_page_id/url/published_at (commit)
            API-->>FE: 200 DebateNotionPublishResponse
        end
    end
```

> **발행 본체**: `app/integrations/notion_mcp.py`가 MCP client로 self-host `@notionhq/notion-mcp-server`(stdio)를 spawn → `API-post-page` 호출. transport는 **newline-delimited JSON**(MCP stdio 규격), 타임아웃·`isError` 표면화 처리.
> **저장 위치**: 속성(session_id/symbol/category/날짜)은 Notion DB property, 요약/핵심논점/주요발언은 페이지 본문 block(paragraph·bulleted).
> **멱등성**: `debate_session.notion_page_*`에 값이 있으면 재발행 대신 기존 URL 반환. **fail-soft**: 발행 실패(502)는 토론 본체 경로에 비전파(rollback).

## 6. 뉴스/공시 감성·투자분석 (동기 baseline + 비동기 Qwen)

```mermaid
sequenceDiagram
    participant IDX as EvidenceIndexing (동기)
    participant AS as EvidenceAnalysisService
    participant FB as FinBERT (local HF)
    participant PG as PostgreSQL
    participant WK as AnalysisWorker (별도 프로세스)
    participant SC as Scraper / DART
    participant QW as Qwen sLLM

    Note over IDX: 뉴스/공시 인덱싱 시점
    IDX->>AS: analyze baseline (rule + FinBERT)
    AS->>FB: classify sentiment
    FB-->>AS: sentiment / impact baseline
    AS->>PG: upsert evidence_analysis (baseline 즉시 저장)
    alt Qwen 게이트 통과 & async 활성
        AS->>PG: enqueue analysis_jobs (status=pending)
    end

    loop 워커 폴링 (analysis_worker_poll_interval)
        WK->>PG: claim_batch(analysis_jobs)
        PG-->>WK: pending jobs (locked)
        WK->>SC: 뉴스 본문 스크랩 / DART 문서 fetch
        SC-->>WK: 원문 텍스트
        WK->>QW: 구조화 분석 enrich
        QW-->>WK: sentiment/impact/event_type/risks/key_points
        WK->>PG: update evidence_analysis + mark_done
        Note over WK: 실패 시 mark_failed (attempts/max_attempts 격리)
    end
```

> **2단 구조**: 동기 인덱싱이 FinBERT baseline을 즉시 `evidence_analysis`에 저장하므로 **워커가 안 떠 있어도 baseline 감성은 제공**된다. 무거운 Qwen 보강만 `analysis_jobs` 큐 → `python -m app.workers.analysis_worker`(별도 프로세스)가 후처리.
> **Qwen 서빙 백엔드**: `ANALYSIS_GENERATION_BACKEND=transformers`면 모델이 워커 프로세스에 1회 로드(메모리 비압박), `=remote`면 Ollama/vLLM(OpenAI 호환)을 HTTP 호출 → 워커 경량화 + 엔진 교체는 URL만.
> **관측성(Langfuse)**: 토글+키 활성 시, 위 enrich 1건이 `evidence-enrich` trace(부모 span)+Qwen generation으로 Langfuse에 기록되고 워커 종료 시 flush. 비활성이면 no-op.
> **노출**: 결과는 `GET /api/watchlists/{user_id}/feed`의 `WatchlistFeedItem` 필드(`sentiment`/`impact_score`/`event_type`/…)로 노출. 분석 전이면 null.

## 현재 시퀀스 상 주의 포인트

- watchlist background sync는 병렬 큐 시스템이 아니라 FastAPI background task 기반
- debate는 **두 경로**: ① 요청-응답형 일괄 반환(`POST /api/debates`, §3) ② **SSE 스트리밍**(`POST /api/debates/sessions`로 세션 선생성 → `GET /api/debates/{id}/stream`). 스트리밍은 §3 그래프 실행을 `astream` 노드 청크로 즉시 forward하고, 이벤트(`session_started`/`statement`/`summary`/`done`/`error`)로 내보냄. `completed` 세션은 DB replay, disconnect 시 `fail_session_if_running`으로 정리(인터페이스 정의서 §3 참조)
- **RAGAS 평가는 결과를 `debate_eval_result`에 영속화**하고 배치(`run_ragas_eval.py`)·리포트(`reports/ragas-<sha>.json`)·회귀테스트까지 구현됨. **golden set 10건 확장 완료**(평가 모델 변경 가능)
