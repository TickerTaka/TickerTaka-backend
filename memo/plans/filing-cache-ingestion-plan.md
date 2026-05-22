# Filing Cache 적재 계획

## 목표

사용자가 관심 종목을 추가하면 해당 종목 관련 **DART 공시**(`filing_cache`)를 가져와 캐싱한다.

핵심 원칙:
- 공시는 토론 evidence로 강한 권위를 갖는 데이터 — 본문 추출 품질이 중요하다.
- DART API는 일일 한도가 있고 본문(`document.xml`)은 별도 호출이라 News cache의 quota 보호 패턴(그룹화/quota 통제)을 재사용한다.
- 같은 인프라(Redis lock / cooldown / fail-closed / sweep last-run / 일일 API 호출량)를 News cache와 동일하게 사용한다.
- 초기 구현은 한국 상장 종목 한정 — DART 공시 대상 기업만.

## 검증 완료 내용

확인 완료 테이블:
- `filing_cache`
- `ticker_metadata`

`filing_cache` 실제 스키마:
- `id UUID PRIMARY KEY DEFAULT gen_random_uuid()`
- `symbol VARCHAR(30) NOT NULL`
- `filing_title TEXT NOT NULL`
- `filing_type VARCHAR(100) NULL`
- `content TEXT NULL`
- `summary TEXT NULL`
- `dart_receipt_no VARCHAR(20) NULL UNIQUE`
- `source_url VARCHAR(2048) NOT NULL`
- `disclosed_at TIMESTAMPTZ NULL`
- `retrieved_at TIMESTAMPTZ NOT NULL DEFAULT now()`
- `ttl_until TIMESTAMPTZ NULL`
- index `(symbol, disclosed_at DESC)`
- partial index `(symbol, ttl_until WHERE ttl_until IS NOT NULL)`
- FK `symbol -> ticker_metadata.symbol ON DELETE CASCADE`

결론:
- 현재 스키마만으로 구현 가능
- `dart_receipt_no` unique를 1차 dedupe 기준으로 사용
- `corp_code` 매핑은 FinancialCache plan과 동일 (in-memory dict 공유)

## 데이터 소스

**OpenDART API**
- 공시검색: `/api/list.json` (corp_code/기간/공시유형 기반)
- 공시 본문: `/api/document.xml` (rcept_no 기반, XML 형태)
- 일일 호출 한도: `20,000회` — FinancialCache와 합산 (같은 API 키)

공시 유형(`pblntf_ty`):
- `A` 정기공시 (사업보고서, 반기/분기 보고서) — FinancialCache가 다룸
- `B` 주요사항보고 (유상증자, 신주발행, 합병 등) — **본 plan 핵심**
- `C` 발행공시
- `D` 지분공시
- `E` 기타공시
- `F` 외부감사관련
- `G` 펀드공시
- `H` 자산유동화
- `I` 거래소공시 — 토론 evidence로 유의미
- `J` 공정위공시

초기 적재 대상:
- `B`, `I`를 우선
- 정기공시(`A`)는 FinancialCache가 처리 — 본 plan에서는 중복 적재하지 않음 (제외 필터)

본문 추출:
- `document.xml` 호출 → XML 파싱 → 본문 텍스트 추출
- DART의 XML 형식은 정형화되어 있으나 표/이미지가 포함된 경우가 많음
- 초기 구현은 텍스트 노드만 추출하고 표는 형식 보존을 시도 안 함
- 본문이 너무 길면 (예: 10,000자 초과) 잘라서 저장

## 환경 변수 / 외부 의존성

- `DART_API_KEY` (이미 `.env`/`.env.example`에 있음)
- requirements.txt 추가: 별도 추가 없음 — `requests` + 표준 `xml.etree.ElementTree`
- corp_code 매핑은 FinancialCache plan과 공유 (`app/external/dart/corp_code.py`)

## 확정 정책

### 1. 적재 건수와 백필 범위 (옵션 B 해석 B 일관 적용)

기본값:
- 초기 백필: 최근 `6개월` 또는 최근 `30건` 중 적은 쪽
- 정기 갱신 시 조회 건수: 최근 `10건`
- 1회 본문 추출 상한: `3건` (초기값 — DART API 2단 호출 비용 고려한 보수적 시작, 운영 후 종목당 시간당 신규 공시 수 관찰 후 조정)
- 종목당 본문 row 적재 상한: `10건` (= 본문 추출된 row만 적재)
- 본문 추출 실패 시 **partial insert 없음** — 메타만 적재하지 않음 (옵션 B 일관)

