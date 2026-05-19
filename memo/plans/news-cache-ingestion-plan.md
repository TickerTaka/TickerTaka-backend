# News Cache 적재 계획

## 목표

사용자가 관심 종목을 추가하면 해당 종목 관련 뉴스를 수집하고, 가공한 뒤 `news_cache` 테이블에 적재한다.  
데이터 소스는 `네이버 뉴스 검색 API + 기사 본문 크롤링` 조합을 기본으로 한다.

핵심 원칙:
- 요청-응답 경로에서 긴 크롤링을 직접 수행하지 않는다.
- 뉴스는 영구 보관 데이터가 아니라 분석용 캐시로 취급한다.
- 완전 중복 기사 저장은 막되, 같은 이슈의 타 언론 기사까지 과도하게 제거하지 않는다.
- 저장 단계의 dedupe와 표시/분석 단계의 다양성 제어를 분리한다.
- 초기 구현은 한국 종목 우선으로 제한하고, 해외 종목은 후속 확장 범위로 둔다.

## 검증 완료 내용

확인 완료 테이블:
- `news_cache`
- `watchlist`
- `ticker_metadata`

`news_cache` 실제 스키마:
- `id UUID PRIMARY KEY DEFAULT gen_random_uuid()`
- `symbol VARCHAR(30) NOT NULL`
- `title TEXT NOT NULL`
- `content TEXT NULL`
- `summary TEXT NULL`
- `source_name VARCHAR(150) NULL`
- `source_url VARCHAR(2048) NOT NULL`
- `published_at TIMESTAMP NULL`
- `retrieved_at TIMESTAMP NOT NULL DEFAULT now()`
- `ttl_until TIMESTAMP NULL`

`news_cache` 제약 및 인덱스:
- `source_url` unique index 존재
- `(symbol, published_at DESC)` index 존재
- `(symbol, ttl_until)` index 존재
- `news_cache.symbol -> ticker_metadata.symbol ON DELETE CASCADE`

연관 스키마:
- `watchlist.symbol VARCHAR(30) NOT NULL`
- `watchlist.user_id UUID NOT NULL`
- `watchlist` unique `(user_id, symbol)`
- `watchlist.symbol -> ticker_metadata.symbol`
- `ticker_metadata.symbol`이 종목 기준키
- `ticker_metadata.name_kr`, `name_en`을 뉴스 검색어 생성에 활용 가능

결론:
- 현재 스키마만으로 `symbol` 기반 뉴스 적재 구현 가능
- 초기 구현에 필수적인 migration은 없음
- 향후 고도화용 컬럼은 별도 확장으로 검토

## 확정 정책

### 1. 관심 종목 추가 시 적재 건수

기본값:
- 최초 적재: 최근 뉴스 `15건`
- 재수집 시 조회 건수: 최근 뉴스 `5건`
- 조회 대상 기간: 최근 `7일`
- 1회 본문 크롤링 상한: `5건`
- 종목당 최대 캐시 row 수: `100건`
- 종목당 `content IS NOT NULL` 기사 상한: `10건`

운영 원칙:
- 첫 토론 UX를 위해 초기 운영 기본값은 `15/5/5`
- 더 보수적인 운영이 필요하면 `10/3/3`으로 하향 가능
- 첫 1시간 내 본문 확보량을 충분히 확보하되, 본문 크롤링 비용은 quota 보호 정책으로 통제한다

네이버 뉴스 API 사용 정책:
- `display=15`, `sort=date`, `start=1`을 기본값으로 사용
- 재수집은 `display=5`, `sort=date`, `start=1`
- `title`, `description`에 포함된 `<b>...</b>` 태그는 제거 후 저장
- `pubDate`는 RFC 822 포맷으로 파싱
- 네이버 검색 API 일일 호출 한도는 25,000회이므로 종목 수와 주기를 기준으로 사용량을 관측한다

검색어 정책:
- 초기 구현은 종목당 단일 기본 검색어를 사용
- 한국 종목은 `ticker_metadata.name_kr` 우선
- 해외 종목은 정식 지원 범위 밖으로 두고, 추후 별도 소스 전략을 둔다
- `"A" OR "B"` 형태의 검색어는 초기 구현에서 사용하지 않는다

### 2. 중복 제거 기준

저장 단계:
- `source_url` 기준 완전 중복 제거
- 같은 URL 재수집은 새 row를 만들지 않음

