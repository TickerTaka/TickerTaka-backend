# Plan 구현 순서 (2026-05-22 기준, 졸프 단계 로컬 RAG 정책 반영)

## 목적

`memo/plans/`에 정리된 7개 plan의 구현 우선순위를 의존성 + 위험 분산 + 사용자 가치 측면에서 정한다.

이 문서는 *어떤 plan부터 코드 구현에 들어갈지*의 가이드이며, 각 plan의 세부 정책/스키마는 해당 plan 문서를 참조한다.

**전제 정책 ([[infra-stage-policy]])**: 졸프 단계는 배포 없음 — PostgreSQL만 공용 NCP, **Redis와 ChromaDB는 개발자별 로컬 Docker**. ChromaDB는 본문 SOT가 아니라 *재생성 가능한 RAG 인덱스* (본문 SOT는 외부 원본 — 네이버/DART). 운영 진입 시 별도 `production-deployment-plan.md`로 NCP 셀프호스트 이전 + 백업/복구 정리.

## 현재 plan 인벤토리

| plan | 상태 | 비고 |
|---|---|---|
| `news-cache-ingestion-plan.md` | **구현 완료** (main 머지) | 옵션 A 상태로 동작 중 |
| `news-cache-policy-revision-plan.md` | 신규 (옵션 B 전환) | 코드 변경 필요 |
| `vector-db-and-evidence-retrieval-plan.md` | 미구현 | 옵션 B 전환의 필수 선행 |
| `debate-runtime-infrastructure-plan.md` | 미구현 | Phase 0 공용 Redis 헬퍼는 가장 먼저 |
| `price-cache-ingestion-plan.md` | 미구현 | 독립적 (pykrx) |
| `financial-cache-ingestion-plan.md` | 미구현 | corp_code 매핑 도입 |
| `filing-cache-ingestion-plan.md` | 미구현 | corp_code 공유 + 옵션 B 패턴 재사용 |
| **토론 도메인 plan** | **미작성** | 단계 3 중간에 초안 작성 권장 |

## Plan별 닫힘 매핑 (어느 단계에서 어떤 Phase가 닫히는가)

각 plan이 단일 단계에서 한 번에 닫히지 않고, **여러 단계에 걸쳐 점진적으로** 닫힌다. 단계별로 어떤 plan의 어떤 Phase가 닫히는지 정리:

### `news-cache-ingestion-plan.md` (이미 구현 완료, 옵션 A)
- 단계 2에서 옵션 B로 전환 — 본 plan 자체는 historical record로 보존, 실제 동작은 옵션 B 정책으로 운영

### `news-cache-policy-revision-plan.md`
- **단계 2에서 닫힘** (코드 측 100%, 인프라 백업 협업 1건은 별도)
- 단계 2 후 인프라 팀의 ChromaDB 백업/복구 절차 셋업 → 운영 측면 100%

### `vector-db-and-evidence-retrieval-plan.md`
- **여러 단계에 걸쳐 분산** (Phase 1~5)
- Phase 1 (chroma_client + embedding) → **단계 1**
- Phase 2 (NewsCache adapter) → **단계 2**
- Phase 3 (FilingCache adapter) → **단계 3.3 (filing 구현 시)**
- Phase 4 (Retrieval API) → **단계 4 (토론 도메인)**
- Phase 5 (Cleanup 동기화) → **단계 2 (news 부분) + 단계 3.3 (filing 부분)** 양쪽
- 전체 마무리는 **단계 4 끝**

### `debate-runtime-infrastructure-plan.md`

**참고: 2026-05-19 커밋 a543ff1에서 LangGraph 토론 그래프 본체가 이미 구현됨** (`app/agents/debate_graph.py`, 노드 6개, DebateState, LLM Factory, debate_repo). Phase 매핑은 그 구현 위에 운영 인프라를 보강하는 관점:

