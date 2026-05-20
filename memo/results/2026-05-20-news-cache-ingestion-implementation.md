# 2026-05-20 News Cache Ingestion 구현 결과

## 작업 범위

이번 작업은 `memo/plans/news-cache-ingestion-plan.md`의 구현 진행과 검증에 집중했다.

이번 턴에서 진행한 범위:
- 동기 SQLAlchemy 세션이 실제 `.env.local`의 PostgreSQL URL로 연결되도록 정리
- `news_ingestion.py`에 기사 선택 기준 기반 필터링 정책 구현
- `sync_news_for_ticker(symbol)` 정책 검증 스크립트 작성
- `news_cache`의 insert / update / content 보강 / content NULL 처리 / row trim 정책 검증
- 설치된 `trafilatura` / `redis` 버전 기준 import 및 검증 재확인
- 실연동 검증용 live sync 스크립트 추가 및 rollback 모드 실검증
- 실 Redis 연결 기반 lock/cooldown/TTL/fail-closed 검증 스크립트 추가 및 통과
- relevance 필터 값싼 정교화 2종(A/B) 반영 및 재검증

이번 턴에서 의도적으로 보류한 것:
- watchlist 등록 후 background task 연결
- 다종목 rollback live sync 기반 노이즈 비율 비교

## 구현 내용

### 1. 동기 DB URL 정규화

수정 파일:
- `app/core/db.py`

반영 내용:
- `postgresql+asyncpg://...` 형태의 URL이 들어와도
- 동기 SQLAlchemy 엔진 생성 시 `postgresql://...`로 변환되도록 처리

배경:
- 현재 프로젝트에는 `asyncpg` 기반 코드와 `psycopg2` 기반 동기 SQLAlchemy 코드가 공존한다.
- `SessionLocal`은 동기 세션이므로 `asyncpg` URL을 그대로 쓰면 모델 검증 및 ingestion 검증이 깨진다.

결론:
- 동기 경로에서 DB 연결 방식이 명확해졌고, 모델/검증 스크립트 재사용성이 좋아졌다.

### 2. 기사 선택 기준 필터링 구현

수정 파일:
- `app/domain/news_ingestion.py`

추가된 정책:
- prefilter
  - 최근 7일 초과 기사 제외
  - 제목 길이 8자 미만 제외
  - 광고성/보도자료/미러링 문구 제외
  - 한국 종목 기준 한글 근거 또는 종목코드 근거가 없는 메타데이터 제외
- 저장 전 최종 필터
  - `ticker_metadata.name_kr` exact match 또는 종목코드 exact match 필요
  - 본문 확보 시 길이 200자 미만 제외
  - scraper가 빈 본문을 돌려준 경우 저장 제외

구현 방식:
- 스크래핑 전에 값싼 필터를 먼저 적용해 quota 낭비를 줄임
- 스크래핑 후에는 저장 직전 최종 관련성/본문 품질 필터를 다시 적용
- 본문 크롤링 실패(`Exception -> None`)는 partial insert 허용 정책을 유지

정리:
- 이전 상태는 `조회 -> dedupe -> 그룹화 -> 저장` 흐름이었다.
- 현재는 `조회 -> prefilter -> dedupe/그룹화 -> 스크래핑 -> 저장 전 최종 필터 -> 저장` 흐름으로 보완됐다.

### 2-1. 값싼 relevance 정교화(A/B) 추가

수정 파일:
- `app/domain/news_ingestion.py`

A. 제목 패턴 제외:
- `[포토]`
- `[사진]`
- `[그래픽]`
- `[표]`
- `[인포그래픽]`

B. 제목 우선 매칭:
- 제목에 `ticker_metadata.name_kr` exact match가 있으면 통과
- 제목에 종목코드 exact match가 있으면 통과
- 제목에 근거가 없으면 본문 내 `name_kr` exact match가 `2회 이상`일 때만 통과
- `description`이나 본문에 `name_kr`이 1회만 등장하는 약한 참조는 통과시키지 않음

의도:
- 그래픽/사진류 기사와 주변 맥락에서 종목이 한 번만 언급되는 노이즈를 값싸게 줄이기
- 점수제나 도메인 블랙리스트 같은 무거운 튜닝은 Phase 2로 미루기

추가 보완:
- 네이버 뉴스 API 제목의 `<b>...</b>` 강조 태그가 공백으로 치환되면서 `[ 포토 ]`, `[포 토]`처럼 보일 수 있으므로
- 제목 제외 마커 비교 시 공백을 제거한 뒤 substring 매칭하도록 보완

검증 포인트:
- 그래픽/포토 류 제목은 공백이 끼어도 차단
- B의 positive 분기(`본문 내 name_kr 2회 이상`)와 negative 분기(`본문 1회`)를 모두 검증

### 3. 설치 버전 기준 런타임 정리

수정 파일:
- `app/domain/news_ingestion.py`
- `requirements.txt`

반영 내용:
- `redis` import 실패를 `ModuleNotFoundError`뿐 아니라 일반 예외까지 fallback 처리
- 현재 설치/확인된 버전에 맞춰 requirements 갱신
  - `trafilatura==2.0.0`
  - `redis==7.4.0`

배경:
- 현재 `venv`에서는 `trafilatura 2.0.0`, `redis 7.4.0` 설치 및 import 확인 완료
- 이전에는 `redis` import 시 예외가 재현된 적이 있어, import 단계에서 서비스 전체가 깨지지 않도록 가드는 유지

결론:
- 현재 설치 버전 기준으로 `news_ingestion` 모듈 import와 검증 스크립트 실행은 가능
- 패키지 설치/import 문제는 현재 기준으로 해소

### 4. news ingestion 검증 스크립트 추가/보강

추가 파일:
- `scripts/validate_news_ingestion.py`

스크립트 목적:
- 외부 API에 의존하지 않고 `sync_news_for_ticker(symbol)`의 핵심 정책을 검증
- 실제 원격 PostgreSQL 세션을 사용하되, 검증 데이터는 rollback 처리

주입한 fake dependency:
- `FakeNaverNewsClient`
- `FakeArticleScraper`
- `FakeRedis`

이 방식으로 확인 가능한 것:
- 신규 기사 insert
- `source_url` 중복 스킵
- 기존 `content IS NULL` row 본문 보강
- 제목 유사도 그룹화에 따른 본문 quota 제한
- `MAX_CACHE_ROWS` 초과 시 오래된 row 삭제
- `MAX_CONTENT_ROWS` 초과 시 오래된 본문 `NULL` 처리
- 본문 크롤링 실패 시 partial insert
- 제목 유사도 6시간 gap 룰
- cooldown skip
- Redis lock 미획득 skip
- `ttl_until = published_at + 30 days`
- 필터링 정책(최근성/제목 길이/광고/미러링/관련성/짧은 본문)
- A/B 정교화(그래픽 기사 제외, 약한 본문 참조 차단)

