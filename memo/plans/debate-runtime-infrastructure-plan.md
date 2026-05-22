# 토론 런타임 인프라 계획 (Redis 추가 활용 + Intraday Quote)

## 목표

토론 진행과 대시보드 활용을 위한 **휘발성/저지연 상태 관리**를 Redis 기반으로 정리한다.

핵심 원칙:
- PostgreSQL은 영구 데이터, Redis는 휘발성/저지연 데이터.
- ChromaDB는 의미 검색 (영구성 + 검색용), Redis는 *값* 자체를 다룬다.
- 이미 NewsCache에서 검증된 키 컨벤션(`prefix:purpose:identifier`)을 일관 적용한다.
- 단일 Redis 인스턴스로 시작, 메모리 압박 시 분리 검토.

## 다루는 데이터

| 용도 | Redis | 영구화 위치 |
|---|---|---|
| 1. 캐시 sync lock/cooldown/sweep | 이미 사용 중 | — |
| 2. 일일 API 호출량 | 이미 사용 중 | — |
| 3. LangGraph state checkpoint | 신규 | 토론 종료 후 `debate_session` |
| 4. LLM response cache | 신규 | — (휘발성) |
| 5. Intraday quote (현재가) | 신규 | — (휘발성, 5분 TTL) |
| 6. Rate limiting / cost guard | 신규 | — |
| 7. 토론 세션 활성 상태 | 신규 | — (TTL 30분) |

## 기존 구현 현황 (2026-05-19 커밋 a543ff1)

**다른 팀원이 토론 에이전트 본체를 이미 구현해두었음.** 본 plan은 그 구현 위에 운영 인프라(Redis/checkpoint/quote/rate/cache/guard)를 보강하는 방향으로 갱신됨.

### 이미 구현된 모듈

| 모듈 | 위치 | 역할 |
|---|---|---|
| LangGraph 토론 그래프 | `app/agents/debate_graph.py` | StateGraph: data → moderator_pre → bull/bear 교대 → moderator_check → moderator_summary |
| 토론 노드 6개 | `app/agents/nodes/` | data_node, bull_node, bear_node, moderator_pre/check/summary_node |
| DebateState | `app/agents/state.py` | TypedDict (session_id, symbol, category, round, statements, hallucination_count, ...) |
| 프롬프트 | `app/agents/prompts/prompts.py` | bull/bear/moderator 시스템·휴먼 프롬프트 |
| LLM Factory | `app/core/llm_factory.py` | OpenRouter 경유 ChatOpenAI + 역할별 모델 (bull/bear/moderator/fallback) |
| 토론 영구화 repo | `app/repositories/debate_repo.py` | **asyncpg 직접 사용** (기존 SQLAlchemy 패턴과 별개), fetch_*_context + save_statement/evidence/summary |
| asyncpg pool | `app/core/database.py` | `get_pool()` 비동기 DB pool (기존 `app/core/db.py` SQLAlchemy 동기 세션과 공존) |
| evidence 검색 더미 | `app/agents/tools/evidence_tools.py` | `search_evidence` 함수 더미 (return []) — ChromaDB 도입 시 채움 |
| 시장 도구 | `app/agents/tools/market_tools.py` | (별도) |

### 본 plan 관점에서 이미 닫힌/부분 닫힌 사항

| 본 plan Phase | 상태 | 비고 |
|---|---|---|
| Phase 0 (공용 Redis 헬퍼) | **미구현** | 토론 코드는 아직 Redis 사용 안 함 |
| Phase 1 (Intraday Quote) | **부분** | `data_node._yfinance_fallback`이 DB 없을 때 yfinance 폴백 — 단 Redis 캐싱 없음, plan의 Phase 1과 정책 다름 |
| Phase 2 (LLM Response Cache) | **미구현** | LLM Factory의 `get_tracker`가 DummyTracker — 실제 캐시 없음 |
| Phase 3 (Rate Limit / Cost Guard) | **미구현** | DummyTracker, `slowapi`만 requirements에 추가됨 |
| Phase 4 (LangGraph Checkpoint) | **그래프만 구현, checkpoint 미설정** | `build_graph()`가 `compile()`만 호출 — `compile(checkpointer=...)` 미사용 |
| Phase 5 (Active Guard) | **미구현** | 토론 endpoint API가 아직 없음 (`test_debate.py`로만 실행) |