- Phase 0 (공용 Redis 헬퍼) → **단계 1**
- Phase 1 (intraday quote) → **단계 4** (현재 `data_node._yfinance_fallback`이 부분 대체 중 — Redis 캐싱 없음)
- Phase 2 (LLM cache) → **단계 4** (LLM Factory 더미 tracker 자리에 결합)
- Phase 3 (rate limit) → **단계 4** (`slowapi` 이미 requirements에 있음)
- Phase 4 (LangGraph checkpoint) → **단계 4** (그래프 본체는 a543ff1 구현, checkpointer만 추가)
- Phase 5 (active guard) → **단계 4** (토론 endpoint API와 동시 도입)
- 전체 마무리는 **단계 4 끝**
- **a543ff1 기준 본 plan은 약 25% 진행된 상태** (Phase 4 그래프 본체 + Phase 1 부분)

### `price-cache-ingestion-plan.md`
- Phase 1~4 모두 **단계 3.1에서 닫힘**
- 단일 단계 완결형

### `financial-cache-ingestion-plan.md`
- Phase 0 (corp_code) + Phase 1~3 → **단계 3.2에서 닫힘**
- Phase 4 (PER/PBR — price 의존) → **후속 valuation phase**로 분리
- 현재 단계 3.2의 닫힘 기준은 **재무 원숫자 + ROE/debt_ratio + scheduler**

### `filing-cache-ingestion-plan.md`
- Phase 0 (corp_code) → financial-cache와 공유 (단계 3.2에서 이미 도입)
- Phase 1~3 → **단계 3.3에서 닫힘**
- Phase 4 (LLM 요약) → 본 plan 범위 밖 (별도 plan)
- 전체 마무리는 **단계 3.3 끝**

### 토론 도메인 plan
- **a543ff1 커밋에서 토론 에이전트 본체가 이미 구현됨** — 노드 6개 (data/moderator_pre/bull/bear/moderator_check/moderator_summary), DebateState, LLM Factory, debate_repo (asyncpg 패턴), prompts
- 따라서 토론 plan은 *새 작성*이 아니라 **현재 구현 backfill + 부족한 부분 보강** 성격
- 작성 시점: 단계 3 중간 (priceCache 구현 즈음)
- 작성 내용:
  - 현 구현 정리 (라우터 로직, 환각 카운트 강제 종료 등)
  - 부족한 부분 (evidence_tools 더미 제거, intraday quote 결합, checkpoint 도입, API endpoint)
  - DB 접근 패턴 혼재 (SQLAlchemy vs asyncpg) 정리 방향

## 의존성 그래프

```
[단계 1: 공통 인프라]
  ┌──────────────────────────────────┐
  │ app/core/redis.py (공용 헬퍼)    │  ◄─── debate-runtime Phase 0
  └──────────────────────────────────┘
                  │
                  ▼
  ┌──────────────────────────────────┐
  │ app/external/chroma_client.py    │  ◄─── vector-db Phase 1
  │ app/external/embedding.py        │
  └──────────────────────────────────┘
                  │
                  ▼
[단계 2: news-cache 옵션 B 전환]
  ┌──────────────────────────────────┐
  │ news_ingestion.py 수정           │  ◄─── news-cache-policy-revision
  │ news_cache_scheduler.py 수정     │      + vector-db Phase 2-3
  │ evidence_indexing.py 신설        │
  └──────────────────────────────────┘
                  │
                  ▼
[단계 3: 신규 cache plan (순차 또는 병렬)]
  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐
  │ price-cache      │  │ financial-cache  │  │ filing-cache     │
  │ (pykrx 도입)     │  │ (corp_code 도입) │  │ (corp_code 공유) │
  └──────────────────┘  └──────────────────┘  └──────────────────┘
        │                       │ corp_code 매핑 공유 │
        │                       └─────────────────────┘
        │
        │ 후속 valuation phase 의존
        └──► financial-cache Phase 4 (별도)
                  │
                  ▼
[단계 4: 토론 도메인 (별도 plan 작성 후)]
  ┌──────────────────────────────────┐
  │ debate-runtime Phase 2-5         │  ◄─── 토론 plan + 인프라 일괄
  │ vector-db Phase 4 (Retrieval)    │
  │ intraday quote                   │
  └──────────────────────────────────┘
```

## 단계별 상세

### 단계 1: 공통 인프라 (1-2일, 병렬 가능)

후속 plan들이 모두 의존하는 모듈 먼저 정리.