### 5. live sync 실행 스크립트 추가

추가 파일:
- `scripts/run_live_news_sync.py`

목적:
- 특정 `symbol`에 대해 실제 Naver 뉴스 API + 실제 본문 크롤링 경로를 한 번에 실행
- 기본값은 rollback 모드로 두어 DB 오염 없이 결과만 확인
- 필요 시 `--commit`으로 실제 적재 가능

구성:
- 기본 symbol: `005930`
- 기본 mode: `initial`
- 기본은 in-memory Redis 사용
- `--use-real-redis` 옵션으로 실제 Redis 경로 테스트 가능

## 검증 결과

### 1. 모델 재검증 성공

실행:
- `python -m scripts.validate_models`

결과:
- 원격 PostgreSQL 기준 전체 모델 조회 성공
- `TickerMetadata`, `NewsCache`, `Watchlist` 포함 현재 모델 매핑 정상

### 2. news ingestion 정책 검증 성공

실행:
- `python -m scripts.validate_news_ingestion`

검증 방식:
- live DB 세션 사용
- fake dependency 주입
- 각 시나리오 종료 후 rollback
- 현재 설치 버전 기준 import 검증 통과
  - `trafilatura 2.0.0`
  - `redis 7.4.0` 설치 및 import 상태
  - `news_ingestion`은 예외 상황 대비 redis import fallback 가드 유지

#### 시나리오 1. 초기 적재

결과:
- `inserted=15`
- `updated=0`
- `skipped=0`
- `body_saved=5`
- `grouped=5`
- `final_rows=15`
- `final_content_rows=5`

의미:
- 기본 정책 `15/5/5`가 의도대로 동작
- 15건 저장, 본문은 5건까지만 확보

#### 시나리오 2. 중복 + content 보강

결과:
- `inserted=1`
- `updated=1`
- `skipped=1`
- `body_saved=2`
- `grouped=2`
- `final_rows=3`
- `final_content_rows=3`

의미:
- 신규 기사는 insert
- 기존 `content IS NULL` row는 update로 본문 보강
- 이미 본문 있는 중복 row는 스킵

#### 시나리오 3. row trim + content trim

검증용 강제 설정:
- `MAX_CACHE_ROWS = 4`
- `MAX_CONTENT_ROWS = 2`
- `BODY_CRAWL_LIMIT = 6`

결과:
- `inserted=6`
- `trimmed_rows=2`
- `trimmed_content=2`
- `final_rows=4`
- `final_content_rows=2`

의미:
- row 상한 초과 시 오래된 기사 삭제 동작 확인
- content 상한 초과 시 오래된 본문 `NULL` 처리 동작 확인

#### 시나리오 4. 본문 크롤링 실패 시 partial insert

결과:
- `inserted=1`
- `body_failed=1`
- `final_rows=1`
- `final_content_rows=0`

의미:
- scraper 예외가 나더라도 메타데이터 조건만 맞으면 row는 저장됨
- `content` 없이 partial insert 허용 정책 확인

#### 시나리오 5. 제목 유사도 6시간 gap 룰

결과:
- `grouped=2`
- `body_saved=2`

의미:
- 제목 토큰이 같아도 기사 시각 차이가 6시간을 넘으면 같은 그룹으로 묶지 않음
- 본문 quota 보호가 과도하게 작동하지 않는 것 확인

#### 시나리오 6. cooldown skip

결과:
- `fetched=0`
- `skipped=1`
- `inserted=0`

의미:
- `force=False`이고 마지막 실행 시각이 15분 이내면 API 호출 전에 바로 스킵

#### 시나리오 7. Redis lock 미획득 skip

결과:
- `fetched=0`
- `skipped=1`
- `inserted=0`

의미:
- lock이 이미 잡혀 있으면 동시 실행 없이 바로 종료

#### 시나리오 8. TTL 정확성

결과:
- `inserted=1`
- 저장 row의 `ttl_until == published_at + 30 days`

의미:
- TTL anchor 계산이 계획 문서 기준과 일치

#### 시나리오 9. 필터링 정책

검증 데이터:
- 정상 기사
- 종목코드 기준 통과 기사
- 7일 초과 기사
- 짧은 제목
- 무관 기사
- 광고성 기사
- 미러링 문구 기사
- 그래픽 기사
- 본문 200자 미만 기사
- 제목 근거 없이 본문에만 `name_kr`이 1회 등장하는 약한 참조 기사
- 제목 근거 없이 본문에만 `name_kr`이 2회 이상 등장하는 강한 참조 기사

결과:
- `fetched=10`
- `fetched=11`
- `inserted=3`
- `filtered=8`
- `body_saved=3`

의미:
- 필터링 정책이 실제 저장 결과에 반영됨
- 허용된 row는 정상 기사 1건 + 종목코드 기준 통과 기사 1건 + 강한 본문 참조 기사 1건만 남음
- A/B 정교화로 그래픽 기사와 약한 본문 참조 기사도 차단되고, 의도한 strong body reference는 통과함

### 3. Redis 실제 연결 및 fail-closed 검증 성공

실행:
- `docker compose up -d redis`
- `docker compose exec redis redis-cli ping`
- `python -c "import redis; print(redis.Redis.from_url('redis://localhost:6379/0').ping())"`
- `python -m scripts.validate_redis_integration`

기본 연결 확인:
- Redis 컨테이너 기동 성공 (`tickertaka-redis`, 0.0.0.0:6379)
- `redis-cli ping -> PONG`
- 호스트(WSL) 측 `redis-cli` 도 `+PONG`
- Python client `redis.Redis.from_url("redis://localhost:6379/0").ping() -> True`

검증 스크립트 구성:
- 외부 API/스크래퍼는 Fake(`FakeNaverNewsClient`, `FakeArticleScraper`) 유지
- Redis만 실연결 (`redis.Redis.from_url(settings.redis_url, decode_responses=True)`)
- 사용 ticker: `ticker_metadata`에서 자동 선택 (예: `000020 동화약품 KOSPI`)
- 각 케이스 종료 시 `news-sync:lock:{symbol}`, `news-sync:last-run:{symbol}` 명시적 cleanup
- DB는 `session.rollback()`으로 흔적 없음

검증 결과 (7/7 PASS):

| # | 케이스 | 핵심 측정값 |
| --- | --- | --- |
| 1 | `redis_ping` | `PING -> True` |
| 2 | `normal_run_releases_lock_and_sets_last_run` | `fetched=1 skipped=0 lock_after=None last_run=<ts> last_run_ttl=86400` |
| 3 | `lock_held_skips_and_preserves_holder` | `skipped=1 fetched=0 lock_value="external-holder" lock_ttl=600` |
| 4 | `lock_ttl_within_600s_window` | `captured_ttl=600 captured_value=<uuid>` |
| 5 | `cooldown_within_window_skips` | `skipped=1 fetched=0 inserted=0` (5분 전 last-run) |
| 6 | `cooldown_outside_window_runs` | `fetched=1 inserted=1 skipped=0` (16분 전 last-run) |
| 7 | `redis_unavailable_fails_closed` | `skipped=1 fetched=0 inserted=0` (포트 6390 / connect refused) |