현재 스키마 기준 결론:
- `source_url` unique index를 1차 dedupe 기준으로 사용
- `content_hash` 컬럼은 없으므로 유사 본문 dedupe는 저장 단계에서 강제하지 않음
- 기존 row의 `content`가 비어 있으면 본문 보강 대상로 다시 시도할 수 있음
- 기존 row에 `content`가 이미 있으면 제목/요약/본문은 덮어쓰지 않음

재배포 기사 대응:
- `source_url` unique만으로는 재배포 기사 dedupe가 불가능하므로, 본문 크롤링 직전 제목 유사도 기반 그룹화를 수행한다
- 목적은 저장 dedupe가 아니라 본문 크롤링 quota 보호다
- 저장은 모든 신규 row를 유지하고, 본문 크롤링만 그룹당 1건으로 제한한다

중요한 구분:
- 저장 단계 dedupe:
  - 완전 중복 제거
  - 동일 기사 재수집 방지
- 본문 quota 보호:
  - 제목 유사도 그룹화로 재배포 기사 묶음당 1건만 본문 크롤링
- 표시/분석 단계 diversity control:
  - 같은 이슈 기사가 너무 많으면 묶어서 대표 기사만 우선 사용
  - 나머지는 관련 기사로 보조 활용

### 3. 갱신 주기

기본값:
- 관심 종목 추가 시: 비동기 수집 `1회 즉시 실행`
- 정기 갱신: `1시간`
- 강제 재수집 최소 간격: `15분`

운영 방향:
- 시장 시간 중에는 필요 시 `30분`으로 단축 가능
- 같은 `symbol`에 대해 이미 실행 중인 작업이 있으면 중복 실행 방지
- 최근 실행 시각은 Redis key로 관리
- 동시 실행 방지는 Redis lock (`SET NX EX`)을 기본안으로 사용
- `force=True`는 15분 최소 간격만 무시하며 Redis lock은 우회하지 않음
- 주기 갱신 대상은 watchlist에 현재 존재하는 종목으로 제한
- 사용자가 watchlist에서 종목을 삭제하면 해당 종목의 정기 갱신은 중단
- 장시간 미사용 종목은 추후 주기 완화 대상이 될 수 있음

### 4. 삭제 정책

기본값:
- TTL: `30일`
- UI/토론 기본 활용 범위: 최근 `7일`
- `ttl_until = published_at + 30 days`
- `published_at`이 없으면 `retrieved_at + 30 days` fallback 사용

정리 작업:
- 매일 1회 cleanup job 실행
- `ttl_until < now()`이면 삭제
- `symbol`별 row 수가 `100건`을 초과하면 오래된 기사부터 초과분 삭제
- `symbol`별 `content IS NOT NULL` row 수가 `10건`을 초과하면 가장 오래된 본문부터 `content = NULL` 처리
- "오래된" 기준은 `published_at ASC NULLS LAST`이며, `published_at`이 NULL인 row끼리는 `retrieved_at ASC`로 정렬

재수집 처리 방침:
- 같은 `source_url`이 다시 발견되면 기존 row를 유지
- TTL은 기사 시각 기준으로 계산하므로 재수집 시 임의로 연장하지 않음
- 기존 row의 `content IS NULL`이면 본문 보강 업데이트는 허용

## 저장 전략

최소 저장 필드:
- `symbol`
- `source_name`
- `source_url`
- `title`
- `summary`
- `content`
- `published_at`
- `retrieved_at`
- `ttl_until`

필드 매핑 기준:
- `symbol`: 관심 종목 심볼
- `title`: 네이버 뉴스 제목 정제본
- `summary`: 본문 크롤링 성공 시 본문 기반 요약, 실패 시 네이버 description 정제본
- `content`: 본문 크롤링 성공 시 기사 본문
- `source_name`: 언론사명
- `source_url`: `originallink` 우선, 없으면 `link` fallback
- `published_at`: 네이버 API `pubDate` 파싱값
- `retrieved_at`: DB default `now()` 사용
- `ttl_until`: 기사 시각 기준 30일 후 시각

실패 허용 범위:
- 본문 크롤링 실패 시 `content` 없이 partial insert 허용
- 네이버 뉴스 API 메타데이터만으로도 저장 가능