| 작업 | 출처 plan | 산출물 | 소요 |
|---|---|---|---|
| 공용 Redis 헬퍼 | debate-runtime Phase 0 | `app/core/redis.py` (클라이언트 팩토리 + 키 헬퍼) | 0.5일 |
| ChromaDB 클라이언트 + 임베딩 wrapper | vector-db Phase 1 | `app/external/chroma_client.py`, `app/external/embedding.py` | 1-2일 |

**왜 먼저?**
- 현재 NewsCache가 Redis 직접 다루고 있어 신규 모듈들 붙기 전 통합 필요
- ChromaDB/embedding wrapper는 옵션 B 전환 + 향후 모든 cache plan에서 사용
- 가장 작은 단위 작업이라 워밍업 + 토대 확보

**검증**:
- `scripts/validate_chroma_connection.py` — chroma ping + collection 생성 + upsert/query/delete
- Redis 헬퍼는 NewsCache 기존 호출이 회귀 없이 동작하는지 확인

**이 단계 완료 시 상태**:
- ✅ 닫히는 Phase: `vector-db Phase 1`, `debate-runtime Phase 0`
- 🟡 진행 중 plan: vector-db (~20% 완료), debate-runtime (~17% 완료)
- ❌ 완료된 plan: 없음 (Phase 일부만 닫힘)
- 📄 봐야 할 plan 문서:
  - `vector-db-and-evidence-retrieval-plan.md` — Phase 1 (line "Phase 1. ChromaDB wrapper + 임베딩 API" 섹션)
  - `debate-runtime-infrastructure-plan.md` — Phase 0 (line "Phase 0. 공용 Redis 헬퍼 모듈" 섹션)
- 📋 산출물: `app/core/redis.py`, `app/external/chroma_client.py`, `app/external/embedding.py`, `scripts/validate_chroma_connection.py`

### 단계 2: news-cache 옵션 B 전환 (2-3일)

기존 동작 중인 시스템을 옵션 B로 전환. **2.a 수집 코드 변경(즉시 Chroma upsert 포함)**과 **2.b 로컬 RAG 인덱싱 스크립트(복구/백필용)**로 분리.

#### 단계 2.a: 수집 코드 (PG만)

| 작업 | 출처 plan | 영향 |
|---|---|---|
| news-cache-policy-revision 구현 | 새 plan | `news_ingestion.py` 상수/P1 제거/content=NULL + direct Chroma upsert |
| scheduler 갱신 | 같은 plan | `news_cache_scheduler.py`에서 `MAX_CONTENT_ROWS` 로직 제거 |
| repository 갱신 | 같은 plan | `trim_content_for_symbol` 제거 |
| 검증 시나리오 갱신 + 신규 4개 | 같은 plan | partial insert 회귀 + content NULL 강제 + 본문 추출 성공 row만 적재 |
| 라이브 검증 (SK하이닉스) | 같은 plan | `content_not_null = 0` + 적재 10건 중 유의미 기사 비율 |

#### 단계 2.b: 로컬 RAG 인덱싱 (수동 reindex)

| 작업 | 출처 plan | 영향 |
|---|---|---|
| evidence_indexing 신설 | vector-db Phase 2 | `app/domain/evidence_indexing.py` (PG row → ChromaDB document 변환 헬퍼) |
| reindex 스크립트 신설 | vector-db Phase 3 | `scripts/reindex_local_chroma.py` (`--symbol`, `--source`, `--reset`, `--force`, `--all-watchlist`) |
| 첫 reindex 실행 | 같은 plan | direct upsert 이후 복구/백필 경로 검증 |

**왜 두 번째?**
- 이미 동작 중인 시스템 변경이라 먼저 안정화
- **news-cache가 옵션 B 구현의 reference** — 이후 filing-cache도 같은 패턴 적용
- 단계 1에서 만든 ChromaDB 인프라가 처음 사용되는 지점

**닫힘 기준 (졸프 단계)**:
- 단계 2.a: PG content NULL 강제 회귀 + direct Chroma upsert + 라이브 적재 검증
- 단계 2.b: reindex 스크립트로 로컬 ChromaDB 복구/백필 가능 + symbol metadata filter 검색 가능
- (운영 진입 시 별도) ChromaDB 백업/복구 절차, fail-soft + reconcile — `production-deployment-plan.md`로 미룸