확인된 정책:
- `SET NX EX=600`으로 lock 획득, 정상 종료 시 token 일치 확인 후 `DELETE` 해제
- 다른 holder가 잡은 lock은 우리 token과 다르므로 release 단계에서 보존됨
- `last-run` 키에 UTC timestamp + 24h TTL 기록, 15분 윈도우 판정
- Redis 통신 실패는 `try/except`로 잡혀 `_acquire_lock`이 `None` 반환 → fail-closed skip
  - 운영 관측성을 위해 `logger.exception("redis lock error; skipping sync for %s", symbol)` 트레이스가 의도적으로 출력됨
  - 검증 스크립트에서도 같은 트레이스가 한 번 보이지만 직후 `skipped_count += 1`로 정상 처리되어 케이스는 `PASS`

결론:
- Redis lock / cooldown 정책은 실제 Docker Desktop 기반 Redis에서 동작 확인됨
- 이전 문서에 남아 있던 “Redis 실연결 미검증” 상태는 해소
- fail-closed 보강까지 포함하여 Phase 1 Redis 통합 검증은 닫힌 상태

### 4. 실 API + 실 스크래퍼 기반 live sync 검증 성공

실행:
- `python -m scripts.run_live_news_sync --symbol 005930 --mode initial --limit 10`

대상:
- `005930 삼성전자`
- rollback 모드
- in-memory Redis 사용

결과:
- `fetched=10`
- `inserted=10`
- `updated=0`
- `skipped=0`
- `filtered=0`
- `body_failed=0`
- `grouped=5`
- `body_saved=5`
- `trimmed_rows=0`
- `trimmed_content=0`
- `elapsed_ms=1011`
- 최종 출력 후 `ROLLBACK`

의미:
- 실제 네이버 뉴스 API 호출 성공
- 실제 기사 본문 추출 성공
- 실제 `sync_news_for_ticker()` 경로가 DB 저장 직전까지 정상 동작
- rollback 모드로 검증했으므로 DB 오염 없음

주의:
- 쉘에서 실행한 `python -c "import ssl, requests; ..."` one-liner 실패는 코드 실패가 아니라 문자열이 줄바꿈되면서 생긴 `SyntaxError`
- live sync가 실제로 성공했으므로 HTTPS/OpenSSL 경로는 현재 실행 환경에서 동작한 것으로 봐야 함

### 5. 실데이터 기준 relevance 품질 이슈 확인

live sync 결과를 보면 `filtered=0`인데도 아래처럼 종목 관련성이 약한 기사들이 일부 포함됐다.

예:
- `"빚투 버티기 어렵다"…반대매매 917억 터져`
- `양향자 단식농성장` 관련 기사

의미:
- 현재 필터링 정책은 계획 문서상 구현됐고 테스트 데이터 기준으로도 통과했지만
- 실데이터에서는 `삼성전자`가 메타데이터 어딘가에 포함되면 통과하는 케이스가 남아 있음
- 즉 Phase 1의 “실행 가능성”은 검증됐고, 다음 개선 포인트는 relevance precision 강화

후속 조치:
- 위 노이즈를 줄이기 위해 A/B 정교화(그래픽 기사 제외, 제목 우선 매칭)를 코드에 반영
- 테스트 데이터 기준으로는 `filtered=8 / inserted=3`까지 개선 확인
- 다만 다종목 rollback live sync 비교는 이 세션 실행 환경의 HTTPS 재현성 이슈로 보류

## 현재까지 확인된 결론

계획 문서의 아래 정책은 현재 코드 기준으로 동작 확인됐다.

- `15/5/5` 기본 정책
- 최근성/제목 길이/광고/미러링/관련성/짧은 본문 필터링
- `source_url` 기준 dedupe
- 기존 `content IS NULL` row 본문 보강
- 제목 유사도 그룹화로 본문 quota 보호
- 제목 유사도 6시간 gap 룰
- 본문 크롤링 실패 시 partial insert
- cooldown skip
- Redis lock 미획득 skip
- `ttl_until = published_at + 30 days`
- 종목별 row 상한 적용
- 종목별 content 상한 적용
- 오래된 본문 `NULL` 처리

즉, 외부 API/실크롤링을 제외한 `news_cache` 핵심 적재 정책은 코드와 검증 스크립트 기준으로 Phase 1 수준까지 닫힌 상태다.
추가로 Redis 실연결과 live sync rollback 검증까지 통과했다.

## 남은 이슈

### 1. relevance precision 추가 관찰 필요

현재 상태:
- 값싼 A/B 정교화는 구현 및 테스트 완료
- 실데이터 1종목 rollback에서 보인 노이즈에 대한 1차 대응은 들어감
- 다종목 실데이터 비교는 추가 필요

의미:
- 기능상 Phase 1은 완료권
- 운영 품질 기준으로는 `관련성 precision`의 추가 관찰/튜닝이 다음 우선순위

개선 후보:
- `005930`, `000660`, `035420` 등 2~3종목 rollback live sync로 노이즈 잔존율 비교
- 필요 시 description-only 매칭 추가 완화
- 필요 시 점수제/도메인 패턴은 Phase 2에서 검토
- 짧은 `name_kr` 종목(prefix 충돌 위험) 노출도 점검 결과를 바탕으로 exact match 규칙 보강 여부 검토

사전 점검 메모:
- `SELECT symbol, name_kr, length(name_kr) ... WHERE length(name_kr) <= 3` 조회 결과, `LG`, `LS`, `CJ`, `DL`, `기아`, `한화` 등 짧은 종목명이 다수 존재
- 이런 종목은 substring count 기반 규칙에서 prefix 충돌 가능성이 있으므로 Phase 2 백로그로 관리

아직 하지 않은 것:
- watchlist 등록 후 background task 연결

## 다음 권장 순서

1. `005930`, `000660`, `035420` 등 대표 종목으로 rollback 재검증
2. 노이즈가 허용 가능하면 `--commit`으로 실제 적재 검증
3. 여전히 시끄러우면 점수제/도메인 패턴을 Phase 2로 설계
4. watchlist 등록 후 background task 연결
5. 필요 시 scheduler/worker 연결

## 변경 파일

수정:
- `app/core/db.py`
- `app/domain/news_ingestion.py`
- `requirements.txt`

추가:
- `scripts/validate_news_ingestion.py`
- `scripts/run_live_news_sync.py`
- `scripts/validate_redis_integration.py`

