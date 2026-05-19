# 2026-05-20 News Cache Ingestion 구현 결과

## 작업 범위

이번 작업은 `memo/plans/news-cache-ingestion-plan.md`의 구현 진행과 검증에 집중했다.

이번 턴에서 진행한 범위:
- 동기 SQLAlchemy 세션이 실제 `.env.local`의 PostgreSQL URL로 연결되도록 정리
- `sync_news_for_ticker(symbol)` 정책 검증 스크립트 작성
- `news_cache`의 insert / update / content 보강 / content NULL 처리 / row trim 정책 검증

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

### 2. news ingestion 검증 스크립트 추가

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

## 현재까지 확인된 결론

계획 문서의 아래 정책은 현재 코드 기준으로 동작 확인됐다.

- `15/5/5` 기본 정책
- `source_url` 기준 dedupe
- 기존 `content IS NULL` row 본문 보강
- 제목 유사도 그룹화로 본문 quota 보호
- 종목별 row 상한 적용
- 종목별 content 상한 적용
- 오래된 본문 `NULL` 처리

즉, 외부 API/실크롤링을 제외한 `news_cache` 핵심 적재 정책은 코드 레벨에서 한 번 검증된 상태다.

## 남은 이슈

### 1. 새 의존성 설치 실패

설치 대상:
- `trafilatura==1.12.2`
- `redis==5.0.8`

현재 상태:
- `pip install` 실패

원인:
- `pyenv` 기반 Python 3.12가 `OPENSSL_3.3.0` 기준으로 빌드되어 있음
- 현재 시스템 `libcrypto.so.3`와 버전이 맞지 않음
- 결과적으로 `pip`에서 SSL 사용 불가처럼 보이는 상태

실패 형태:
- `ImportError: /usr/lib/x86_64-linux-gnu/libcrypto.so.3: version 'OPENSSL_3.3.0' not found`
- `pip is configured with locations that require TLS/SSL, however the ssl module is not available`

영향:
- 실 API 기반 Naver 호출 검증 불가
- `trafilatura` 기반 실본문 크롤링 검증 불가
- Redis 실연결 검증 불가

### 2. 실운영 연결 검증은 아직 미완료

아직 하지 않은 것:
- 네이버 뉴스 API 실호출
- 실제 기사 페이지 본문 추출
- Redis 실제 lock/cooldown 검증
- watchlist 등록 후 background task 연결

## 다음 권장 순서

1. `pyenv` Python/OpenSSL 문제 해결
2. `pip install -r requirements.txt` 재실행
3. 실 API 기반 `sync_news_for_ticker(symbol)` 검증
4. watchlist 등록 후 background task 연결
5. 필요 시 scheduler/worker 연결

## 변경 파일

수정:
- `app/core/db.py`

추가:
- `scripts/validate_news_ingestion.py`