본문 보유 상한 정책:
- 종목당 `content IS NOT NULL` 기사 수는 최대 `10건`
- 상한 초과 시 가장 오래된 본문부터 `content = NULL` 처리
- row 자체는 유지하여 dedupe와 메타데이터 활용은 계속 가능
- 본문 없는 기존 row는 이후 refresh에서 다시 본문 보강 대상이 될 수 있음
- 단, 본문 보강 우선순위는 신규 row 우선이고, 기존 `content IS NULL` row는 남는 quota에서만 보강한다

URL 정규화 규칙:
- 가능하면 `originallink`를 저장
- 없거나 비정상이면 `link`를 저장
- `utm_*`, `fbclid`, `gclid`, `ref` 등 추적성 querystring 제거
- fragment 제거
- scheme/host 소문자 통일
- `www.` 제거
- trailing slash 통일

## 기사 선택 기준

저장 필터:
- 한국어 기사 우선
- 최근 7일 이내
- 종목명과 직접 관련된 기사
- 제목/본문 길이 최소 기준 통과
- 광고성, 미러링, 빈 본문 제외

예시 검색어:
- `삼성전자`
- 종목코드는 초기 구현에서 후처리 검증용으로만 사용

관련성 검증 기준:
- 제목 또는 본문에 `ticker_metadata.name_kr` 정확 매칭이 1회 이상 있어야 함
- 제목 길이 최소 `8자`
- 본문 확보 시 본문 길이 최소 `200자`
- 다른 엔티티로 오탐되는 케이스는 exact match 규칙으로 제외

종목코드 보조 활용:
- `ticker_metadata.symbol`(예: `005930`)이 제목 또는 본문에 등장하면 관련성 가점으로 처리
- `name_kr` 매칭이 애매한 경우(부분 매칭 등) 종목코드 정확 매칭이 있으면 통과 처리
- 종목코드만으로는 검색어로 사용하지 않고, 후처리 검증에만 사용

## 이슈 다양성 제어 (후속 분석 단계)

본 섹션은 `news_cache` 저장 단계가 아닌 **후속 분석 단계(토론 evidence 선정 등)** 에 적용되는 가이드라인이다.
저장 단계는 본문 quota 보호(옵션 A)로 끝나고, 저장된 row 중에서 evidence를 선정할 때 본 정책이 동작한다.

저장 단계에서는 같은 이슈의 타 언론 기사도 저장 가능하게 둔다.  
대신 후속 단계에서 clustering이나 ranking으로 다양성을 제어한다.

권장 정책:
- 한 클러스터당 대표 기사 최대 `2건`
- 전체 evidence 후보 `6~8건`
- 서로 다른 클러스터 최소 `3개` 이상 확보 시 우선 채택

의도:
- 같은 사건의 기사만 과도하게 모이는 문제 방지
- 토론 evidence와 요약 입력에 이슈 다양성 확보

구현 시점:
- 본 plan의 범위는 아니며, 토론 도메인 구현 phase에서 채택

## 트리거 및 실행 구조

권장 구조:
1. 사용자가 관심 종목 추가
2. `watchlist` 저장
3. `symbol` 기준 뉴스 수집 작업 enqueue
4. 백그라운드 작업이 실제 수집 수행
5. `news_cache` 저장 완료 후 화면에서 조회

핵심:
- `watchlist` API는 트리거 역할
- 실제 수집 로직은 별도 함수/서비스가 담당

초기 구현:
- FastAPI `BackgroundTasks`로 시작 가능

이후 확장:
- Redis 기반 worker 또는 scheduler 분리

정기 실행 주체:
- 초기 cleanup/refresh는 외부 cron 또는 별도 스케줄러 프로세스로 실행
- API 프로세스 내부에서 장기 스케줄을 직접 돌리는 방식은 피한다

트랜잭션 원칙:
- watchlist insert
- DB commit 성공
- 이후 background task enqueue
- commit 실패 시 background task는 등록하지 않음

## 수집 함수 역할

핵심 함수 이름:
- `sync_news_for_ticker(symbol)`

권장 시그니처:
- `sync_news_for_ticker(symbol: str, mode: str = "initial", force: bool = False, limit: int | None = None)`

역할:
- 종목 정보 조회
- 네이버 뉴스 API 호출
- 검색 결과 정규화
- `source_url` 기준 중복 확인
- 신규 기사 또는 본문 없는 기존 기사 중 우선순위 높은 기사 본문 크롤링
- `news_cache` insert/upsert
- 실행 결과 반환

파라미터 의미:
- `mode="initial"`: 최초 적재 정책 사용
- `mode="refresh"`: 재수집 정책 사용
- `force=True`: 15분 최소 간격만 무시
- `limit`: 운영 오버라이드용