## Phase 3 진행: scheduler / cleanup 골격 구현

이번 단계에서 추가한 범위:
- watchlist 등록 종목 대상 `mode="refresh"` 주기 실행 서비스 추가
- `ttl_until < now()` 기준 TTL cleanup 서비스 추가
- 종목별 row/content trim sweep을 정기 실행 경로로 분리
- sweep 단위 최근 실행 시각 Redis 기록 추가
- 네이버 뉴스 API 일일 호출량 Redis 카운터 추가
- 외부 API 없이 검증 가능한 Phase 3 검증 스크립트 추가

### 구현 파일

수정:
- `app/repositories/watchlist_repository.py`
- `app/repositories/news_cache_repository.py`

추가:
- `app/domain/news_cache_scheduler.py`
- `scripts/run_news_cache_scheduler.py`
- `scripts/validate_news_cache_scheduler.py`

### 구현 내용

#### 1. watchlist symbol sweep

`WatchlistRepository.list_distinct_symbols()` 추가:
- 현재 watchlist에 등록된 종목 symbol 집합을 중복 없이 조회
- Phase 3 refresh sweep의 순회 기준으로 사용

#### 2. news cache cleanup helpers

`NewsCacheRepository`에 아래 메서드 추가:
- `list_symbols_with_cache()`
- `delete_expired_rows(now=None)`

용도:
- cleanup 시 실제 캐시가 존재하는 symbol만 순회
- `ttl_until < now()` row를 일괄 삭제

#### 3. scheduler service

`app/domain/news_cache_scheduler.py`

추가된 핵심 메서드:
- `run_watchlist_refresh(force=False, limit=None)`
  - watchlist symbol 순회
  - 각 symbol에 대해 `sync_news_for_ticker(symbol, mode="refresh")` 호출
  - symbol마다 별도 session_scope를 사용해 트랜잭션 격리
  - 집계 결과를 `RefreshSweepResult`로 반환
- `run_news_cleanup(now=None)`
  - TTL 만료 row 삭제
  - symbol별 row trim
  - symbol별 content trim
  - 집계 결과를 `CleanupSweepResult`로 반환

추가된 진입점:
- `run_scheduled_watchlist_refresh()`
- `run_scheduled_news_cleanup()`

이 함수들은 `session_scope()`를 열어 한 번의 운영 실행 단위로 바로 사용할 수 있게 구성했다.

추가 보완:
- refresh sweep은 symbol별로 별도 세션을 열어 한 symbol 실패가 이후 symbol 처리까지 전염되지 않게 했다
- refresh / cleanup 각각에 대해 `news-sync:sweep:last-run:{mode}` Redis 키를 남긴다
- last-run 값은 unix timestamp가 아니라 `KST ISO 8601` 문자열로 저장해 운영자가 `redis-cli`에서 바로 읽을 수 있게 했다

#### 3-1. 일일 API 호출량 집계

`app/domain/news_ingestion.py`

추가된 동작:
- 네이버 뉴스 API 호출 직후 `naver-api-count:YYYY-MM-DD` Redis 키를 `INCR`
- 첫 호출 시 `48시간 TTL` 설정

의미:
- plan Phase 3의 `일일 API 호출량 로그 집계`를 가장 가벼운 Redis 방식으로 충족
- 외부 로그 분석기 없이도 일별 호출량을 바로 확인 가능
- 날짜 기준은 운영/UI 기준에 맞춰 `KST(Asia/Seoul)`로 계산

#### 4. 운영 실행 스크립트

`scripts/run_news_cache_scheduler.py`

지원 모드:
- `--mode refresh`
- `--mode cleanup`
- `--mode all`

옵션:
- `--force`
- `--limit`

용도:
- 외부 cron 또는 별도 scheduler 프로세스에서 바로 호출 가능한 최소 진입점
- 출력은 Python repr이 아니라 JSON 한 줄 포맷으로 맞춤

### 검증

실행:
- `source venv/bin/activate && python -m compileall app scripts`
- `source venv/bin/activate && python -m scripts.validate_news_cache_scheduler`

검증 스크립트:
- `scripts/validate_news_cache_scheduler.py`

검증 시나리오:

0. `daily_api_counter`
- `scripts/validate_news_ingestion.py`에서 검증
- Naver 호출 1회당 Redis 일일 카운터가 증가하는지 확인
- 첫 증가 시 TTL이 설정되는지 확인

결과:
- counter=`1`
- ttl=`172800`

1. `watchlist_refresh`
- watchlist symbol 집합만 순회하는지 확인
- 각 symbol에 대해 `mode="refresh"`, `force=True`, `limit=4`로 호출되는지 확인
- 집계 결과가 호출 수와 일치하는지 확인

결과:
- `symbols=['000020', '000040']`
- `fetched=6`
- `inserted=2`
- `ALL PASSED`

2. `refresh_failure_isolation`
- 한 symbol은 강제 실패, 다른 symbol은 정상 처리되게 구성
- 한 symbol 실패가 sweep 전체를 망치지 않고 다음 symbol이 계속 처리되는지 확인

결과:
- `failed=000020`
- `processed=000040`

3. `cleanup_sweep`
- TTL 만료 row 삭제
- row 상한 초과분 trim
- content 상한 초과분 trim
- 정리 후 최종 row/content 개수가 기대값과 일치하는지 확인

결과:
- `deleted=1`
- `trimmed_rows=2`
- `trimmed_content=1`
- 정리 후 해당 symbol row=`3`, content row=`2`

4. `cleanup_no_expired`
- TTL 만료 row가 없을 때 `deleted_expired_rows=0` 확인

5. `cleanup_under_limits`
- row 수와 content 수가 상한 미만일 때 trim이 발생하지 않는지 확인

6. `empty_watchlist`
- watchlist symbol이 0건일 때 `processed=0`, `failed=0` 확인

### 현재 상태

Phase 3 핵심 명세 기준으로 아래가 닫혔다.

- watchlist 대상 refresh 실행 진입점
- TTL cleanup 실행 진입점
- row/content trim 정기 실행 경로
- sweep 단위 최근 실행 시각 기록
- 일일 네이버 API 호출량 집계
- symbol 단위 트랜잭션 격리
- fake sync + live DB 세션 기반 검증 스크립트

추가 보강:
- plan "최소 구조화 로그"의 `그룹화로 절약된 본문 크롤링 건수`를 `SyncNewsResult.body_quota_saved_count`로 노출
- sync 로그 `extra`와 refresh sweep 결과에 `body_quota_saved` 키 추가
- `scripts/validate_news_ingestion.py`에 `body_quota_saved` 시나리오 추가 (4건이 1그룹으로 묶이는 케이스, 절약 3건 PASS)

아직 남은 것:
- 실제 cron / worker 연결
- 필요 시 `DataRefreshJob` 테이블과의 연결