### 기존 구현이 plan과 다른 정책

1. **DB 접근 패턴 혼재**:
   - 기존 NewsCache 등: SQLAlchemy 동기 (`app/core/db.py`)
   - 토론 코드: asyncpg 비동기 (`app/core/database.py`)
   - 향후 통일 여부는 별도 결정 사항. 본 plan은 토론은 async 그대로 두고 진행.
2. **data_node yfinance 폴백**:
   - 본 plan Phase 1은 pykrx + Redis 5분 TTL 명시
   - 현 구현은 DB 캐시 없으면 즉시 yfinance 호출 (캐싱 없음)
   - 단계 3에서 price_cache 채워지면 fallback 빈도 감소
   - Phase 1 도입 시 yfinance 폴백을 Redis 캐싱 경로로 교체
3. **사회자 검증 (moderator_check) — 환각 카운트**:
   - LLM 발언마다 사회자가 verdict 판정 (`ok`/`intervene`/`hallucination`)
   - hallucination 2회 누적 시 강제 종료
   - 본 plan에 없던 디테일 → 정책 섹션에 추가 (아래 "사회자 검증 정책")
4. **OpenRouter 무료 모델 사용**:
   - `config.py`에 `bull_model`, `bear_model`, `moderator_model`, `fallback_model` 추가됨
   - 기본값: `meta-llama/llama-3.3-70b-instruct:free`, `deepseek/deepseek-r1:free` 등 — 무료 모델 우선
   - 모델 교체는 환경 변수로 처리
5. **requirements.txt 광범위 추가**:
   - langgraph, langchain-openai/community/huggingface/chroma
   - chromadb (직접 라이브러리), sentence-transformers, rank-bm25
   - redis[asyncio], celery[redis], tenacity
   - sse-starlette, slowapi, yfinance
   - **버전 핀(`==`) 정책 일부 누락** — 사용자 메모리의 "requirements.txt 의존성은 모두 ==로 버전 핀 유지" 정책과 충돌, 별도 정리 필요

## 사회자 검증 정책 (기존 구현 반영)

본 plan에 추가:

- LLM 발언(`bull_agent` / `bear_agent`)마다 `moderator_check_node`가 호출됨
- 사회자 LLM이 발언을 검토해 JSON 응답 (`verdict`, `note`, `corrected_fact`)
- `verdict` 값:
  - `ok`: 다음 흐름으로 진행
  - `intervene`: 사회자 개입 발언 추가, 같은 에이전트 재발언
  - `hallucination`: 사회자 개입 + `hallucination_count` 증가
- `hallucination_count >= 2` 시 라우터가 `moderator_summary`로 강제 이동 (토론 조기 종료)
- 본 정책은 *evidence 강제 정책*과 결합 — 사회자가 사실관계/근거 부재를 잡아냄
- LLM cache 정책에서 moderator의 `verdict` 응답을 캐싱할지 결정 필요 (false positive 캐싱 위험 vs 비용 절감)

## 1. LangGraph State Checkpoint

배경:
- 토론은 3 카테고리 × 3 라운드 × Bull/Bear + Judge = 약 18~21 LLM 호출
- 중간 실패 시 처음부터 재시작은 비용 큼
- LangGraph는 checkpoint 인터페이스 제공 (`MemorySaver`, `SqliteSaver`, `RedisSaver` 등)

설계:
- Redis key: `debate:checkpoint:{session_id}` (JSON, MessagePack 가능)
- TTL: `24시간` (토론은 보통 수 분 내 완료)
- 한 라운드 종료 직후 checkpoint
- 토론 완료 후 PostgreSQL `debate_session` + `agent_statement`로 영구화 + Redis key 삭제

라이브러리:
- 1순위: `langgraph-checkpoint-redis` (공식)
- fallback: 직접 dict 직렬화 + Redis SET/GET