**이 단계 완료 시 상태**:
- ✅ 닫히는 Phase: `vector-db Phase 2` (News adapter), `vector-db Phase 3` (reindex 스크립트), `news-cache-policy-revision` 코드 측 전부
- 🟢 **완료된 plan**: `news-cache-policy-revision-plan.md` (졸프 단계 닫힘 기준 100%, 운영 진입 항목은 별도 plan으로 분리)
- 🟡 진행 중 plan: vector-db (~60% 완료 — Phase 1+2+3), debate-runtime (~25% 그대로)
- 📄 봐야 할 plan 문서:
  - `news-cache-policy-revision-plan.md` — 전체 (특히 "옵션 B 흐름 - 수집 단계", "옵션 B 흐름 - RAG 인덱싱 단계", "ChromaDB 동기화 정책 (수동 reindex 우선)", "닫힘 기준")
  - `vector-db-and-evidence-retrieval-plan.md` — Phase 2, Phase 3 (로컬 Chroma reindex)
- 📋 산출물:
  - `app/domain/evidence_indexing.py` (신설)
  - `app/domain/news_ingestion.py` (수정 — 상수/P1/content NULL + direct Chroma upsert)
  - `app/domain/news_cache_scheduler.py` (수정 — `MAX_CONTENT_ROWS` 제거)
  - `app/repositories/news_cache_repository.py` (수정 — `trim_content_for_symbol` 제거)
  - `scripts/reindex_local_chroma.py` (신설)
  - `scripts/validate_news_ingestion.py` (갱신 + 신규 시나리오 4개)
  - `scripts/validate_chroma_connection.py`는 단계 1에 이미 있음

### 단계 3: 신규 cache plan (각 2-3일)

**순차 또는 병렬 진행** (사람 수에 따라):

#### 3.1 price-cache (가장 우선)

- 토론 technical 카테고리 evidence 핵심
- pykrx 처음 도입 (외부 의존성 추가 + 라이브 검증 필요)
- 가격 + 기술지표 통합 (Phase 1-4)
- 1년 백필 + 매일 16:00 KST sweep
- 가격/지표 트랜잭션 분리

**이 단계 완료 시 상태**:
- ✅ 닫히는 Phase: `price-cache Phase 1-4` (전체)
- 🟢 **완료된 plan**: `price-cache-ingestion-plan.md` (100%)
- 🟡 진행 중: vector-db (~50% 그대로), debate-runtime (~17% 그대로)
- 📄 봐야 할 plan 문서: `price-cache-ingestion-plan.md` 전체
- 📋 산출물:
  - `app/external/krx_client.py` (pykrx wrapper)
  - `app/repositories/price_cache_repository.py`
  - `app/repositories/technical_indicator_cache_repository.py`
  - `app/domain/price_ingestion.py`
  - `app/domain/technical_indicator.py`
  - `app/domain/price_cache_scheduler.py`
  - `app/domain/watchlist_service.py` 수정 (`sync_watchlist_prices` 추가)
  - `app/api/watchlist.py` 수정 (background task 추가)
  - `scripts/validate_price_ingestion.py`
  - `scripts/validate_technical_indicator.py`
  - `scripts/validate_price_cache_scheduler.py`
  - `scripts/run_price_cache_scheduler.py`
- 🟦 토론 plan 작성 시점: **이 즈음 (단계 3.1 끝 무렵)** 토론 도메인 plan 초안 작성 시작 권장

#### 3.2 financial-cache

- corp_code 매핑 도입 (filing과 공유) — `app/external/dart/corp_code.py`
- 분기 단위 (변동 빈도 가장 낮음 → corp_code 인프라 안정화에 적합)
- 5년 백필 (~20분기)
- ROE/debt_ratio 즉시 계산, PER/PBR은 가격 의존이라 후속 valuation phase로 분리

**이 단계 완료 시 상태**:
- ✅ 닫히는 Phase: `financial-cache Phase 0-4`
- 🟢 **완료된 plan**: `financial-cache-ingestion-plan.md` (100%)
- 🟡 진행 중: vector-db (~50% 그대로), debate-runtime (~17% 그대로)
- 📄 봐야 할 plan 문서:
  - `financial-cache-ingestion-plan.md` 전체
  - `price-cache-ingestion-plan.md` (가격 의존 배경)
