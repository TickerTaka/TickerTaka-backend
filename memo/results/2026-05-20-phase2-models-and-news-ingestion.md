# 2026-05-20 Phase 2 결과

## 작업 범위

이번 작업은 아래 두 범위를 우선 구현했다.

1. `memo/phase2.md` 기준 SQLAlchemy 모델 작성
2. `memo/plans/news-cache-ingestion-plan.md` 기준 news cache 적재 도메인 골격 구현

이번 턴에서 의도적으로 보류한 것:
- Alembic 도입
- phase2의 나머지 운영성 정리 항목
- watchlist API와 background task 실제 연결
- news ingestion 실운영 호출 검증

## 구현 완료 내용

### 1. SQLAlchemy 모델 작성 완료

클라우드 DB 실스키마를 직접 조회한 뒤 아래 모델을 1:1 매핑으로 작성했다.

- `app/models/base.py`
- `app/models/user.py`
- `app/models/ticker.py`
- `app/models/watchlist.py`
- `app/models/cache.py`
- `app/models/debate.py`
- `app/models/__init__.py`

포함된 테이블:
- `app_user`
- `ticker_metadata`
- `price_cache`
- `financial_cache`
- `technical_indicator_cache`
- `news_cache`
- `filing_cache`
- `event_timeline`
- `data_refresh_job`
- `debate_session`
- `agent_statement`
- `evidence`
- `moderator_summary`
- `debate_note`
- `watchlist`

반영 사항:
- PostgreSQL ENUM 타입 이름 그대로 매핑
- UUID PK는 `server_default=text("gen_random_uuid()")`
- 실제 DB 기준 `DateTime(timezone=True)` 적용
- UNIQUE / INDEX / FK 관계 명시

### 2. DB 세션 유틸 정리

추가/수정:
- `app/core/db.py`

반영 사항:
- `SessionLocal`
- `session_scope()`

이후 repository / domain service에서 공통 사용 가능하도록 정리했다.

### 3. News ingestion 도메인 골격 추가

추가 파일:
- `app/external/naver_news.py`
- `app/external/article_scraper.py`
- `app/repositories/news_cache_repository.py`
- `app/domain/news_ingestion.py`

구현된 정책:
- `sync_news_for_ticker(symbol)` 함수 골격
- 네이버 뉴스 API 조회
- `source_url` 기준 dedupe
- URL 정규화
- Redis lock / cooldown 구조
- `15/5/5` 정책 반영
- `content IS NULL` 기존 row 보강 구조
- 제목 유사도 그룹화(Jaccard 기반)로 본문 크롤링 quota 보호
- 종목별 row 상한 / 본문 상한 / 오래된 본문 `NULL` 처리

## 검증 결과

### 1. 모델 매핑 검증 성공

검증 스크립트:
- `scripts/validate_models.py`

실행 결과:
- 모든 모델 import 성공
- 각 모델에 대해 `select(...).limit(1)` 실행 성공
- 결과적으로 live DB와 모델 매핑은 현재 기준 정상

확인된 대표 결과:
- `TickerMetadata: OK (row)`
- `NewsCache: OK (empty)`
- `Watchlist: OK (empty)`

### 2. 정적 검증 성공

실행:
- `python3 -m compileall app scripts`

결과:
- 문법/임포트 수준 오류 없음

### 3. news ingestion import 검증 성공

다음 import 확인:
- `NewsIngestionService`
- `SyncNewsResult`
- `ArticleScraper`
- `NaverNewsClient`

결과:
- 현재 코드 구조상 import 가능

## 남은 이슈

### 1. 새 의존성 설치는 아직 미완료

`requirements.txt`에 아래를 추가했다.
- `trafilatura==1.12.2`
- `redis==5.0.8`

하지만 현재 venv의 Python이 SSL 모듈 없이 빌드되어 있어 `pip install`이 실패했다.

실패 원인:
- `Can't connect to HTTPS URL because the ssl module is not available.`

대응:
- 현재 코드는 `trafilatura`, `redis`가 없어도 import 시점에 바로 깨지지 않도록 지연 로딩 처리함
- 실제 news ingestion 실행 전에는 Python/venv SSL 문제를 해결하고 의존성을 설치해야 함

### 2. 실제 ingestion 실행은 아직 안 함

아직 하지 않은 것:
- 네이버 뉴스 API 실제 호출 검증
- 기사 본문 크롤링 실동작 검증
- `news_cache` 실제 insert/update 검증
- watchlist 등록 후 background task 연결

즉 현재 상태는:
- 모델은 검증 완료
- 도메인 코드는 골격 구현 완료
- 실운영 동작 검증은 다음 단계

## `ticker_metadata`를 팀원이 채워둔 상태에 대한 판단

문제 없다. 오히려 현재 구조에서는 필요한 선행 상태다.

