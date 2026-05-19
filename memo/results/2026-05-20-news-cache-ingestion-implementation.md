# 2026-05-20 News Cache Ingestion 구현 결과

## 작업 범위

이번 작업은 `memo/plans/news-cache-ingestion-plan.md`의 구현 진행과 검증에 집중했다.

이번 턴에서 진행한 범위:
- 동기 SQLAlchemy 세션이 실제 `.env.local`의 PostgreSQL URL로 연결되도록 정리
- `news_ingestion.py`에 기사 선택 기준 기반 필터링 정책 구현
- `sync_news_for_ticker(symbol)` 정책 검증 스크립트 작성
- `news_cache`의 insert / update / content 보강 / content NULL 처리 / row trim 정책 검증
- 설치된 `trafilatura` / `redis` 버전 기준 import 및 검증 재확인

이번 턴에서 의도적으로 보류한 것:
- 네이버 뉴스 API 실호출 검증
- 실제 기사 본문 크롤링 검증
- watchlist 등록 후 background task 연결
- Redis 실서버 연결 기반 lock/cooldown 검증

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
- 본문 200자 미만 기사

결과:
- `fetched=8`
- `inserted=2`
- `filtered=6`
- `body_saved=2`

의미:
- 필터링 정책이 실제 저장 결과에 반영됨
- 허용된 row는 정상 기사 1건 + 종목코드 기준 통과 기사 1건만 남음

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

## 남은 이슈

### 1. 실운영 연결 검증은 아직 미완료

아직 하지 않은 것:
- 네이버 뉴스 API 실호출
- 실제 기사 페이지 본문 추출
- Redis 실제 lock/cooldown 검증
- watchlist 등록 후 background task 연결

## 다음 권장 순서

1. Redis 실제 연결 확인
2. 실 API 기반 `sync_news_for_ticker(symbol)` 검증
3. watchlist 등록 후 background task 연결
4. 필요 시 scheduler/worker 연결

## 변경 파일

수정:
- `app/core/db.py`
- `app/domain/news_ingestion.py`
- `requirements.txt`

추가:
- `scripts/validate_news_ingestion.py`