참고:
- `DataRefreshJob`은 현재 스키마상 `symbol NOT NULL`이라 sweep 전체를 1 row로 남기기엔 바로 맞지 않는다
- 따라서 현재는 Redis last-run 기록으로 Phase 3 실행 시각 추적을 닫고, `DataRefreshJob` 연동은 후속 확장으로 둔다

## 라이브 검증과 관련성 매칭 보강

### 1. 라이브 테스트 스크립트 추가

`scripts/live_test_watchlist_sync.py`

목적:
- watchlist 등록 → BackgroundTasks → `sync_news_for_ticker` → 네이버 API + 본문 크롤링 + `news_cache` 적재까지 한 사이클을 실제 외부 의존성에 붙여 end-to-end 검증

흐름:
- `phase2-test-user@example.com` 시드 사용자 + 검증 대상 종목(`000660` SK하이닉스 기본)
- 시작 시점에 해당 사용자/종목의 잔여 row 정리
- `TestClient`로 `POST /api/watchlists` 호출 (FastAPI `BackgroundTasks`가 sync를 동기 실행)
- 적재된 `news_cache` row 결과 출력 (총 row 수, content 보유 수, source 다양성, 상위 5건 샘플)
- 적재 후 row는 DB에 남겨두어 운영자가 직접 확인 가능
- `--cleanup` 옵션으로 watchlist + news_cache row 정리 단계 분리

로깅:
- `root` logger를 `INFO`로 올리고 `ExtraFormatter`로 sync 로그 `extra`를 한 줄에 같이 출력
- `sqlalchemy` / `urllib3` / `httpx` / `httpcore` / `asyncio`는 `WARNING`으로 억제
- sync 종료 시 `news sync finished | symbol=... fetched=... grouped=... body_quota_saved=... body_saved=... filtered=...` 형태로 카운터가 그대로 콘솔에 노출됨

### 2. 첫 라이브 결과 (000660 SK하이닉스)

```
target  : 000660 (SK하이닉스)
news sync finished | symbol=000660 fetched=15 inserted=2 updated=0 skipped=0 body_failed=0 grouped=5 body_quota_saved=10 body_saved=2 elapsed_ms=1766
total_rows=2 content_not_null=2 distinct_sources=2 (매일경제, 연합뉴스)
filtered=13
```

분해:
- 네이버 검색 15건 모두 `_passes_prefilter` 통과 (Filter A — 제목 패턴/광고/외국어 차단 단계 통과)
- 제목 유사도 Jaccard 0.7 그룹화로 그룹 5개 형성 → 그룹 대표 5건만 본문 크롤링 (`body_quota_saved=10`)
- 본문 크롤링 5건 모두 성공 (`body_failed=0`)
- `_passes_storage_filters`에서 13건 filtered, 2건 inserted
  - 본문 없는 후보 10건은 제목/요약 기준 매칭 부족
  - 본문 있는 5건 중 3건은 본문 매칭(`name_kr ≥ 2회`) 또는 200자 기준에서 컷

### 3. 매칭 누락 발견: 띄어쓰기 변형

`_contains_exact_name_reference`/`_count_exact_name_reference`가 단순 `str.__contains__` / `str.count` 기반이라 `name_kr="SK하이닉스"`와 본문 `"SK 하이닉스"` 같은 띄어쓰기 변형이 매칭되지 않음.

영향:
- 한국 매체 표기 관행상 `SK하이닉스` / `SK 하이닉스` / `삼성전자` / `삼성 전자` / `현대자동차` / `현대 자동차` 등이 매체별로 갈림
- 13건 filtered 중 상당수가 본문에 띄어쓰기 변형으로만 등장해 매칭 실패한 케이스로 추정

### 4. 옵션 1: 공백 무시 매칭 구현

`app/domain/news_ingestion.py`

추가/변경:
- `_strip_whitespace(value)` 헬퍼 추가 (`WHITESPACE_RE.sub("", value)`)
- `_contains_exact_name_reference`와 `_count_exact_name_reference` 양쪽에서 비교 직전에 양변 공백 제거
- 결과적으로 본문/제목의 띄어쓰기 변형을 정확 매칭과 동일하게 취급

다음은 영향을 받지 않는다:
- `_passes_prefilter`의 한글/심볼 존재 검사 (`_contains_hangul`, `_contains_symbol_reference`)
- 제목 유사도 그룹화의 `normalize_title` (토큰화 기반이라 별도)
- 광고/미러링 마커, 제목 패턴 차단

### 5. 검증 시나리오 추가

`scripts/validate_news_ingestion.py`에 단위 검증 시나리오 `whitespace_variant_match` 추가.

검증 케이스:
- `name_kr="SK하이닉스"` 기준
- 제목 `"SK 하이닉스 호실적 발표"` → 매칭 통과
- 본문 `"... SK 하이닉스가 ... SK 하이닉스의 ..."` (2회) → 매칭 통과
- 본문 `"... SK 하이닉스가 언급되었다."` (1회) → 매칭 실패 (2회 미만)
- 본문 `"삼성전자 ... 삼성전자 ..."` → 매칭 실패 (다른 종목)

결과:
- `validate_news_ingestion` 전체 11 시나리오 PASS (whitespace_variant_match 포함)
- `validate_news_cache_scheduler` 6 시나리오 PASS

### 6. 운영 후속 액션

- 동일 라이브 테스트 재실행 시 `filtered` 카운터 감소 / `inserted` 증가 여부 관찰
- 영문 표기 (`SK Hynix`), 한글 표기 변형 (`에스케이하이닉스`)은 여전히 매칭 불가 → 필요해지면 `ticker_metadata` alias 컬럼 도입 검토 (옵션 2, 현재는 보류)

## partial insert 보강 + 초기 적재 정책 완화

### 1. 발견 — plan과 구현의 격차

`scripts/live_test_watchlist_sync` 재실행 결과 `fetched=15 → inserted=1`이 다시 관찰되었다. 분석 결과:

- plan 본문은 "본문 크롤링 실패 시 `content` 없이 partial insert 허용 / 메타데이터만으로도 저장 가능"을 명시 (`memo/plans/news-cache-ingestion-plan.md:171-173`)
- 그러나 `_matches_ticker_reference`가 **본문이 없는 후보에 대해서는 제목 또는 metadata+body에 `symbol` 매칭만** 요구해 description에 `name_kr`이 들어 있어도 컷됨
- 결과적으로 그룹화로 본문 시도 안 한 `body_quota_saved` 후보 다수가 storage filter에서 떨어져 plan의 "partial insert 허용" 의도가 실현되지 못함

### 2. 옵션 P1 — 본문 없는 후보에만 metadata `name_kr` 매칭 허용

`app/domain/news_ingestion.py`

변경 위치: `_matches_ticker_reference`