이유:
- `watchlist.symbol -> ticker_metadata.symbol` FK가 있음
- `news_cache.symbol -> ticker_metadata.symbol` FK가 있음
- `sync_news_for_ticker(symbol)`도 `ticker_metadata`에서 종목 정보를 조회함
- 검색어 생성에 `ticker_metadata.name_kr`, `name_en`을 사용함

즉 `ticker_metadata`가 채워져 있어야:
- watchlist 추가가 자연스럽고
- news ingestion이 검색어를 만들 수 있고
- FK 제약도 정상 동작한다

정리:
- 팀원이 `ticker_metadata`를 미리 채워둔 것은 현재 구현과 충돌하지 않음
- 오히려 news ingestion 구현 전제에 부합함
- 단, 기존 데이터 품질은 중요하므로 `symbol`, `name_kr`, `market` 값이 정확한지만 팀 차원에서 유지하면 됨

## 다음 권장 순서

1. Python/venv SSL 문제 해결
2. `pip install -r requirements.txt`
3. `sync_news_for_ticker(symbol)` 실제 실행 검증 스크립트 추가
4. `watchlist` 등록 이후 background task 연결
5. `news_cache` 실제 적재/정리 정책 검증

## 변경 파일 요약

수정:
- `app/core/db.py`
- `requirements.txt`

추가:
- `app/models/base.py`
- `app/models/user.py`
- `app/models/ticker.py`
- `app/models/watchlist.py`
- `app/models/cache.py`
- `app/models/debate.py`
- `app/models/__init__.py`
- `app/repositories/news_cache_repository.py`
- `app/external/naver_news.py`
- `app/external/article_scraper.py`
- `app/domain/news_ingestion.py`
- `scripts/validate_models.py`

---

## 1차 검토 보완 (2026-05-20 추가)

### 배경: 시간대 정책 재정립

DB 검증 스크립트(`scripts/check_tz.py`)로 클라우드 DB의 실제 상태를 확인했다.

확인된 사실:
- DB 컬럼은 `TIMESTAMPTZ` (timezone-aware)
- 클라우드 DB session timezone은 `Asia/Seoul` (KST)
- 팀원이 채워둔 `ticker_metadata.created_at`은 `2026-05-19 18:53:04.591112+09:00` 형태로 KST aware 저장됨
- 내부적으로는 UTC instant로 보존, 표시만 KST

→ 코드도 UTC aware datetime으로 통일하고, DB가 알아서 KST로 표시하는 방향으로 결정.
→ plan의 시간대 정책 섹션도 `TIMESTAMP / naive` → `TIMESTAMPTZ / aware` 로 갱신.

### 적용된 코드 수정

#### 🔴 1. aware datetime 일관화

기존 코드에 `.replace(tzinfo=None)` 패턴으로 naive datetime을 생성하던 부분 전부 제거. 모든 datetime은 UTC aware로 통일.

수정 파일:
- `app/external/naver_news.py`
  - `_parse_pub_date`: `.replace(tzinfo=None)` 제거, `astimezone(UTC)` aware 반환
  - timezone 누락 시 KST 가정 추가 (`parsed.replace(tzinfo=KST)`)
- `app/external/article_scraper.py`
  - `_parse_datetime`: 동일 패턴 적용 (UTC aware 반환, KST fallback)
- `app/domain/news_ingestion.py`
  - `_build_news_row`: `retrieved_at`의 naive 생성 제거
  - `datetime.min.replace(tzinfo=None)` → `datetime.min.replace(tzinfo=UTC)` (정렬 fallback)
  - `ttl_until` 계산을 aware datetime 기준으로 처리

#### 🔴 2. 제목 유사도 그룹화의 published_at gap 체크 버그 수정

기존 코드는 `_titles_similar` 호출 시 두 번째 published_at에 항상 `None`을 넘겨서, plan의 "6시간 이상 차이면 별 그룹" 룰이 무력화되어 있었다.

수정:
- `groups: list[set[str]]` → `groups: list[tuple[set[str], datetime | None]]`
- 그룹 추가 시 published_at 함께 저장
- 비교 호출 시 양쪽 모두 정확한 published_at 전달
- `group_has_content` 클로저도 인자로 `tokens_published_at` 받도록 변경

이제 일별 시세 보도(`삼성전자, 1.2% 상승` / `삼성전자, 1.5% 상승`)처럼 토큰셋이 유사하지만 시간 차이가 큰 기사들이 잘못 묶이지 않는다.

#### 🟡 3. Redis lock fail-closed로 변경

기존 동작: Redis 미설치 또는 에러 시 lock 우회하고 sync 실행 (fail-open).
변경 동작: Redis 사용 불가 시 sync 자체를 skip (fail-closed).

이유:
- plan에서 "Redis lock 기반 중복 실행 방지"를 강제 정책으로 명시
- fail-open 상태에서는 다중 워커 환경에서 동일 symbol 동시 실행 가능 → ON CONFLICT 경합