**복구 실패 UX 정책** (서버 재배포/Redis flush 시):
- 토론 진행 중 checkpoint가 사라지면 LangGraph 재개 불가
- UX: 사용자에게 "토론이 중단되었습니다. 다시 시작해주세요." 표시 + 해당 `debate_session.status=FAILED` 기록
- 완전한 복구를 시도하지 않음 (비용 대비 가치 작음) — 단 부분 결과(완료된 라운드)는 PG에 영구화되어 있다면 그대로 보존
- 토론 길이 ~수분 가정 시 재시작 비용도 작음 — pragmatic 한 선택

## 2. LLM Response Cache

배경:
- 개발/디버깅 중 같은 prompt를 반복 호출하는 케이스가 많음 (특히 토론 reproducibility 점검)
- 동일 prompt + model + temperature=0 조합은 거의 결정적이라 캐싱 의미 있음
- 토론 시점 비용 절감보다 *개발 비용 절감*이 1차 가치

설계:
- 캐시 키: `llm-cache:{model}:{prompt_version}:{sha256(prompt)}:{temperature}`
- `prompt_version`을 키에 포함 — Judge 같이 운영 ON 시 prompt 변경 시 캐시 회귀 위험 차단
- 값: `{response_text, usage, ts, prompt_version}`
- TTL: `24시간` (개발 디버깅 사이클 가정)
- 운영 환경에서는 `LLM_CACHE_ENABLED=false`로 끄기 (사용자에게 동일 답변 반복 방지)

활성화 정책:
- 개발: 기본 ON
- prod: 기본 OFF
- 카테고리/에이전트별 명시적 opt-in 가능 (예: Judge는 일관성을 위해 ON)
- Judge처럼 운영 ON을 허용하는 경우 `prompt_version`을 키에 포함시켜 prompt 변경 시 cache invalidation 자동화

## 3. Intraday Quote (가장 우선순위 큰 신규 모듈)

배경:
- `PriceCache`는 일봉 단위 (장 마감 후 갱신)
- 토론 시점에 "지금 SK하이닉스 가격이 X원" 같은 컨텍스트 필요
- 매번 yfinance/pykrx 호출은 latency↑ + rate limit↑

설계:
- 데이터 소스 1순위: **pykrx** (KRX 실시간/지연시세, KOSPI/KOSDAQ)
  - 장중에는 **15분 지연 시세** — 실시간 아님
- 데이터 소스 2순위: **yfinance** (해외 종목, KOSPI도 가능하지만 지연 큼)
- Redis key: `quote:latest:{symbol}` → JSON `{price, prev_close, change, change_rate, volume, ts, source, is_delayed}`
- TTL: 장중 `5분`, 장 마감 후 `30분`, 주말 `24시간`
- **UI 표기 정책**: 사용자 노출 시 "지연 시세 (15분)" 또는 "실시간 아님" 명시 — "현재가" 단독 표기 금지 (오해 방지)

조회 정책:
- **옵션 A (lazy)** — 토론 시작 직전 1회 fetch, Redis TTL 내면 재사용
  - 토론 빈도가 낮을 거라 가정 → 추천
- **옵션 B (eager polling)** — watchlist 종목 대상 1~5분 polling
  - 대시보드에서 실시간 표시 UX 필요할 때

초기 선택: **옵션 A**.

함수 시그니처:
```python
def get_latest_quote(symbol: str, max_age_seconds: int = 300) -> Quote: ...
```

흐름:
1. Redis에서 `quote:latest:{symbol}` 조회
2. TTL/`ts` 기준 stale 여부 판정
3. fresh → 반환
4. stale → 외부 fetch → Redis SET → 반환
5. 외부 fetch 실패 시 stale 값이라도 반환 (fail-open) — DB의 직전 종가 fallback

위치:
- `app/external/quote_client.py` — pykrx + yfinance wrapper
- `app/domain/intraday_quote.py` — Redis 캐싱 + fetch 정책

## 4. Rate Limiting / Cost Guard

배경:
- LLM 호출은 비용 발생 → 사용자/세션당 한도 필요
- 토론 시작 전 cost 추정 → 한도 초과 시 거부
- 사용자 우회 방지 (한도 도달 후 재시도) — Redis로 강제

