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