수정:
- `app/domain/news_ingestion.py:_acquire_lock`
  - Redis client None이면 즉시 None 반환 (skip)
  - Redis 에러도 None 반환 (skip)
  - 로그는 `error` 레벨로 가시화

#### 🟡 4. 모델의 `Mapped[DateTime]` → `Mapped[datetime]` 일괄 변경

SQLAlchemy 2.0 컨벤션상 type hint는 Python 타입이어야 한다. `DateTime`은 컬럼 타입 클래스이므로 `datetime`을 써야 type checker가 정확히 동작.

수정 파일:
- `app/models/user.py`
- `app/models/ticker.py`
- `app/models/watchlist.py`
- `app/models/cache.py` (+ `Mapped[Date]` → `Mapped[date]`)
- `app/models/debate.py`

각 파일에 `from datetime import datetime` (또는 `datetime, date`) import 추가.

### 추가된 검증 스크립트

#### `scripts/check_tz.py`
- 목적: DB의 timestamptz 동작과 session timezone 확인
- 실행 결과: `created_at`이 KST aware 형식으로 출력됨 (정상)

#### `scripts/validate_enums.py`
- 목적: SQLAlchemy ENUM과 DB의 PostgreSQL ENUM 값 정합성 검증
- 검증 ENUM 9종: `market_type`, `source_type`, `refresh_job_type`, `refresh_job_status`, `debate_category`, `debate_mode`, `debate_status`, `debate_round`, `agent_role`
- 실행 결과: **9/9 모두 일치** (대소문자 케이스까지 정확)

### 검증 재실행 결과

| 검증 항목 | 결과 |
|---|---|
| `python3 -m compileall app scripts` | 통과 |
| `scripts/validate_models.py` (15 모델) | 모두 OK |
| `scripts/validate_enums.py` (9 ENUM) | 모두 OK |
| `scripts/check_tz.py` (KST 표시 확인) | 정상 |

### plan 문서 갱신

`memo/plans/news-cache-ingestion-plan.md`의 "시간대 정책" 섹션을 다음과 같이 갱신:
- DB 컬럼이 `TIMESTAMPTZ`임을 명시
- session timezone이 `Asia/Seoul`임을 명시
- naive datetime 사용 금지 원칙 추가
- 내부 코드는 UTC aware로 통일, 표시는 KST 자동 적용

### 남은 작업

남은 작업은 모두 `memo/plans/news-cache-ingestion-plan.md`의 Phase 1~3 흐름에 해당한다.

#### 선행 환경 정비 (plan 범위 밖)
- [ ] Python/venv SSL 문제 해결 → `pip install -r requirements.txt`로 `trafilatura`, `redis` 설치
- [ ] Redis 컨테이너 기동 (fail-closed 정책이라 Redis 없으면 sync 자체가 skip됨)

#### Phase 1 — 수집 함수 실호출 검증
plan Phase 1 (수집 함수 최소 버전)의 처리 순서 1~11 중 7~11 (저장/TTL/cleanup) 동작 확인.
- [ ] `sync_news_for_ticker(symbol)` 실호출 스크립트 작성
- [ ] 실제 네이버 API 응답 → 정규화 → DB 저장까지 end-to-end 동작 검증
- [ ] 본문 크롤링 5건 quota 동작 확인
- [ ] 제목 유사도 그룹화의 false positive/negative 운영 데이터 수집 시작

#### Phase 2 — 트리거 연결
plan Phase 2 전체.
- [ ] `app/api/watchlist.py` (또는 동등 위치) watchlist 등록 엔드포인트
- [ ] `db.commit()` 성공 후 `background_tasks.add_task(sync_news_for_ticker, symbol)` 등록
- [ ] commit 실패 시 enqueue 안 되도록 보장

#### Phase 3 — 정기 갱신/정리
plan Phase 3 전체.
- [ ] 1시간 주기 refresh 실행 주체 결정 (외부 cron 또는 별도 스케줄러)
- [ ] watchlist 등록 종목 순회 + `sync_news_for_ticker(symbol, mode="refresh")` 호출
- [ ] TTL cleanup (`ttl_until < now()` 삭제)
- [ ] 종목별 row 100건 초과분 정리
- [ ] 종목별 `content IS NOT NULL` 10건 초과분의 본문 `NULL` 처리
- [ ] 일일 API 호출량 로그 집계

### 변경 파일 요약 (1차 보완)

수정:
- `app/models/user.py`
- `app/models/ticker.py`
- `app/models/watchlist.py`
- `app/models/cache.py`
- `app/models/debate.py`
- `app/external/naver_news.py`
- `app/external/article_scraper.py`
- `app/domain/news_ingestion.py`
- `memo/plans/news-cache-ingestion-plan.md`

추가:
- `scripts/check_tz.py`
- `scripts/validate_enums.py`