반환 예시:
- 조회 건수
- 저장 건수
- 중복 스킵 건수
- 본문 크롤링 실패 건수
- 제목 유사도 그룹 수
- 본문 quota 절약 건수
- 소요 시간(ms)

## 본문 크롤링 정책

초기 구현 기본값:
- HTTP timeout: connect `3s`, read `7s`
- User-Agent 명시
- 동시 본문 크롤링: symbol당 최대 `2건`
- 크롤링 간 짧은 간격 유지

실패 처리:
- `403`, `429`, timeout, parsing 실패 시 재시도 없이 partial insert
- 동일 그룹 내 대표 본문이 실패하면 다음 refresh 주기에 **같은 그룹의 다른 후보**를 우선 시도
- 같은 후보를 다시 시도하는 것은 `content IS NULL` 상태인 신규 row가 없을 때로 한정
- 반복 실패 도메인은 blocklist 후보로 로그 축적

본문 추출:
- 기사 본문은 전용 추출기 wrapper를 통해 처리
- 초기 구현 라이브러리 1순위는 `trafilatura`
- `app/external/article_scraper.py`에서 wrapper 형태로 사용

제목 유사도 그룹화 옵션 A:
- 본문 크롤링 직전에 제목 유사도 기반 in-memory 그룹화를 수행
- 목적은 재배포 기사로 인한 본문 크롤링 quota 낭비 방지
- 저장 자체는 모든 신규 row를 그대로 유지

알고리즘:
1. 제목 정규화
   - HTML 태그 제거
   - HTML entity decode
   - 특수문자 제거 또는 공백 치환
   - 연속 공백 정리
   - 영문 소문자 통일
   - 공백 기준 토큰화
   - 1글자 토큰 제거
2. 유사도 계산
   - token set 기반 Jaccard similarity 사용
   - 임계값 `0.7`
   - 토큰 수 `5` 미만 제목은 그룹화 비교 대상에서 제외
3. 비교 범위
   - 이번 sync의 신규 후보들
   - 같은 `symbol`의 최근 24시간 내 기존 row 제목
4. 그룹별 본문 크롤링 대상 선정
   - 기존 row에 `content`가 있는 그룹은 신규 기사 본문 크롤링 제외
   - 그룹당 최대 1건만 본문 크롤링
   - 우선순위: 신규 row > `published_at` DESC > `originallink` 존재
5. quota 사용 정책
   - 본문 크롤링 상한 `5건`은 그룹당 1건 합산 기준
   - 남는 quota가 있을 때만 기존 `content IS NULL` row 보강에 사용

효과:
- 동일 내용 재배포 기사 5건이 quota 5건을 모두 소모하는 문제 방지
- 본문 quota를 서로 다른 이슈 확보에 더 많이 사용 가능

## 시간대 정책

현재 DB 컬럼은 `TIMESTAMP`다.

저장 기준:
- 네이버 `pubDate`는 timezone-aware로 파싱
- 내부에서는 UTC로 변환
- DB에는 UTC 기준 naive timestamp로 저장
- 애플리케이션 표시 단계에서 KST로 변환

정렬 기준:
- `published_at`가 NULL일 수 있으므로 조회 시 `NULLS LAST`를 명시하는 쿼리를 사용

## 구현 단계

### Phase 1. 수집 함수 최소 버전

목표:
- `sync_news_for_ticker(symbol)` 구현

처리 순서:
1. `ticker_metadata`에서 종목 정보 조회
2. Redis lock 획득 및 최근 실행 시각 확인
3. 네이버 뉴스 API 호출
4. 결과 15건 정규화
5. `source_url` 정규화 및 중복 확인
6. 본문 크롤링 대상 선정
   - 6-1. 신규 후보 제목 정규화
   - 6-2. 같은 `symbol`의 최근 24시간 기존 row 제목 로딩
   - 6-3. Jaccard `0.7` 임계로 그룹화
   - 6-4. 그룹당 최대 1건 선정
   - 6-5. 우선순위: 신규 row > `published_at` DESC (NULLS LAST) > `originallink` 존재
   - 6-6. 최대 5건까지 본문 크롤링
7. `news_cache` 저장 또는 기존 row 본문 보강
8. `ttl_until` 설정
9. 종목별 row 수 100건 초과분 정리
10. 종목별 본문 보유 수 10건 초과분의 오래된 본문 `NULL` 처리
11. 실행 결과 로그 기록