- 📋 산출물:
  - `app/external/dart/corp_code.py` (corp_code 매핑, filing과 공유)
  - `app/external/dart/client.py` (DART HTTP wrapper)
  - `app/external/dart/financial_account_map.py`
  - `app/repositories/financial_cache_repository.py`
  - `app/domain/financial_ingestion.py`
  - `app/domain/financial_ratios.py` (ROE/debt_ratio 계산)
  - `app/domain/financial_cache_scheduler.py`
  - `app/domain/watchlist_service.py` 수정 (`sync_watchlist_financials` 추가)
  - `app/api/watchlist.py` 수정
  - `scripts/validate_financial_ingestion.py`
  - `scripts/refresh_corp_code_map.py` (수동 실행)
  - `scripts/run_financial_cache_scheduler.py`
- 🟦 토론 plan 작성 시점: **3.2 진행 중에 plan 초안 마무리** 권장

#### 3.3 filing-cache

- corp_code 재사용 (3.2에서 이미 도입)
- 옵션 B 패턴 재사용 (news-cache의 reference 활용)
- DART `list.json` + `document.xml`
- XML 파서 모듈 ingestion과 분리
- 본문 추출 상한 3건 (초기값, 운영 후 조정)

**이 단계 완료 시 상태**:
- ✅ 닫히는 Phase: `filing-cache Phase 0-3`, `vector-db Phase 3`, `vector-db Phase 5 (filing 부분)`
- 🟢 **완료된 plan**: `filing-cache-ingestion-plan.md` (100%, Phase 4 LLM 요약은 본 plan 범위 밖)
- 🟡 진행 중: vector-db (~80% — Phase 4만 남음), debate-runtime (~17% 그대로)
- 📄 봐야 할 plan 문서:
  - `filing-cache-ingestion-plan.md` 전체
  - `vector-db-and-evidence-retrieval-plan.md` — Phase 3, Phase 5(filing 부분)
- 📋 산출물:
  - `app/external/dart/client.py` 확장 (`list.json` + `document.xml`)
  - `app/external/dart/document_parser.py` (XML 파서, 단위 검증 가능 형태)
  - `app/repositories/filing_cache_repository.py`
  - `app/domain/filing_ingestion.py`
  - `app/domain/filing_cache_scheduler.py`
  - `app/domain/watchlist_service.py` 수정 (`sync_watchlist_filings` 추가)
  - `app/api/watchlist.py` 수정
  - `app/domain/evidence_indexing.py` 확장 (`upsert_filing` 등)
  - `scripts/validate_filing_ingestion.py`
  - `scripts/run_filing_cache_scheduler.py`
- 🟦 토론 plan: **이 시점에 작성 완료 상태여야 단계 4 진입 매끄러움**

### 단계 4: 토론 도메인 (이미 구현된 본체 + 운영 인프라 보강)

**시작 시점 상태 (a543ff1 커밋 기준)**:
- ✅ LangGraph 토론 그래프 본체 구현됨 (`app/agents/debate_graph.py`)
- ✅ 토론 노드 6개 (data/moderator_pre/bull/bear/moderator_check/moderator_summary)
- ✅ DebateState + 프롬프트 + LLM Factory
- ✅ `debate_repo.py` (asyncpg) — agent_statement/evidence/moderator_summary 영구화
- ✅ 환각 카운트 + 강제 종료 정책
- ❌ ChromaDB Retrieval API (evidence_tools.search_evidence 더미)
- ❌ LangGraph checkpoint (그래프만 있고 checkpointer 미설정)
- ❌ LLM cache / rate limit / intraday quote Redis 캐싱 / active guard
- ❌ 토론 endpoint API (`test_debate.py`로만 실행)



**현재 토론 도메인 plan 없음** — 단계 4 진입 전에 별도 plan 작성 필요.

토론 plan 작성 시 함께 묶일 작업:
- debate-runtime Phase 2-5 (LLM cache / rate limit / LangGraph checkpoint / active guard)
- vector-db Phase 4 (Retrieval API)
- intraday quote (debate-runtime Phase 1) — 토론에서 현재가 컨텍스트 필요할 때