`news-cache-policy-revision-plan.md`의 옵션 B 결정을 일관 적용 (해석 B):
- **PostgreSQL `filing_cache.content`는 항상 NULL** — 본문은 로컬 ChromaDB document에 별도 reindex로 적재
- 본문 추출 성공한 row만 PG INSERT (PG는 본문 추출 시도 후 성공 row만)
- ChromaDB upsert는 `scripts/reindex_local_chroma.py`로 분리 (졸프 단계 정책 — [[infra-stage-policy]])
- partial insert 정책 없음 (timeout/parsing 실패 row는 적재 자체를 안 함)
- UI는 `filing_title` + DART viewer URL로 redirection

운영 원칙:
- 토론 evidence로 사용되므로 본문 확보 우선순위가 높음
- 본문 추출이 News보다 더 신뢰성 있음 (DART 직접 — 403 차단 없음)
- 정기공시(A)는 제외 필터 — FinancialCache가 처리
- `pblntf_ty` 분포를 sync 로그에 남겨 누락 유형(E 등) 감지

### 2. 중복 제거

저장 단계:
- `dart_receipt_no` unique를 1차 dedupe 기준
- 동일 receipt 재수집 시 새 row 만들지 않음
- 본문 추출 실패한 receipt는 PG에도 ChromaDB에도 없음 → 다음 sync에서 동일 receipt 재시도 가능

본문 보강 정책:
- 옵션 B에서 partial insert가 없으므로 "본문 없는 기존 row 보강" 케이스 없음
- 새 sync에서 본문 추출 성공해야 비로소 적재됨
- DART 차단/timeout 발생 시 같은 receipt는 다음 sync에서 재시도

### 3. 갱신 주기

기본값:
- 관심 종목 추가 시: 비동기 수집 `1회 즉시 실행`
- 정기 갱신: `1시간` (News cache와 동일 주기)
- 강제 재수집 최소 간격: `15분`

운영 방향:
- 영업일 장중에는 새 공시가 자주 발표됨 (예: 14:00 ~ 18:00)
- 야간/주말은 신규 거의 없음
- `force=True`는 15분 최소 간격만 무시하며 Redis lock은 우회하지 않음
- watchlist에서 종목 삭제 시 해당 종목 정기 갱신 중단

### 4. 삭제 정책 / TTL

기본값:
- TTL: `180일` (6개월) — News cache(30일)보다 길게. 공시는 분기 토론에 활용 가능.
- `ttl_until = disclosed_at + 180 days`
- `disclosed_at`이 없으면 `retrieved_at + 180 days` fallback

정리 작업:
- 매일 1회 cleanup
- `ttl_until < now()`이면 삭제
- `symbol`별 row 수 `10`건 초과 시 오래된 공시부터 삭제 (옵션 B: row = 본문 적재이므로 본문 상한과 동일)
- 본문 NULL trim 로직은 옵션 B에서 의미 없음 (애초에 NULL 적재) — 제거
- "오래된" 기준은 `disclosed_at ASC NULLS LAST`, NULL끼리는 `retrieved_at ASC`
- **PG cleanup은 PG row만 처리** — 로컬 ChromaDB는 reindex 스크립트로 재생성 (졸프 단계 정책)
- ChromaDB drift가 보이면 `reindex_local_chroma.py --reset --source filing --symbol X` 수동 재실행

## 저장 전략

PG `filing_cache` 최소 저장 필드 (옵션 B):
- `symbol`
- `filing_title` — DART `report_nm`
- `filing_type` — DART `pblntf_detail_ty` 또는 `pblntf_ty`
- `dart_receipt_no` — DART `rcept_no` (14자리 숫자, dedupe 핵심)
- `source_url` — DART 공시 viewer URL (`https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}`)
- `disclosed_at` — DART `rcept_dt` (YYYYMMDD) + 시각 매핑
- `content` — **항상 NULL** (본문은 ChromaDB에 저장)
- `summary` — 초기 구현은 NULL. 후속 phase에서 LLM 요약 추가 가능
- `retrieved_at`
- `ttl_until`

ChromaDB collection `filing`:
- `id = filing_cache.id` (UUID) — 1:1 매핑
- `document = 추출된 본문 텍스트`
- `embedding = 1536차원 벡터`
- `metadata = {symbol, source_id, disclosed_at, source_url, filing_type}`