### Phase 2. 트리거 연결

목표:
- watchlist 등록 직후 뉴스 수집 1회 실행

처리 순서:
1. watchlist insert 성공
2. DB commit 성공
3. background task enqueue
4. `sync_news_for_ticker(symbol)` 호출

### Phase 3. 정기 갱신/정리

목표:
- 캐시 최신성 유지

처리 순서:
1. 1시간 주기로 watchlist 등록 종목 순회하며 `sync_news_for_ticker(symbol, mode="refresh")` 호출
2. 30일 TTL cleanup (`ttl_until < now()` 삭제)
3. 종목별 row 100건 초과분 정리 (`published_at ASC NULLS LAST` 기준)
4. 종목별 `content IS NOT NULL` 10건 초과분의 오래된 본문 `NULL` 처리 (동일 정렬 기준)
5. 최근 실행 시각 기록
6. 일일 API 호출량 로그 집계

## 향후 확장 후보

현재 스키마에는 없지만, 고도화 시 검토 가능한 항목:
- `content_hash`
- `canonical_url`
- `updated_at`
- 이슈 클러스터 식별 컬럼
- 해외 종목용 별도 뉴스 소스

용도:
- 더 정교한 유사 기사 dedupe
- 재배포 기사 판별 보강
- 후속 ranking/clustering 최적화
- 미국 종목 지원

## 관측성과 로그

최소 구조화 로그:
- `symbol`
- API 조회 건수
- 신규 저장 건수
- 중복 스킵 건수
- 본문 크롤링 실패 건수
- 제목 유사도 그룹 수
- 그룹화로 절약된 본문 크롤링 건수
- 소요 시간(ms)

추가 운영 지표:
- 일일 네이버 API 호출량
- 도메인별 본문 추출 실패율
- cleanup 삭제 건수
- 그룹 평균 size

## 검증 결과 요약

반영 완료 항목:
- 네이버 뉴스 API 제약 반영
- URL 정규화 규칙 반영
- Redis lock 및 15분 쿨다운 정책 반영
- TTL 연장 함정 제거
- `force=True`와 lock 관계 명시
- watchlist commit 후 enqueue 원칙 반영
- 본문 추출 라이브러리 방향 확정
- 종목별 row 상한과 본문 상한 반영
- 본문 없는 기존 기사 재보강 정책 반영
- 본문 quota 보호용 제목 유사도 그룹화 반영
- 재배포 기사로 인한 본문 크롤링 낭비 방지 정책 반영
- `published_at` NULL 정렬 룰(`NULLS LAST`) 명시
- Phase 3 정기 갱신 호출 시그니처(`mode="refresh"`) 명시
- 그룹 대표 본문 실패 시 다음 후보 시도 룰 명시
- 이슈 다양성 제어가 후속 분석 단계임을 명시
- 종목코드 후처리 보조 활용 룰 명시

검증 결론:
- 구조적 공백 없이 구현 착수 가능한 수준
- 현재 계획은 첫 토론 UX를 고려한 `15/5/5` 기준으로 시작 가능
- 운영 중 비용 부담이 보이면 `10/3/3`으로 하향 가능

## 최종 결론

현재 계획은 실제 스키마와 네이버 뉴스 API 제약, 3차/4차 검증 코멘트를 기준으로 검증 완료된 상태다.

확정 내용:
- `symbol` 기반 적재
- `source_url` 기반 완전 중복 제거
- `originallink` 우선 저장, `link` fallback
- 관심 종목 추가 시 비동기 1회 수집
- 정기 갱신 1시간, 최소 재수집 간격 15분
- Redis lock 기반 중복 실행 방지
- TTL 30일, 기사 시각 기준 만료
- 기본 수집 정책은 `15/5/5`
- 종목당 전체 기사 상한 `100건`
- 종목당 본문 보유 기사 상한 `10건`
- 상한 초과 시 오래된 본문부터 `NULL` 처리
- 신규 row 우선, 기존 `content IS NULL` row는 남는 quota에서만 보강
- 제목 유사도 그룹화로 본문 크롤링 quota 보호
- 본문 실패 시 partial insert 허용
- 이슈 다양성은 저장 단계가 아니라 후속 분석 단계에서 제어
- 초기 범위는 한국 종목 우선

즉, 이제 남은 것은 이 계획에 따라 구현을 진행하는 것이다.