**소요 시간 추정 불가** — 토론 plan 작성 후 확정.

**이 단계 완료 시 상태**:
- ✅ 닫히는 Phase: `vector-db Phase 4`, `debate-runtime Phase 1-5`, 토론 도메인 plan 전체
- 🟢 **완료된 plan (전부)**:
  - `vector-db-and-evidence-retrieval-plan.md` (100% — Phase 1-5 모두 닫힘)
  - `debate-runtime-infrastructure-plan.md` (100% — Phase 0-5 모두 닫힘)
  - 토론 도메인 plan (100%)
- 📄 봐야 할 plan 문서:
  - 토론 도메인 plan (단계 3 중간에 작성한 것)
  - `vector-db-and-evidence-retrieval-plan.md` — Phase 4
  - `debate-runtime-infrastructure-plan.md` — Phase 1-5
- 📋 산출물 (예상):
  - `app/external/quote_client.py` (intraday quote — pykrx 호출)
  - `app/domain/intraday_quote.py` (Redis 캐싱)
  - `app/external/llm_cache.py`
  - `app/domain/rate_limiter.py`
  - `app/domain/evidence_retrieval.py` (카테고리별 query + source quota)
  - LangGraph 노드 + 토론 도메인 코드 (토론 plan에 따라)
  - `app/api/debate.py` (토론 시작/조회 endpoint)
  - 검증 스크립트 + 라이브 검증
- 🎯 토론 시작 가능 상태 = 프로젝트 핵심 가치 달성

## 권고 사항

### 토론 plan 작성 시점

**단계 3 중간 (price-cache 구현 즈음)에 토론 plan 초안 잡아두기.**

이유:
- 단계 4 진입 직전에 plan부터 만들면 흐름 끊김
- 단계 3 구현 중에 토론 evidence 사용 시나리오가 자연스럽게 명확해짐 — plan 작성에 유리
- price-cache의 기술지표가 토론 technical 카테고리에 어떻게 들어가는지 그림을 그리면서 작성 가능

### worker 분리 검토 시점

watchlist 등록 직후 다음이 병렬로 enqueue됨:
- News sync (~10초)
- Price sync (1년 백필 + 지표 계산, ~30초)
- Financial sync (5년 백필, ~20초)
- Filing sync (~15초)

종목 수가 늘면 FastAPI BackgroundTasks 점유 시간 증가 → 신호 (sync 함수 p95 > 60초) 감지 시 worker(RQ/Celery/Arq) 분리 검토.

본 구현 순서 자체는 BackgroundTasks 가정으로 진행.

### 매 단계 끝에 commit + main 머지

- branch_strategy 메모 정책 (main 직접 커밋 회피)
- 단계별로 작업 브랜치 → main 머지
- 각 단계가 운영 가능한 상태로 닫힘 (irreversible breaking change 방지)

## 다음 액션

```
즉시: 단계 1.1 — app/core/redis.py 신설
  - 기존 NewsCache의 Redis 호출은 그대로 두고 새 헬퍼를 우선 도입
  - 후속 작업에서 점진적 이전 (한꺼번에 갈아끼우지 않음)

그 다음: 단계 1.2 — chroma_client.py + embedding.py
  - 단계 2 옵션 B 전환의 필수 선행
```

작업 시작 전 본 문서의 가정/순서가 여전히 유효한지 한 번 더 확인.

## Plan × 단계 진행 매트릭스

각 plan이 단계 진행에 따라 어떻게 닫히는지 한눈에 (a543ff1 커밋 + 졸프 단계 로컬 RAG 정책 반영):