설계:
- 사용자별 일일 토큰: `rate:user:{user_id}:tokens:{YYYY-MM-DD}` (INCR + EX=48h) — **`YYYY-MM-DD`는 KST 기준** (news/dart 카운터와 동일 정책)
- 사용자별 일일 토론 수: `rate:user:{user_id}:debates:{YYYY-MM-DD}` (INCR + EX=48h) — KST 기준
- 세션별 누적: `cost:debate:{session_id}` (HSET `input_tokens` / `output_tokens`)
- 한도 임계치는 환경 변수 또는 config로 (`MAX_TOKENS_PER_USER_PER_DAY=1000000`)

운영:
- 토론 시작 직전 cost 추정 + 사용자 잔여 한도 비교
- 초과 시 `429 Too Many Requests` 또는 친절한 에러 메시지
- 운영자용 dashboard로 일일 cost 추적 (Redis 키 집계)

## 5. 토론 세션 활성 상태

배경:
- 같은 사용자가 같은 종목 토론을 여러 개 동시에 시작하는 케이스 방지
- 진행 중 토론은 DB에는 `debate_session.status=RUNNING`으로 표시되지만, Redis로 *fast guard* 확보

설계:
- Redis key: `debate:active:{user_id}:{symbol}` → session_id
- TTL: `30분` (토론 최대 길이 추정)
- 토론 시작 시 `SET NX` — 실패 시 이미 진행 중인 토론 있다는 의미
- 토론 완료 또는 실패 시 키 삭제

## Redis 키 컨벤션 정리

```
# 캐시 sync (이미 사용 중)
news-sync:lock:{symbol}             news 캐시 동시 실행 방지
news-sync:last-sync:{symbol}        news 캐시 cooldown
news-sync:sweep:last-run:{mode}     news sweep 마지막 실행 시각
naver-api-count:{YYYY-MM-DD}        네이버 API 일일 호출량

price-sync:lock:{symbol}            (계획)
price-sync:last-sync:{symbol}       (계획)
price-sync:sweep:last-run:{mode}    (계획)

financial-sync:lock:{symbol}        (계획)
financial-sync:last-sync:{symbol}   (계획)
financial-sync:sweep:last-run:{mode} (계획)

filing-sync:lock:{symbol}           (계획)
filing-sync:last-sync:{symbol}      (계획)
filing-sync:sweep:last-run:{mode}   (계획)

dart-api-count:{YYYY-MM-DD}         DART API 일일 호출량 (financial+filing 공유)

# 본 plan 신규
debate:checkpoint:{session_id}      LangGraph state (TTL 24h)
debate:active:{user_id}:{symbol}    진행 중 토론 guard (TTL 30m)
llm-cache:{model}:{prompt_hash}:{temperature}  LLM response cache (TTL 24h)
quote:latest:{symbol}               intraday quote (TTL 5m/30m/24h)
rate:user:{user_id}:tokens:{date}   사용자 일일 토큰 (TTL 48h)
rate:user:{user_id}:debates:{date}  사용자 일일 토론 수 (TTL 48h)
cost:debate:{session_id}            세션별 토큰 누적 (TTL 24h, HSET)
```

prefix 정책:
- `<domain>:<purpose>:<identifier>` 형식 유지
- domain: `news-sync`, `price-sync`, `debate`, `quote`, `llm-cache`, `rate`, `cost`
- 캐시 lock류는 `<source>-sync:lock:` 컨벤션

## 환경 변수 / 외부 의존성

추가 환경 변수:
- `REDIS_URL=redis://localhost:6379/0` (이미 있음 — 운영 시 NCP 서버 IP로 교체)
- `LLM_CACHE_ENABLED=false` (운영 기본값)
- `MAX_TOKENS_PER_USER_PER_DAY=1000000`
- `MAX_DEBATES_PER_USER_PER_DAY=20`
- `QUOTE_TTL_TRADING_SECONDS=300`
- `QUOTE_TTL_AFTERHOURS_SECONDS=1800`