본문 적재 상한 정책:
- 종목당 PG row 수 = ChromaDB document 수 = 최대 `10건`
- 상한 초과 시 PG row + ChromaDB document 동시 삭제 (오래된 것부터)

## Redis 키 컨벤션

- `filing-sync:lock:{symbol}` — 동시 실행 방지 (`SET NX EX=600`)
- `filing-sync:last-sync:{symbol}` — 최근 실행 시각 (15분 cooldown 판정)
- `filing-sync:sweep:last-run:{mode}` — 전체 sweep 최근 실행 시각
- `dart-api-count:{date}` — 일일 DART API 호출량 (FinancialCache와 공유)

**졸프 단계 정책 ([[infra-stage-policy]])**: Redis는 개발자별 로컬 Docker. 팀 단위 lock 약함 — 같은 종목 공시에 대해 동시 sync 가능하나 `dart_receipt_no` unique로 PG에서 최종 중복 방어. 운영 진입 시 별도 plan으로 (현재 `debate-runtime-infrastructure-plan.md`의 "운영 환경 배치" 섹션은 *후속 참고용*).

## 토론 코드 연계 (a543ff1 커밋 기준)

토론 에이전트의 `data_agent` 노드가 본 cache 데이터를 `debate_repo.fetch_filing_context(symbol)`로 SELECT:

```sql
SELECT id, filing_title, filing_type, summary, source_url, disclosed_at
FROM filing_cache WHERE symbol=$1 AND ttl_until > now()
ORDER BY disclosed_at DESC LIMIT 5
```

영향:
- 본 plan 적재 컬럼이 위 SELECT와 일치 (`filing_title`/`filing_type`/`summary`/`source_url`/`disclosed_at`)
- `content`는 SELECT 안 됨 — 옵션 B 정책(PG content NULL)과 정합
- `summary`는 본 plan 초기 NULL → 토론에서 `filing_title + ""`로 표시되니, LLM 요약(Phase 4)이 토론 품질에 큰 영향
- `ttl_until > now()` 필터 — 본 plan의 TTL 180일과 정합
- evidence 영구화 시 `evidence.filing_cache_id` 외래 키로 cache row 참조 + ChromaDB metadata `source_id`로 본문 retrieval

## 수집 함수 시그니처

```python
def sync_filings_for_ticker(
    symbol: str,
    mode: str = "initial",   # "initial" | "refresh"
    force: bool = False,
    limit: int | None = None,
) -> SyncFilingResult: ...
```

역할:
- `sync_filings_for_ticker`:
  1. `ticker_metadata`에서 종목 확인 + corp_code 매핑
  2. Redis lock 획득 + cooldown 확인
  3. DART 공시검색 API 호출 (`list.json`, corp_code + 기간 + pblntf_ty 필터)
  4. `rcept_no` 기준 dedupe (DB 기존 row 조회)
  5. 신규 후보 우선순위 정렬 (옵션 B: 본문 없는 기존 row 보강 케이스 없음)
  6. 본문 추출 상한 `3건`까지 `document.xml` 호출
  7. **XML 파서 모듈(`app/external/dart/document_parser.py`)을 ingestion 로직과 분리** — 단위 검증 가능 형태 (표/주석/첨부서류 처리 품질 차이가 크므로 분리 필수)
  8. XML 파싱 → text 추출 → 길이 제한 적용 (10,000자 cap)
  9. 본문 추출 성공한 row만 `filing_cache` INSERT (content=NULL) — ChromaDB upsert는 안 함
  10. 본문 추출 실패 row는 적재 자체를 skip (다음 sync에서 재시도 가능)
  11. `ttl_until` 계산
  12. row 상한 초과분 정리 (PG row만)

ChromaDB 적재는 별도 `scripts/reindex_local_chroma.py`(vector-db plan Phase 3)가 담당. PG row 기준으로 `source_url` 또는 `dart_receipt_no` + `document.xml` 재호출하여 본문 재추출 후 로컬 ChromaDB upsert.

졸프 단계 정책 ([[infra-stage-policy]]): 수집 단계는 PG metadata + 본문 추출 성공 판정만, RAG 인덱싱은 분리. 운영 진입 시 sync 직후 동기 upsert로 전환 검토.