```python
if not body_text and cls._contains_exact_name_reference(metadata_text, ticker.name_kr):
    return True
```

설계:
- 본문이 있는 후보는 기존대로 `body name_kr ≥ 2회` 기준 유지 → 노이즈 컷 강도 유지
- 본문이 없는 후보는 metadata(title + description)에 `name_kr` 1회 매칭으로 통과 허용 → partial insert 허용
- 옵션 1(공백 무시 매칭)과 시너지 → 띄어쓰기 변형도 같은 경로로 통과

본문 있는 케이스에 대한 안전성은 `body_quota_saved` / `whitespace_variant_match` 시나리오로 회귀 검증.

### 3. 초기 적재 정책 완화

운영 시나리오: 사용자가 watchlist 등록 직후 토론을 바로 시작할 때 evidence 후보가 너무 적은 문제 대응.

| 상수 | 이전 | 변경 | 사유 |
|---|---|---|---|
| `INITIAL_FETCH_COUNT` | 15 | **20** | 첫 적재 후보 풀을 5건 더 확보 |
| `REFRESH_FETCH_COUNT` | 5 | 5 (유지) | 정기 refresh는 누적이라 그대로 |
| `BODY_CRAWL_LIMIT` | 5 | 5 (유지) | 본문 quota는 비용 보호 그대로 |
| `MIN_CONTENT_LENGTH` | 200 | **120** | trafilatura 본문 추출이 부실해도 한 문단 수준이면 evidence로 사용 가능 |

네이버 일일 API 한도(25,000건) 영향:
- initial 1회 호출량만 15 → 20으로 증가
- refresh 5건/시간 그대로 → 일일 누적 호출량 변화 미미

plan 본문은 `15/5/5` + `200자`로 기록되어 있으나, 본 변경은 plan 본문의 "기본값" 정의 안에서 운영 튜닝 범위로 해석한다. plan 본문 수정은 별도 검토.

### 4. 검증 시나리오 추가

`scripts/validate_news_ingestion.py`에 단위 검증 `metadata_name_match` 추가.

검증 케이스 (`name_kr="SK하이닉스"`):
- 제목에 종목명 없음 + description에 `"SK하이닉스"` + 본문 없음 → 매칭 통과 (P1)
- 제목에 종목명 없음 + description에 `"SK 하이닉스"` + 본문 없음 → 매칭 통과 (P1 + 옵션 1)
- 제목에 종목명 없음 + description에 다른 종목명만 + 본문 없음 → 매칭 실패

### 5. 회귀 검증

- `validate_news_ingestion` 12 시나리오 모두 PASS (`metadata_name_match` 추가, `whitespace_variant_match` 유지)
- `validate_news_cache_scheduler` 6 시나리오 모두 PASS
- 본문 있는 케이스의 `name_kr ≥ 2회` 기준은 회귀 없음

### 6. 후속 관찰 포인트

- 라이브 재실행 시 `body_quota_saved` 10건 중 일부가 storage filter를 통과해 `inserted`가 늘어나는지 측정
- `MIN_CONTENT_LENGTH=120` 완화로 trafilatura 부실 추출 케이스가 살아나는지 / 노이즈 증가량은 어느 정도인지 운영 데이터로 관찰
- 노이즈가 너무 늘면 `MIN_CONTENT_LENGTH`만 다시 150~180 사이로 미세 조정 검토

## 본문 추출 실패 측정 보강

### 1. 라이브 재실행 결과의 두 번째 보틀넥

partial insert 보강 후 라이브 결과:
```
fetched=20 inserted=16 grouped=5 body_quota_saved=15 body_saved=1 filtered=4
content_not_null=1 (16 row 중 본문 보유는 1건)
```

`body_quota_saved`로 묶인 15건이 모두 적재됐다는 점에서 partial insert는 의도대로 작동. 다만 grouped 5건 중 본문이 채워진 건 1건뿐. trafilatura가 일부 매체 페이지(chosun.com, bizwnews 등)에서 raise 없이 **빈 본문을 반환**하는 케이스가 보틀넥으로 드러났다.

### 2. 측정 보강 — `body_failed` 카운터 확장

`app/domain/news_ingestion.py`의 `_scrape_candidate`가 기존에는 raise한 경우만 `body_failed_count`를 증가시켰다. 빈 본문 반환 케이스를 정확히 측정하기 위해 다음과 같이 확장:

```python
scraped = self.article_scraper.scrape(candidate.normalized_url)
if scraped is None or not (scraped.content and scraped.content.strip()):
    result.body_failed_count += 1
    logger.info("article scrape returned empty content for %s", ...)
```

이제 다음 두 케이스 모두 `body_failed`로 잡힌다:
- 스크래퍼가 예외를 던진 경우 (네트워크/파싱 오류)
- 스크래퍼가 예외 없이 빈 `content`를 반환한 경우 (trafilatura 부실 추출)

### 3. 검증 시나리오 추가

`scripts/validate_news_ingestion.py`에 `body_failed_empty_content` 시나리오 추가.

| 시나리오 | body_failed | inserted | 비고 |
|---|---|---|---|
| `partial_insert_on_scrape_failure` | 1 | 1 | scraper raise → partial insert |
| `body_failed_empty_content` | 1 | 0 | 빈 본문 반환 → storage filter 컷 |

전체 13 시나리오 PASS.

### 4. 발견 — partial insert 경로 비대칭

위 표에서 보이듯 같은 "본문 실패" 상황에서도 raise 경로는 partial insert를 받지만, 빈 본문 반환 경로는 storage filter 컷으로 row 자체가 안 들어간다. 코드 동선 차이:

- **raise** → `_scrape_candidate` None 반환 → storage filter `scraped is None` 경로 → P1 매칭으로 통과 가능
- **빈 본문 반환** → `scraped` not None 상태로 storage filter 진입 → `not scraped.content` 조건에서 컷

plan의 "본문 크롤링 실패 시 partial insert 허용" 의도 관점에서 보면 두 경로가 동일하게 partial insert를 받는 게 자연스럽다. 다만 본 단계에서는 측정 보강만 적용하고, 동작 변경은 후속 결정으로 둔다.

### 5. 후속 결정 후보

- 빈 본문 반환 케이스도 None 반환으로 통일해 partial insert 경로로 합치기 (plan 의도 정렬)
- 도메인별 `body_failed` 누적 (Redis `naver-body-fail:{host}:YYYY-MM-DD` INCR 등) → plan "추가 운영 지표 — 도메인별 본문 실패율" 충족 + blocklist 후보 발굴
- trafilatura fallback 추출기 도입 (newspaper3k 등) → 본문 적재율 직접 개선

## 그룹 내 본문 즉시 fallback (옵션 E)

### 1. 발견 배경

