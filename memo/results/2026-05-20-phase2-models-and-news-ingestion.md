# 2026-05-20 Phase 2 결과

## 요약

Phase 2는 최종적으로 닫혔고, 이후 작업까지 반영된 상태로 `main`에 머지 완료됐다.

확인 기준:
- SQLAlchemy 모델과 live DB 스키마 매핑 완료
- `sync_news_for_ticker(symbol)` 구현 및 정책 검증 완료
- 실 네이버 API / 실 스크래퍼 / Redis / 실제 DB commit 확인 완료
- watchlist API 및 background sync 트리거 연결 완료
- TestClient 기반 endpoint smoke test 완료
- 테스트용 사용자 시드 완료
- 이후 Phase 3 구현까지 진행한 브랜치가 `main`에 머지됨

현재 git 기준:
- `main` HEAD: `a1e7743 Merge branch 'uc' into main`

## 구현 범위

### 1. SQLAlchemy 모델 작성

작성/정리 파일:
- `app/models/base.py`
- `app/models/user.py`
- `app/models/ticker.py`
- `app/models/watchlist.py`
- `app/models/cache.py`
- `app/models/debate.py`
- `app/models/__init__.py`

반영 내용:
- 실클라우드 DB 스키마 기준 1:1 매핑
- PostgreSQL ENUM 타입 이름 그대로 연결
- UUID PK + `gen_random_uuid()`
- `DateTime(timezone=True)` 기준 통일
- FK / UNIQUE / INDEX 반영

포함 테이블:
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

### 2. DB 세션 유틸

정리 파일:
- `app/core/db.py`

반영 내용:
- `SessionLocal`
- `get_db()`
- `session_scope()`
- `postgresql+asyncpg://...` → 동기 SQLAlchemy용 `postgresql://...` 정규화

### 3. News ingestion 도메인

관련 파일:
- `app/external/naver_news.py`
- `app/external/article_scraper.py`
- `app/repositories/news_cache_repository.py`
- `app/domain/news_ingestion.py`
- `scripts/validate_news_ingestion.py`
- `scripts/validate_redis_integration.py`
- `scripts/run_live_news_sync.py`

구현된 핵심:
- `sync_news_for_ticker(symbol, mode, force, limit)`
- 네이버 뉴스 API 조회
- `source_url` dedupe
- URL 정규화
- Redis lock / cooldown / fail-closed
- `15/5/5` 정책 (Phase 2 종료 시점 기준. 이후 plan 운영 보강에서 `20/5/5` + `MIN_CONTENT_LENGTH=120`으로 완화 — 세부는 `memo/results/2026-05-20-news-cache-ingestion-implementation.md`)
- `content IS NULL` 기존 row 보강
- 제목 유사도 그룹화
- row 상한 / content 상한 / 오래된 본문 `NULL`
- TTL = `published_at + 30 days`
- A/B relevance 필터
  - A: 그래픽/포토성 제목 차단
  - B: 제목 우선 매칭 + 본문 2회 이상 참조만 허용
- 일일 네이버 API 호출량 Redis 집계

## watchlist 연결

관련 파일:
- `app/api/watchlist.py`
- `app/schemas/watchlist.py`
- `app/repositories/watchlist_repository.py`
- `app/domain/watchlist_service.py`
- `scripts/validate_watchlist_flow.py`
- `scripts/validate_watchlist_api.py`
- `scripts/seed.py`
- `scripts/live_test_watchlist_sync.py`

구현된 흐름:
1. `POST /api/watchlists`
2. watchlist 저장
3. commit 성공 후 `BackgroundTasks.add_task(sync_watchlist_news, symbol)`
4. `sync_watchlist_news(symbol)`가 별도 세션에서 `sync_news_for_ticker(symbol, mode="initial", force=True)` 호출

추가 반영:
- 중복 등록 409 처리
- 없는 user / 없는 symbol 404 처리
- create 응답에서 `ticker_name_kr` 일관성 보장
- enqueue 실패 시 `sync_enqueued=False` + 로그 기록
- background sync 실패 시 API 응답과 분리

## 검증 결과

### 1. 모델 / 정적 검증

검증 스크립트:
- `scripts/validate_models.py`
- `scripts/validate_enums.py`
- `scripts/check_tz.py`
- `python -m compileall app scripts`

결과:
- 모델 import/조회 성공
- ENUM 매핑 정상
- 시간대 정책은 `TIMESTAMPTZ / aware datetime` 기준으로 정리

### 2. ingestion 정책 검증

검증 스크립트:
- `scripts/validate_news_ingestion.py`

확인된 항목:
- 초기 적재 `15/5/5`
- 중복 update / `content IS NULL` 보강
- row trim / content trim
- partial insert
- 제목 유사도 6시간 gap
- cooldown skip
- lock skip
- TTL 정확성
- A/B relevance 필터
- 일일 API 카운터 증가

### 3. Redis 실연결 검증

검증 스크립트:
- `scripts/validate_redis_integration.py`

확인된 항목:
- `PING`
- 정상 lock 획득/해제
- lock held skip
- TTL window
- cooldown 동작
- Redis 단절 시 fail-closed

### 4. 실 API / 실 DB 적재 검증

실행:
- `python -m scripts.run_live_news_sync --symbol 005930 --mode initial --limit 10 --commit`

결과:
- 실 네이버 API 호출 성공
- 실 본문 추출 성공
- `news_cache` 실제 적재 확인

이후 라이브 watchlist 트리거 검증도 수행됨:
- `scripts/live_test_watchlist_sync.py`
- SK하이닉스(`000660`) 기준
- `watchlist API -> background sync -> news_cache insert` 한 사이클 확인

### 5. watchlist 서비스 검증

검증 스크립트:
- `scripts/validate_watchlist_flow.py`

통과 시나리오:
- `service_flow`
- `empty_watchlist`
- `missing_user`
- `missing_ticker`
- `background_trigger`
- `background_failure`

### 6. endpoint smoke test

검증 스크립트:
- `scripts/validate_watchlist_api.py`

전제:
- `scripts.seed.py`로 `phase2-test-user@example.com` 시드

결과:
- TestClient 기반 smoke test `8/8` 통과

확인 항목:
- `GET /health`
- `POST /api/watchlists` happy path
- `GET /api/watchlists/{user_id}`
- duplicate `POST` → 409
- unknown user `POST` → 404
- unknown symbol `POST` → 404
- unknown user `GET` → 404
- missing field `POST` → 422

## 최종 상태

Phase 2 종료 기준으로 아래가 모두 닫혔다.

- SQLAlchemy 모델 구현
- live DB 매핑 검증
- news ingestion 구현
- 실 API / 실 스크래퍼 / Redis / 실제 DB 적재 검증
- watchlist service/repository
- watchlist → background sync 트리거
- 보완사항 반영
- 테스트용 사용자 시드
- TestClient endpoint smoke test

정리:
- Phase 2는 기능 구현, 서비스 검증, endpoint 검증까지 모두 완료
- 이후 진행된 Phase 3 작업과 함께 `main`에 머지 완료

## 참고

이 문서는 최종 상태 기준으로 정리한 결과 문서다.

세부 정책, Phase 1/3 검증 로그, scheduler/cleanup 결과는 아래 문서를 참조:
- `memo/results/2026-05-20-news-cache-ingestion-implementation.md`