| plan | 시작 | 단계 1 후 | 단계 2 후 | 단계 3.1 후 | 단계 3.2 후 | 단계 3.3 후 | 단계 4 후 |
|---|---|---|---|---|---|---|---|
| `news-cache-ingestion` (기존) | 100% (옵션 A) | 100% | 100% (옵션 B 전환) | 동일 | 동일 | 동일 | 동일 |
| `news-cache-policy-revision` | 0% | 0% | **100%** ✅ (졸프 닫힘 기준) | 동일 | 동일 | 동일 | 동일 |
| `vector-db-and-evidence-retrieval` | 0% | ~20% | ~60% | ~60% | ~60% | ~75% | **100%** ✅ (졸프 단계) |
| `debate-runtime-infrastructure` | **~25%** (a543ff1) | ~42% | ~42% | ~42% | ~42% | ~42% | **100%** ✅ (졸프, 운영 배치 섹션 별도) |
| `price-cache-ingestion` | 0% | 0% | 0% | **100%** ✅ | 동일 | 동일 | 동일 |
| `financial-cache-ingestion` | 0% | 0% | 0% | 0% | **100%** ✅ | 동일 | 동일 |
| `filing-cache-ingestion` | 0% | 0% | 0% | 0% | 0% (corp_code 공유) | **100%** ✅ | 동일 |
| 토론 도메인 plan (backfill) | ~50% (a543ff1) | ~50% | ~50% | 초안 작성 | 작성 완료 | (검토) | **100%** ✅ |
| (후속) `production-deployment-plan` | — | — | — | — | — | — | 배포 결정 시점 별도 작성 |

**범례**: ✅ = 100% 닫힘, 숫자 % = 부분 닫힘
**a543ff1**: 2026-05-19 토론 에이전트 본체 구현 커밋 — debate-runtime Phase 4 일부 + Phase 1 부분, 토론 도메인 plan 영역의 절반 정도 선구현
**졸프 단계 닫힘**: 운영 인프라(공용 ChromaDB/Redis NCP 셀프호스트, 백업/복구, 인증/TLS, fail-soft+reconcile) 항목은 본 매트릭스에서 제외 — 배포 결정 시 별도 plan

## 단계별 plan 완료 카운트

| 단계 끝 | 완료된 plan 수 | 누적 완료 plan 목록 |
|---|---|---|
| 단계 1 | 0개 | — |
| 단계 2 | 1개 | news-cache-policy-revision |
| 단계 3.1 | 2개 | + price-cache |
| 단계 3.2 | 3개 | + financial-cache |
| 단계 3.3 | 4개 | + filing-cache |
| 단계 4 | **7개 (전부)** | + vector-db + debate-runtime + 토론 plan |

## 정리

| 단계 | 작업 | 소요 | 핵심 산출물 |
|---|---|---|---|
| 1 | 공용 Redis 헬퍼 + ChromaDB 인프라 (로컬) | 1-2일 | `app/core/redis.py`, `app/external/chroma_client.py`, `app/external/embedding.py` |
| 2.a | news-cache 옵션 B 전환 (수집 코드) | 1-2일 | news-cache 코드 변경 (PG만, ChromaDB 호출 없음) |
| 2.b | 로컬 RAG 인덱싱 스크립트 + 첫 reindex | 1일 | `scripts/reindex_local_chroma.py`, `app/domain/evidence_indexing.py` |
| 3.1 | price-cache + technical_indicator | 2-3일 | pykrx 도입 + 가격/지표 적재 + scheduler |
| 3.2 | financial-cache + corp_code 인프라 | 2-3일 | DART 클라이언트 + corp_code 매핑 + 재무 적재 + ROE/debt_ratio |
| 3.3 | filing-cache | 2-3일 | DART 공시 + XML 파서 + 옵션 B 일관 적재 (PG만, RAG는 reindex 스크립트 확장) |
| 4 | 토론 도메인 plan 작성 + 구현 | TBD | 토론 plan + 런타임 Redis 모듈 + retrieval + LangGraph + 토론 endpoint |
| (후속) | 운영 진입 — 배포 결정 시 별도 plan | TBD | `production-deployment-plan.md` 신설 — NCP 셀프호스트, 백업/복구, 인증/TLS, 공용 ChromaDB 이전, fail-soft+reconcile |

**졸프 단계 단계 4 종료 후 운영 진입 시**:
- 공용 Redis (NCP 서버 + Docker 셀프호스트)로 이전
- 공용 ChromaDB (또는 Qdrant) 이전 + 백업/복구 + 인증/TLS
- sync 직후 ChromaDB 동기 upsert로 전환 (현재 수동 reindex → 자동)
- fail-soft + reconcile 스크립트 도입
- 인프라 팀 협업 (호스트 IP, ACG, 비밀번호, 백업 정책)