반환 예시:
- 조회 건수 (DART API list 응답)
- 신규 저장 건수
- 중복 스킵 건수
- 본문 추출 시도 건수
- 본문 추출 실패 건수
- 소요 시간(ms)

## 트리거 및 실행 구조

NewsCache와 동일 구조:
1. 사용자가 관심 종목 추가
2. `watchlist` 저장 + DB commit
3. `BackgroundTasks.add_task(sync_watchlist_filings, symbol)` enqueue
4. `sync_watchlist_filings(symbol)`:
   1. `sync_filings_for_ticker(symbol, mode="initial", force=True)`
5. 정기 갱신은 별도 scheduler (`FilingCacheSchedulerService`)

스케줄러 실행:
- 외부 cron으로 매시 정각 직후 (예: HH:05)에 `scripts/run_filing_cache_scheduler.py --mode refresh`
- 매일 새벽 1회 cleanup

## 본문 추출 정책

초기 구현 기본값:
- HTTP timeout: connect `3s`, read `10s` (DART document.xml은 큰 파일 가능)
- 동시 본문 추출: symbol당 `1건` 직렬
- 추출 간 짧은 간격 유지

실패 처리:
- timeout, parsing 실패 시 partial insert (메타데이터만 저장)
- 동일 receipt 재시도는 다음 refresh 주기에 본문 없는 기존 row 보강으로
- DART API 자체 에러(429, 5xx)는 재시도 없이 다음 sweep으로

XML 파싱:
- `xml.etree.ElementTree`로 처리
- 본문 텍스트 노드만 추출
- 표(`<TABLE>`)는 초기 구현에서 무시 또는 단순 텍스트로 변환
- 본문 길이 `10,000자` 초과 시 잘라서 저장 (앞부분 우선)

본문 추출 우선순위:
- 신규 row > `disclosed_at` DESC > 본문 길이 추정값

## 시간대 정책

NewsCache와 동일:
- DART `rcept_dt` (YYYYMMDD)는 KST 가정으로 파싱
- 시각이 없는 경우 `00:00 KST`로 처리
- 내부 코드에서는 UTC aware datetime으로 통일
- DB 저장 시 TIMESTAMPTZ로 보존

## 구현 단계

### Phase 0. corp_code 매핑 (FinancialCache plan과 공유)

이미 FinancialCache plan에서 정의됨 — `app/external/dart/corp_code.py`. 같은 in-memory dict를 공유.

### Phase 1. DART 클라이언트 + filing 적재

목표: `sync_filings_for_ticker(symbol)` 구현

처리 순서:
1. `app/external/dart/client.py` 확장 — 공시검색(`list.json`) + 본문(`document.xml`) wrapper
2. `app/external/dart/document_parser.py` — XML 본문 텍스트 추출
3. `app/repositories/filing_cache_repository.py` — bulk insert/upsert, 본문 보강, TTL/상한 정리
4. `app/domain/filing_ingestion.py` — `sync_filings_for_ticker` + Redis lock + 본문 quota 통제
5. `scripts/validate_filing_ingestion.py` — 적재/upsert/lock/cooldown/본문 quota/TTL 시나리오

### Phase 2. watchlist 트리거 연결

목표: watchlist 등록 시 filing sync

처리 순서:
1. `app/domain/watchlist_service.py`에 `sync_watchlist_filings(symbol)` 추가
2. `app/api/watchlist.py`의 `create_watchlist`에서 background task 추가

### Phase 3. 정기 갱신 / cleanup

## 검증/보완 메모 (2026-05-22)