requirements.txt 추가:
- `langgraph-checkpoint-redis==1.x.x` (LangGraph checkpointer 도입 시)
- `redis[asyncio]` — 이미 a543ff1 커밋에서 추가됨 (버전 핀 누락)
- `slowapi` — 이미 a543ff1 커밋에서 추가됨 (Phase 3 rate limit 라이브러리 후보)
- `celery[redis]` — 이미 a543ff1 커밋에서 추가됨 (worker 분리 시 활용 가능)
- `tenacity` — 이미 a543ff1 커밋에서 추가됨 (Phase 2 LLM cache의 retry/backoff 활용)
- `pykrx==1.0.45` (Price plan과 공유)

**주의**: a543ff1 커밋에서 requirements.txt가 광범위 갱신되며 기존 `==` 버전 핀 정책이 일부 깨졌음. 사용자 메모리의 "requirements.txt 의존성은 모두 ==로 버전 핀 유지" 정책과 충돌 — 별도 정리 작업 필요.

## 운영 환경 배치 (NCP 서버 + Docker 셀프 호스트)

### 배치 결정

- PostgreSQL은 NCP 매니지드 DB(Cloud DB for PostgreSQL 또는 동등 인스턴스)에 별도 운영 — 이미 구축됨
- **Redis는 NCP 일반 서버 인스턴스 위에 Docker로 셀프 호스트** — 인프라 팀 결정
- NCP Cloud DB for Redis(매니지드) 옵션은 채택하지 않음 (셀프 호스트 비용 효율)
- ChromaDB도 같은 패턴으로 NCP 서버 위 Docker 운영 (별도 plan `vector-db-and-evidence-retrieval-plan.md` 참고)

### 운영 docker-compose 예시 (인프라 팀 참고용)

```yaml
services:
  redis:
    image: redis:7-alpine
    container_name: tickertaka-redis-prod
    restart: unless-stopped
    command: >
      redis-server
      --requirepass ${REDIS_PASSWORD}
      --maxmemory 1gb
      --maxmemory-policy allkeys-lru
      --save 900 1
      --save 300 10
      --appendonly no
    ports:
      - "6379:6379"  # 사설망 운영이면 bind 사설 IP만
    volumes:
      - /var/lib/tickertaka/redis:/data
    healthcheck:
      test: ["CMD", "redis-cli", "-a", "${REDIS_PASSWORD}", "ping"]
      interval: 10s
      timeout: 5s
      retries: 5
```

### 운영 시 결정 항목

1. **인증**
   - `requirepass` 또는 Redis 6+ ACL 기반 사용자/비밀번호
   - 사설망 내부만 접근하더라도 비밀번호 필수 (방어 심도)
   - 비밀번호는 강한 난수 (32자 이상)
2. **네트워크**
   - 사설망(VPC 내부 IP) 권장 — API 서버와 같은 VPC
   - 공인 IP 노출 시 NCP ACG(보안 그룹)에서 API 서버 IP만 허용 + TLS 필수
3. **TLS**
   - 사설망이면 평문 OK (`redis://`)
   - 공인망이면 `stunnel` 또는 Redis 6 TLS 기본 기능 사용 → `rediss://` 스킴
4. **Persistence**
   - **RDB(snapshot) 권장**, AOF는 부담 큼
   - `save 900 1` (15분 안에 1개 변경 시 스냅샷) + `save 300 10` 정도
   - RDB 파일 백업은 호스트 디렉토리 마운트(`/var/lib/tickertaka/redis`) → 주기적 외부 백업(NCP Object Storage 등)
5. **메모리 한도**
   - `maxmemory 1gb` (본 프로젝트 예상 사용량 ~100MB 대비 여유)
   - `maxmemory-policy allkeys-lru` (메모리 차면 오래된 키부터 evict)
6. **가용성**
   - 초기 운영: 단일 인스턴스
   - SPOF 우려 시 후속 단계에서 sentinel 또는 replica 추가
7. **모니터링**
   - `redis-cli INFO` 주기 수집 (메모리/연결수/슬로우로그)
   - NCP 콘솔 메트릭(서버 CPU/메모리/디스크) 활용
   - 후속에 Prometheus exporter 도입 검토

### 로컬 개발 환경과의 분리