`body_failed_empty_content` 라이브 결과(`chosun.com` 2건이 빈 본문)에서 해당 URL을 브라우저로 직접 열어보니 `403 Forbidden`이 반환됨. 즉 trafilatura의 추출 능력 문제가 아니라 **매체 측에서 우리 요청을 차단한 것**. trafilatura fallback 추출기(C)나 도메인 blocklist(D)로는 해결되지 않는 케이스다 — 응답 자체에 본문이 없기 때문.

사용자 의도: "그룹이 5개로 나뉘었으니 최소 그만큼 본문이 들어와야 한다".

### 2. plan 정신과 즉시 fallback

plan 본문 (`memo/plans/news-cache-ingestion-plan.md:305-307`):
```
실패 처리:
- 403, 429, timeout, parsing 실패 시 재시도 없이 partial insert
- 동일 그룹 내 대표 본문이 실패하면 다음 refresh 주기에 같은 그룹의 다른 후보를 우선 시도
```

plan은 "다음 refresh 주기"라 했지만, 첫 watchlist 등록 시 토론을 바로 시작하는 운영 시나리오에서는 한 시간 뒤 회복은 너무 늦다. 같은 sync 사이클 안에서 그룹 내 다음 후보로 즉시 fallback하는 정책으로 보강한다.

### 3. 구현

`app/domain/news_ingestion.py`

추가/변경:
- `BODY_ATTEMPTS_PER_GROUP = 3` 상수 추가 (그룹당 최대 본문 시도 횟수)
- `SyncNewsResult.body_attempts_count` 추가 (실제 본문 크롤링 시도 횟수)
- `_select_body_candidates` → `_select_body_candidate_groups`로 이름/반환 타입 변경
  - 반환 타입: `list[list[NewsCandidate]]` (그룹별 후보 리스트의 리스트)
  - 그룹 외부 길이 ≤ `BODY_CRAWL_LIMIT` (= 5)
  - 그룹 내부 길이 ≤ `BODY_ATTEMPTS_PER_GROUP` (= 3)
  - 그룹 내 후보는 published_at 내림차순 + originallink 우선 정렬을 그대로 상속
- `sync_news_for_ticker` 본문 처리 루프를 그룹 순회로 변경:
  ```python
  for group in body_candidate_groups:
      for candidate in group:
          result.body_attempts_count += 1
          scraped = self._scrape_candidate(candidate, result)
          if scraped is not None and scraped.content and scraped.content.strip():
              body_urls[candidate.normalized_url] = scraped
              break
  ```
  본문이 채워지면 그 그룹은 즉시 break, 다음 그룹으로 진행.

`app/domain/news_cache_scheduler.py`

- `RefreshSweepResult.body_attempts_count` 추가
- `_merge_sync_result`와 sweep 로그 `extra`에 `body_attempts` 키 노출

### 4. 카운터 의미 정리

| 카운터 | 의미 |
|---|---|
| `grouped_count` | 본문 시도 대상 그룹 수 (≤ BODY_CRAWL_LIMIT) |
| `body_quota_saved_count` | `candidates - grouped_count` — 그룹화 효과로 본문 시도 그룹에 포함되지 않은 후보 수 |
| `body_attempts_count` | **실제 본문 크롤링 시도 횟수** — 그룹당 1~3회 누적 |
| `body_failed_count` | 본문 추출 실패 시도 횟수 (raise + 빈 본문) |
| `body_saved_count` | 본문이 row에 적재된 건수 (= 본문 있는 그룹 수) |

운영 관찰:
- `body_attempts > grouped` → fallback이 발동된 횟수만큼 차이가 벌어짐
- `body_failed > 0`이면서 `body_saved`가 그룹 수와 같으면 fallback이 회복 효과를 보고 있음

### 5. 검증 시나리오 추가

`scripts/validate_news_ingestion.py`에 `body_fallback_within_group` 추가.

구성:
- 4건 후보, 같은 그룹으로 묶이는 유사 제목
- 1번째 URL: 빈 본문 반환 (404/차단 시뮬레이션)
- 2번째 URL: 정상 본문
- 3번째 URL: 정상 본문 (사용 안 됨, 2번에서 break)
- 4번째 후보: `BODY_ATTEMPTS_PER_GROUP=3` 초과로 그룹에 들어가지 못함

PASS 결과:
- `grouped=1`
- `body_failed=1` (1번 빈 본문)
- `body_attempts=2` (1번 실패 → 2번 성공 break)
- `body_saved=1` (2번 본문 적재)
- `body_quota_saved=3` (4 candidates - 1 group)
- `inserted=4` (모두 적재, 1건 본문 있음 + 3건 partial)

### 6. 회귀 검증

- `validate_news_ingestion` 14 시나리오 모두 PASS
- `validate_news_cache_scheduler` 6 시나리오 모두 PASS
- 기존 `body_quota_saved` / `title_similarity_6h_gap` / `filtering_policy` 등 회귀 없음

### 7. 후속 관찰 포인트

- 라이브 재실행 시 `body_attempts`가 `grouped`보다 큰지 (fallback 발동 빈도)
- chosun.com 같은 차단 도메인이 첫 시도일 때 두 번째 매체로 회복되는 비율
- `BODY_ATTEMPTS_PER_GROUP=3`이 적절한지 (5~7 그룹 sync에서 외부 매체 호출 횟수 부담 측정)

## storage filter 컷 fallback (옵션 E')

### 1. E 적용 후 라이브 관찰

E 적용 후 라이브 재실행 결과:
```
fetched=20 grouped=5 body_quota_saved=15
body_attempts=5 body_failed=0 body_saved=1 filtered=4
```

`body_failed=0` + `body_attempts=5` → 그룹당 1번씩만 시도되고 모두 본문 추출은 성공했지만 storage filter에서 4건이 컷됐다. E의 fallback 조건이 "본문 추출 실패"만이라 트리거 자체가 안 일어남.

또 한 가지 발견: chosun.com 본문 URL을 브라우저로 직접 열면 `403 Forbidden`이라 trafilatura의 추출 능력 문제가 아니라 매체 측 봇 차단. fallback 추출기(C)나 도메인 blocklist(D)로는 풀리지 않는 케이스.

### 2. 정책 조정

| 항목 | 이전 | 변경 |
|---|---|---|
| `BODY_CRAWL_LIMIT` | 5 | 5 (유지) — 본문 적재 자연 상한 5건 |
| `_passes_storage_filters` 호출 위치 | 적재 루프에서만 | **fallback 루프에서도 호출** → 컷이면 같은 그룹 다음 후보로 fallback |

본문 적재 상한은 plan 본문(`BODY_CRAWL_LIMIT = 5`)을 그대로 유지. fallback 트리거만 확장.

### 3. 구현

`app/domain/news_ingestion.py` 본문 처리 루프:

```python
body_urls: dict[str, ScrapedArticle] = {}
for group in body_candidate_groups:
    for candidate in group:
        result.body_attempts_count += 1
        scraped = self._scrape_candidate(candidate, result)
        if scraped is None or not (scraped.content and scraped.content.strip()):
            continue
        if not self._passes_storage_filters(ticker, candidate, scraped):
            continue
        body_urls[candidate.normalized_url] = scraped
        break
```

이제 fallback 트리거 조건이 두 가지:
- 본문 추출 실패 (raise + 빈 본문) — 기존
- 본문은 가져왔지만 storage filter 컷 — **신규**

같은 그룹에 후보가 더 있으면 다음으로 넘어가고, 본문 성공 + storage filter 통과면 break.

### 4. 적재 루프와의 관계

fallback 루프에서 컷된 후보는 `body_urls`에 들어가지 않는다. 적재 루프 (`for candidate in candidates`)에서 `scraped=None`으로 처리되고, **본문 없는 후보의 P1 경로**(metadata에 `name_kr` 매칭)를 다시 탄다.

- 그룹 우승자(본문 성공 + 매칭 통과): `body_urls`에 있음 → 본문 있는 row 적재 (`body_saved += 1`)
- 그룹 비우승자(시도했지만 컷됐거나 시도 자체 안 됨): `body_urls`에 없음 → P1 매칭 → 통과 시 partial insert, 실패 시 `filtered`

본문 짧음(`MIN_CONTENT_LENGTH=120` 미달) 케이스도 fallback 루프에서 컷 → 적재 루프에서 본문 없는 P1 경로 → 제목/메타에 종목 매칭 있으면 partial insert로 살아남는다. 이건 `filtering_policy` 시나리오 갱신으로 확인된 의도된 변화.

### 5. 검증 시나리오 추가

`scripts/validate_news_ingestion.py`에 `body_fallback_on_storage_cut` 추가.

구성:
- 4건 후보, 같은 그룹으로 묶이는 유사 제목 (`build_neutral_item` 패턴: 제목에 `ticker.symbol` 없이 한글만)
- 1번 본문: `name_kr` 1회 매칭 (Filter B의 `≥2회` 기준 미달) → storage filter 컷
- 2번 본문: `name_kr` 2회 매칭 → 통과

PASS 결과:
- `grouped=1`
- `body_failed=0` (추출은 성공)
- `body_attempts=2` (1번 컷 → 2번 성공 break)
- `body_saved=1`
- `inserted=1`, `filtered=3`

### 6. 기존 시나리오 영향과 갱신

`filtering_policy` 시나리오 기대값 갱신:
- `inserted` 3 → **4** (`short_body_item`이 partial insert로 살아남음)
- `filtered` 8 → **7** (prefilter 7건만)
- `body_saved` 3 (변화 없음)
- `saved_urls`에 `short_body` URL 추가

`initial_insert` 시나리오는 `service.BODY_CRAWL_LIMIT = 5` 명시 override로 기존 의도 보존.

### 7. 회귀 검증

- `validate_news_ingestion` **15 시나리오** 모두 PASS (신규 `body_fallback_on_storage_cut` 포함)
- `validate_news_cache_scheduler` **6 시나리오** 모두 PASS

### 8. 운영 측면 — 본문 적재 상한 5건 유지

`BODY_CRAWL_LIMIT=5`는 plan 본문 그대로 유지. 그룹당 1건 적재 원칙으로 sync 1회 본문 row는 최대 **5건**.

부담 계산:
- sync 1회 본문 시도 최대: `BODY_CRAWL_LIMIT × BODY_ATTEMPTS_PER_GROUP = 5 × 3 = 15회`
- 실제로는 첫 시도 성공이 다수라 `body_attempts`는 보통 그룹 수와 비슷한 수준
- 외부 매체 호출은 도메인 분산되므로 종목당 sync 1회 15회 / 매체 분산 시 매체당 1~3회

E'의 효과는 같은 그룹에 다른 매체 후보가 있을 때 발휘된다. 그룹이 단일 매체로 구성된 경우는 fallback 발동 안 됨 → 그 그룹은 본문 없이 partial insert 경로로 처리.

## 검색 관련도 정렬로 전환 (sort=date → sort=sim)

### 1. E' 적용 후 라이브 관찰

```
fetched=20 grouped=5 body_quota_saved=15 body_attempts=6 body_failed=0 body_saved=1~2 filtered=0
total_rows=20 content_not_null=1~2
```

`body_attempts > grouped` → fallback 발동 확인. 그러나 `content_not_null`이 여전히 1~2건. 본질적 원인:
- 검색이 `sort=date`(최신순)이라 검색 결과가 **그 시점 가장 뜨거운 시장 이슈(삼성전자 노사 합의)** 중심으로 차서, SK하이닉스를 메인으로 다룬 기사가 결과에 적게 들어옴
- 본문 시도 6번 중 5번이 Filter B의 `name_kr ≥ 2회` 미달 (산업/비교 기사라 SK하이닉스 단독 mention)

### 2. 정책 전환

| 항목 | 이전 | 변경 |
|---|---|---|
| `search_news` 호출 시 `sort` | `"date"` | **`"sim"`** (네이버 관련도 정렬) |

`sort=sim`은 검색어와 본문/제목 관련도 높은 기사를 위로 올린다. SK하이닉스를 메인으로 다룬 기사가 결과 상위로 올라올 가능성이 높음.

plan 본문 (`memo/plans/news-cache-ingestion-plan.md:72-75`)은 `sort=date` 명시. 본 변경은 운영 튜닝 범위로 보고 plan 본문은 그대로 두되, 보고서에 변경 사실 기록.

### 3. 7일 컷은 그대로 유지

`MAX_ARTICLE_AGE_DAYS = 7`은 `_passes_prefilter`에서 그대로 동작. `sort=sim`이라도 published_at이 7일 초과면 prefilter에서 컷. 단, sort=sim은 관련도 우선이라 오래된 관련도 높은 기사가 결과에 섞일 수 있어 **prefilter에서 stale로 컷되는 비율이 늘 수 있음** → `filtered` 카운트 증가 가능. 이는 정상 동작이며 plan의 "최근 7일" 정책과 일치.

### 4. 회귀 검증

- `validate_news_ingestion` 15 시나리오 모두 PASS (`FakeNaverNewsClient`는 sort 옵션을 무시하고 fixture를 그대로 반환하므로 시나리오 결과는 동일)
- `validate_news_cache_scheduler` 6 시나리오 모두 PASS

### 5. 운영 후속 관찰 포인트

- 라이브 재실행 시 `content_not_null` 비율이 늘어나는지 (관련도 우선 효과)
- `filtered`가 늘었다면 stale 컷 비율 증가인지 본문 매칭 컷 증가인지 분해
- SK하이닉스 외 다른 종목(삼성전자, 카카오 등)에서도 동일 효과 확인되는지