1. 본문 정책이 `news-cache-policy-revision-plan.md`의 옵션 B와 완전히 일치하도록 갱신이 필요하다. 현재 문서에는 아직 `content` 저장과 `content IS NOT NULL` 상한 서술이 남아 있어, 구현 직전엔 `content 항상 NULL + ChromaDB SOT`로 통일해야 한다.
2. `timeout, parsing 실패 시 partial insert`는 옵션 B 결정과 충돌한다. 본문 없는 메타만 적재하지 않기로 했다면, filing도 동일하게 "본문 추출 성공한 row만 적재"로 바뀌어야 한다.
3. 공시는 News보다 희소하고 권위가 높기 때문에 `본문 추출 상한 3건`은 다소 보수적일 수 있다. 다만 DART API 2단 호출 비용을 고려하면 초기값으론 타당하고, 실제 종목당 시간당 신규 공시 수를 관찰한 뒤 조정하는 편이 맞다.
4. `B`, `I` 유형 우선 결정은 합리적이지만, 실제 토론 품질에 영향을 주는 `E(기타공시)`가 일부 빠질 수 있다. 초기 구현 후 타입별 누락 체감을 보고 whitelist를 넓힐 수 있게 로그에 `pblntf_ty` 분포를 남기는 편이 좋다.
5. `document.xml` 파싱은 HTML 기사보다 구조가 안정적이지만, 표/주석/첨부서류가 많은 문서에서 텍스트 정제가 생각보다 품질 차이를 크게 낸다. 구현 시 파서 모듈을 ingestion 로직과 분리해 단위 검증 가능한 형태로 두는 것이 중요하다.
6. cleanup 시 `dart_receipt_no` unique row 삭제와 Chroma delete를 함께 해야 하므로, 옵션 B 적용 후에는 `delete_expired_rows_returning_ids`류 인터페이스가 사실상 필수다.

목표: 매시 filing 갱신, TTL/상한 정리

처리 순서:
1. `app/domain/filing_cache_scheduler.py` — `FilingCacheSchedulerService`
2. `run_watchlist_refresh()` — watchlist 전체 sweep
3. `run_filing_cleanup()` — TTL/상한 정리
4. `scripts/run_filing_cache_scheduler.py` — cron 진입점

### Phase 4. 요약 추가 (선택)

목표: LLM 기반 본문 요약 → `summary` 컬럼 채움

별도 plan으로 분리 가능. 본 plan 범위 밖.

## 관측성과 로그

최소 구조화 로그:
- `symbol`
- API list 조회 건수
- 신규 저장 건수
- 중복 스킵 건수
- 본문 추출 시도 / 실패 건수
- 본문 길이 평균 / 잘린 건수
- 소요 시간(ms)

추가 운영 지표:
- 일일 DART API 호출량 (FinancialCache 합산, KST 기준 날짜)
- 공시 유형별 분포 (`pblntf_ty` 카운트 — `B`/`I` 외 누락 유형 감지용)
- 종목별 마지막 `disclosed_at`
- 본문 추출 실패율
- ChromaDB upsert 성공/실패 (fail-soft 후 reconcile 대상)

## 향후 확장 후보

- LLM 기반 요약(`summary`) 도입 — 토론 evidence 길이 통제
- 표(table) 파싱 추가 — 재무 수치 추출
- 첨부 PDF 처리 — 일부 공시는 PDF만 제공
- 정정 공시(`rcept_no` 패턴) 식별 — 원본/정정 매핑
- 공시 본문에서 EventTimeline 자동 추출 (M&A, 유증, 신주 발행 등)

## 결론

확정 내용 (옵션 B 해석 B 일관 적용 + 졸프 단계 로컬 RAG):
- 데이터 소스 OpenDART `list.json` + `document.xml`
- 본문 추출은 XML 텍스트 노드 기준, 표/이미지는 후속 확장
- 본문 SOT는 외부 원본(DART), 로컬 ChromaDB collection `filing`은 재생성 가능한 RAG 인덱스
- **PG `filing_cache.content`는 항상 NULL**
- partial insert 없음 — 본문 추출 성공한 row만 PG 적재
- 초기 백필 6개월 또는 30건, row 상한 = 본문 적재 상한 = 10건
- 정기 갱신 1시간, 강제 재수집 최소 15분
- TTL 180일, 공시 시각 기준 만료
- 본문 추출 상한 3건 (초기값, 운영 신호 보고 조정)
- 정기공시(`A`)는 FinancialCache가 처리 — 본 plan에서 제외 필터
- corp_code 매핑은 FinancialCache와 공유 (부팅 시 로컬 캐시 우선, 없으면 다운로드)
- XML 파서 모듈은 ingestion과 분리해 단위 검증 가능
- ChromaDB upsert/delete는 `scripts/reindex_local_chroma.py`로 분리 (졸프 단계)
- cleanup은 PG row만 처리, ChromaDB drift는 reindex 수동 재실행으로 해결
- watchlist 트리거 NewsCache 동일 패턴
- Phase 0~4 순차 구현 (Phase 0/4는 다른 plan과 공유 또는 선택)
- 운영 진입 시 별도: ChromaDB 동기화/백업 정책, 공용 collection 운영 — `production-deployment-plan.md` (배포 결정 시점에 작성)