- 로컬은 기존 `docker-compose.yml`의 `redis:7-alpine` 그대로 사용 (인증 없음, 영속화 없음)
- 운영 서버는 위의 운영 compose로 띄움 (인증 + 영속 + 메모리 한도)
- API 서버는 `.env`(운영) / `.env.local`(로컬 개발) 중 어느 한 곳의 `REDIS_URL`을 읽어 자동 분기

### `.env` 운영/로컬 분리 예시

```bash
# 로컬 개발 (.env.local — git 무시)
REDIS_URL=redis://localhost:6379/0

# 운영 서버 (.env — git 무시, 운영 서버에만 배치)
REDIS_URL=redis://:STRONG_PASSWORD@10.0.x.x:6379/0
# 또는 TLS 사용 시
REDIS_URL=rediss://:STRONG_PASSWORD@10.0.x.x:6379/0?ssl_cert_reqs=none
```

`app/config.py`가 이미 `(.env, .env.local)` 둘 다 읽도록 되어 있어 추가 코드 변경 없음.

### 코드 측 영향

없음. `redis.Redis.from_url(redis_url, decode_responses=True)`가 URL만 받아 처리하므로 `REDIS_URL` 환경 변수만 운영값으로 바꾸면 됨.

### 인프라 팀에 요청할 정보

- Redis 호스트(IP/도메인) / 포트
- 비밀번호 (또는 ACL 사용자/비번)
- 사설망 내부 IP인지 공인 IP인지
- TLS 사용 여부
- 영속 디렉토리 마운트 경로 (백업 운영용)

## ChromaDB와의 분담

| Redis | ChromaDB |
|---|---|
| 휘발성 상태 | 영구성 |

## 검증/보완 메모 (2026-05-22)

1. 이 문서는 Redis를 상태 저장/가드 용도로 잘 분리했지만, 실제 선행 구현 순서는 `Intraday Quote`보다 `debate:active`, `rate limiting`, `checkpoint`가 더 앞설 가능성이 높다. 토론 런타임을 먼저 붙인다면 우선순위 표기를 한 번 더 정리하는 편이 좋다.
2. `quote:latest:{symbol}`를 pykrx 1순위로 둔 것은 한국 종목 기준 타당하지만, pykrx의 장중 지연 시세 의미와 `현재가`라는 UI 문구가 어긋날 수 있다. 사용자 노출 시에는 "지연 시세" 표기가 필요할 수 있다.
3. `LLM_CACHE_ENABLED`는 개발 기본 ON, 운영 OFF 정책이 합리적이지만, Judge처럼 deterministic한 일부 역할만 운영 ON을 허용할 경우 prompt versioning을 키에 포함해야 회귀 위험을 줄일 수 있다.
4. `debate:checkpoint:{session_id}`를 Redis 단독으로 둘 때, 토론 중 서버 재배포/Redis flush 상황의 복구 전략이 없다. 완전한 복구가 필수는 아니더라도, 실패 시 세션 상태를 어떻게 사용자에게 보여줄지 UX 메모가 있으면 좋다.
5. `rate:user:{user_id}:tokens:{date}` 등 일일 키는 news/dart 카운터와 마찬가지로 KST 기준 날짜인지 명시하는 편이 운영 일관성에 좋다.
6. Redis 운영 compose 예시는 충분하지만, 현재 프로젝트는 이미 Redis를 news sync lock에 쓰고 있으므로 실제 구현 시 `app/core/redis.py` 같은 공용 클라이언트/키 헬퍼 모듈이 필요해질 가능성이 높다. 이건 worker/quote/checkpoint가 붙기 전에 한 번 정리하는 게 유리하다.
| key-value 조회 | 의미 검색 |
| 짧은 TTL | TTL은 cache cleanup과 동기화 |
| lock / cache / quote / rate | evidence retrieval |
| 단일 인스턴스 (메모리) | 단일 인스턴스 (디스크 + 메모리) |

두 시스템은 서로 보완 관계 — 토론은 다음 흐름:
1. (ChromaDB) 카테고리별 evidence 검색
2. (Redis) intraday quote 컨텍스트 주입
3. (Redis) rate limit 확인
4. (Redis) LangGraph checkpoint
5. (LLM) 발언 생성, (Redis) LLM cache 활용 (개발 환경)
6. (PostgreSQL) 발언 영구화 → `agent_statement`, `evidence`

## 구현 단계

### Phase 0. 공용 Redis 헬퍼 모듈 (선행 필수)

현재 NewsCache가 `app/domain/news_ingestion.py` 안에서 Redis 클라이언트를 직접 다루고 있음. 본 plan의 7가지 용도(checkpoint, llm-cache, quote, rate, active guard, sweep last-run, lock)를 추가하면 각 모듈에서 클라이언트/키 헬퍼가 중복 정의될 가능성.

목표:
- `app/core/redis.py` — 단일 Redis 클라이언트 팩토리 (`get_redis()`) + 키 헬퍼 (`make_key(domain, purpose, *parts)`)
- 기존 NewsCache의 Redis 호출을 점진적으로 이전 (호환성 유지하면서)
- 본 plan의 모든 신규 모듈은 이 헬퍼 사용

이 단계가 Phase 1 이후 모듈들 (intraday_quote / rate_limiter / llm_cache / checkpoint)이 붙기 전에 정리되어야 운영 시 일관성 확보.

### Phase 1. Intraday Quote 모듈

PriceCache plan과 별도로 진행. 토론 도메인 이전에 가장 단순한 가치.

**현재 a543ff1에서 `data_node._yfinance_fallback`이 비슷한 역할을 수행하나 Redis 캐싱이 없음.** 본 Phase 도입 시 yfinance 폴백 자리를 캐싱 경로로 교체.

목표:
- `app/external/quote_client.py` — pykrx 호출 + 폴백 (yfinance도 fallback으로 유지)
- `app/domain/intraday_quote.py` — Redis 캐싱 + fetch 정책
- `get_latest_quote(symbol)` 호출 인터페이스
- `data_node._yfinance_fallback` 호출을 `get_latest_quote(symbol)`로 교체

산출물:
- `scripts/validate_intraday_quote.py` — 시드 종목 quote 조회 + TTL 동작 확인

### Phase 우선순위 재정렬 안내

본 plan에 정리된 Phase는 *구현 가능 순서*이지 *실행 순서*가 아니다. 토론 도메인을 먼저 붙이는 시나리오라면 다음 순서가 더 합리적:

- Phase 0 (공용 Redis 헬퍼) — 가장 먼저
- Phase 5 (debate active guard) + Phase 3 (rate limit) — 토론 endpoint 추가 시 동시에
- Phase 4 (LangGraph checkpoint) — 토론 도메인 LangGraph 도입과 동시
- Phase 2 (LLM cache) — LLM 호출 모듈 도입 후
- Phase 1 (Intraday quote) — UI/토론에서 현재가가 필요해질 때

Intraday quote가 가장 단순하지만 *반드시 가장 먼저일 필요는 없음* — 토론 흐름 구축 후 컨텍스트 주입 시점에 도입해도 충분.

### Phase 2. LLM Response Cache

**LLM Factory는 a543ff1에서 구현 완료** (`app/core/llm_factory.py`) — 단 `get_tracker`가 DummyTracker.
본 Phase는 그 더미 자리에 실제 캐시 구현을 끼워 넣는 작업.

목표:
- `app/external/llm_cache.py` — wrap LLM client, prompt → cache key
- `llm_factory.get_llm()` 반환 인스턴스에 cache 래퍼 적용 (또는 invoke 호출 시점에 캐시 lookup/store)
- 토론 노드(`bull_node` / `bear_node` / `moderator_*_node`)는 변경 없음 — `get_llm()`만 호출

기존 구현 활용:
- 모든 노드가 `get_llm(role, temperature)` → `llm.invoke([...])` 패턴 사용
- 이 invoke 경로에 캐시 hook 삽입 시 노드 코드 무수정으로 적용 가능
- `tenacity`가 이미 requirements에 있어 retry/backoff에 활용 가능

### Phase 3. Rate Limit / Cost Guard

운영 진입 시점에 도입. 사용자 인증/세션이 자리잡은 후.

목표:
- `app/domain/rate_limiter.py` — Redis INCR 기반
- 토론 시작 API 직전에 호출
- 운영자 dashboard용 집계 스크립트

라이브러리 활용:
- `slowapi`가 a543ff1에서 requirements에 추가됨 — FastAPI rate limit middleware 후보
- 다만 slowapi는 IP/사용자 단위 *요청 카운트*용. 본 plan의 *토큰 카운트*는 별도 Redis 키 직접 관리 필요
- 두 가지 병행 (slowapi=요청, 직접=토큰)

### Phase 4. LangGraph Checkpoint

**LangGraph 그래프 자체는 a543ff1에서 구현 완료** (`app/agents/debate_graph.py`).
본 Phase는 그 그래프에 **checkpointer를 결합**하는 작업.

목표:
- `langgraph-checkpoint-redis` 도입
- `build_graph()`의 `compile()` 호출에 `checkpointer=RedisSaver(...)` 추가
- 라운드별 checkpoint 설정 (`moderator_check_node` 직후가 자연스러움)
- 실패 시 복구 흐름 검증

기존 구현 활용:
- DebateState가 이미 TypedDict로 정의되어 있어 직렬화 가능
- statements에 `operator.add` 리듀서 적용됨 — checkpoint 복구 시 누적 보존
- `app/core/database.py`의 asyncpg pool과 별개로 Redis checkpointer 독립 운영

### Phase 5. 활성 토론 Guard

토론 시작 endpoint 만들 때 같이.

**현재 토론 endpoint API 없음** — `test_debate.py`로만 실행 가능. API 작성 시 함께 도입.

목표:
- `app/api/debate.py` 신설 (`POST /api/debates`로 토론 시작)
- `SET NX EX=1800`으로 중복 진입 방지
- 응답에 기존 진행 중 session_id 반환
- 토론 진행 스트리밍이 필요하면 `sse-starlette` 활용 (a543ff1에서 requirements에 추가됨)

## 관측성과 로그

최소 구조화 로그:
- Redis 메모리 사용량 (`INFO memory`)
- key별 hit rate (sample 기반)
- quote stale fallback 빈도
- LLM cache hit rate
- rate limit hit 횟수 (사용자별 / 일자별)

추가 운영 지표:
- 토론 1회당 평균 cost (`cost:debate:*` 집계)
- intraday quote fetch latency (외부 호출 + Redis)
- checkpoint 키 누적 수 (TTL 정상 작동 확인)

## 향후 확장 후보

- Redis cluster 도입 (메모리 압박 시)
- intraday quote eager polling (옵션 B) — 대시보드 실시간 UX 요구 시
- LLM cache를 의미 기반 (semantic cache)으로 확장 — 유사 prompt → 같은 답변 재사용
- LangGraph 다단계 checkpoint (라운드 단위 → 발언 단위)
- 사용자별 cost 사전 추정 정확화 (모델별 단가 + token 추정)
- BullMQ/RQ 같은 작업 큐 도입 (현재는 BackgroundTasks)
- Pub/Sub 기반 실시간 dashboard 업데이트 (가격 변동 push 등)

## 결론

확정 내용:
- 단일 Redis 인스턴스, 키 컨벤션 일관 (`<domain>:<purpose>:<identifier>`)
- **공용 Redis 헬퍼(`app/core/redis.py`)를 Phase 0로 선행** — 신규 모듈 4종이 붙기 전 클라이언트/키 헬퍼 일원화
- LangGraph checkpoint + LLM cache + intraday quote + rate limit + 토론 active guard 일괄 정리
- 영구 데이터는 PostgreSQL, 의미 검색 + 본문 SOT는 ChromaDB, 휘발성/저지연은 Redis로 분담
- intraday quote는 pykrx 1순위 + Redis 5분 TTL, 토론 시작 직전 lazy fetch — UI 표기는 "지연 시세" 명시
- LLM cache는 개발 ON / prod OFF 기본, 운영 ON 시 `prompt_version` 키 포함
- rate limit은 사용자 일일 토큰/토론 수로 강제, 카운터 키 KST 일관
- LangGraph checkpoint는 토론 도메인 phase에 종속, Redis flush 시 복구 시도 없이 사용자에 재시작 안내
- Phase 실행 순서는 토론 도메인 진행에 맞춰 재정렬 (위 "Phase 우선순위 재정렬 안내" 참고)
