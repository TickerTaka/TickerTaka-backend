# News Cache 고도화 계획 (filing 고도화 흐름 참고)

> 작성일: 2026-05-28
> 참고 문서:
> - `docs/filing-ingestion-enhancement.md`
> - `docs/filing-chroma-validation-runbook.md`
> - `memo/plans/news-cache-policy-revision-plan.md`

## 배경

`filing_cache` 쪽은 본문 파싱 v2(표 grid 보존) + 섹션별 청킹 + `source_id` metadata + reset/reindex 스크립트 + e2e 검증 스크립트까지 일괄 적용되어 토론 RAG의 공시 근거 품질이 크게 개선됐다 (`docs/filing-chroma-validation-runbook.md` 26~30장).

본 plan은 그 흐름을 `news_cache`에 1:1로 검토하되, **뉴스의 도메인 특성에 맞춰 일부 항목만 채택**하고 나머지는 의도적으로 보류한다. 본문 추출/요약/청킹은 filing과 달리 ROI가 낮거나 현재 정책 결정과 충돌하므로 별도 트리거 발생 시에만 진행한다.

`news-cache-policy-revision-plan.md`의 옵션 B(`news_cache.content`는 항상 NULL, 본문 SOT는 외부 원본) 결정은 그대로 유지한다.

## 결정 요약

| 영역 | 결정 | 비고 |
|---|---|---|
| **F. 컬렉션 리셋 + reindex 스크립트 정비** | **진행** | filing 패턴 그대로 복제 |
| **G. e2e 검증 스크립트 신설** | **진행** | filing 검증 런북 15장 패턴 |
| E. summary 품질 개선 (헤더 제거/LLM 요약) | **보류** | 현재 summary 활용 범위가 작아 ROI 낮음 |
| B/C/D. 섹션별 청킹 + chunk_id + retrieval source_id join | **보류** | 뉴스 평균 본문 길이상 청킹 효용 작음, 현 1:1 매핑이 단순 |
| H. 임베딩 기반 클러스터링 고도화 | **조건부 보류** | 자카드 0.7로 충분치 않다고 판단되는 시점에 재개 |
| I. `news_cache.content` PG 저장 전환 | **보류 (현 정책 유지)** | 중복 저장으로 인한 용량 부담이 크고, 본문 PG 저장이 *필수*가 되는 요구사항이 생기면 재개 |
| J. 종목명 결합 신호 강화 (오매칭 보완) | **조건부 보류** | 오매칭/누락 사례가 관찰되면 재개 |
| K. 추출기 폴백 체인 (trafilatura → readability → site-specific) | **조건부 보류** | `body_failed_count` 비율이 임계 초과하면 재개 |

> 보류 항목은 *지금 안 한다*는 뜻이지 *영원히 안 한다*는 뜻이 아니다. 각 항목은 아래 **재개 트리거** 절에 조건을 명시한다.

## 변경 결정 (진행 항목)

### F. 컬렉션 리셋 + reindex 스크립트 정비

**현 상태**

- `scripts/reindex_local_chroma.py` — `--reset` 플래그가 있지만 컬렉션 통째 delete 후 재인덱싱하는 개발용 한 줄 스크립트
- `scripts/reset_filing_collection.py`처럼 "현재 카운트 출력 → 삭제 → 안내 메시지"를 갖춘 운영용 정돈 스크립트는 **없음**
- `scripts/reindex_all_filings.py`에 대응하는 `reindex_all_news.py`도 **없음**
- 임베딩 기본값은 filing과 동일(`huggingface` / `jhgan/ko-sroberta-multitask`, 768차원) — 차원은 정합하나, 운영 Chroma의 `news` 컬렉션이 과거 64차원 deterministic 잔재를 안고 있을 가능성은 점검 필요

**변경**

1. `scripts/reset_news_collection.py` 신설
   - `scripts/reset_filing_collection.py`를 베이스로 컬렉션명을 `NEWS_COLLECTION_NAME`으로 교체
   - 현재 카운트 출력 → `delete_collection` → 안내 메시지 (다음 reindex 실행 시 새 embedding 차원으로 재생성) 포맷 유지
2. `scripts/reindex_all_news.py` 신설
   - `WatchlistRepository.list_distinct_symbols()`로 종목 순회
   - 각 종목에 `EvidenceIndexingService.reindex_news_for_symbol(symbol, reset=False)` 호출
   - filing의 `reindex_all_filings.py`와 출력 포맷(`{count, results: [{symbol, scanned_rows, indexed_rows, skipped_rows, failed_rows}, ...]}`) 일치
3. 운영 Chroma의 `news` 컬렉션 차원/카운트 1회 점검
   - 64차원 잔재가 확인되면 reset 1회 수행 후 reindex
   - 차원 정합이면 reset 불필요, 스크립트는 향후 운영 절차용으로 보존

**범위 밖**

- `EvidenceIndexingService.reindex_news_for_symbol` 자체의 로직 변경은 없음 (현재 1 row = 1 ChromaDocument 매핑 유지)

### G. e2e 검증 스크립트 신설

**현 상태**

- `scripts/validate_evidence_indexing_news.py` — 인덱싱 경로만 검증
- `scripts/validate_news_ingestion.py` — ingestion 경로만 검증
- `scripts/validate_evidence_retrieval.py` — retrieval 경로만 검증
- `scripts/validate_filing_evidence_retrieval.py`처럼 **Fake 데이터 → 추출 → upsert → `source_id` 조회 → 청소**까지 한 번에 도는 e2e 스크립트는 **없음**

**변경**

1. `scripts/validate_news_evidence_retrieval.py` 신설
   - filing 검증 런북 15장 흐름 차용:
     ```text
     1. news_cache에 검증용 row 생성
     2. FakeArticleScraper가 가짜 본문 반환 (실제 외부 호출 없음)
     3. EvidenceIndexingService가 news 컬렉션에 upsert
     4. `news_validate` 컬렉션에 where={"source_id": <row_id>}로 조회
     5. 검증용 news_cache row 삭제
     6. 검증용 Chroma collection 삭제
     ```
   - 출력 포맷:
     ```json
     {
       "symbol": "...",
       "scanned_rows": N,
       "indexed_rows": N,
       "skipped_rows": N,
       "failed_rows": N,
       "collection_count": N,
       "fetched_id": "..."
     }
     ```
   - filing 검증 스크립트와 동일하게 마지막에 collection을 cleanup

**범위 밖**

- 운영 Chroma 데이터에 영향 없음 (검증 전용 collection 사용)

## 보류 항목 — 재개 트리거

각 항목은 아래 조건이 관찰되는 시점에 별도 plan으로 분리해 재개한다.

### E. summary 품질 개선

- **재개 트리거**: 토론 evidence_context의 excerpt가 매체 보일러플레이트(`〇〇기자 (서울=연합뉴스)` 등)로 시작해 발언 품질을 떨어뜨리는 사례가 관찰될 때
- **착수 방안**: 1) 매체 헤더 제거 규칙 추가(저비용) → 2) Claude Haiku 4.5 / GPT-4o-mini 요약 검토 (비용 측정 선행)

### B/C/D. 청킹 + chunk_id + retrieval source_id join

- **재개 트리거**: longform 분석/기획 기사(>3,000자) 비중이 늘어 단일 벡터로 검색 점수가 흐려지는 사례가 관찰될 때
- **착수 방안**: filing의 `build_filing_chunks` / `_search_filings` / `_source_id_for_hit` 패턴을 그대로 차용. retrieval 쪽 `_source_id_for_hit`(`app/domain/evidence_retrieval.py:209-214`)에 이미 `":s"` 패턴 파싱이 들어가 있으므로 헬퍼를 `":c"`까지 일반화하면 부담 작음

### H. 임베딩 기반 클러스터링

- **재개 트리거**: 자카드 0.7 기반 그룹핑이 패러프레이즈된 미러 기사를 못 잡아 중복 본문 크롤링/벡터화가 과다하다고 판단될 때
- **착수 방안**: 후보 30개 임베딩 → cosine ≥ 0.85 그리디 매칭으로 클러스터링. 자카드 1차 → 임베딩 2차 계층화

### I. `news_cache.content` PG 저장 전환

- **현 정책 유지 이유**: 옵션 B(`news-cache-policy-revision-plan.md`)에서 결정된 대로 content는 항상 NULL. 본문을 PG에 저장하면 종목당 10건 × 사용자 다수 × 평균 본문 길이만큼 **중복 저장으로 용량을 과도하게 점유**할 수 있음
- **재개 트리거**: 본문을 PG에 저장하는 것이 *필수* 가 되는 요구사항이 발생할 때
  - 예: 원문 사이트 삭제로 인한 영구 손실이 토론 품질에 실측 가능한 영향을 줄 때
  - 예: 토론 외 다른 기능(아카이브/검색/내보내기)이 본문 직접 접근을 요구할 때
- **착수 방안**: 본문 별도 archive 테이블 / 오브젝트 스토리지 등 *PG 본문 저장 외 대안*도 같이 비교

### J. 종목명 결합 신호 강화

- **재개 트리거**: 다른 종목 위주 기사가 특정 종목 리스트에 잘못 매칭되는 사례(False positive) 또는 영문명/약어만 쓴 기사 누락(False negative) 사례가 관찰될 때
- **착수 방안**: 본문 내 종목명 등장 비율 기반 우선순위 조정, 또는 NER로 주요 주체 판정

### K. 추출기 폴백 체인

- **재개 트리거**: `body_failed_count` 비율이 임계(예: 전체 후보의 30% 이상)를 초과하거나, 특정 매체에서 trafilatura가 일관되게 실패할 때
- **착수 방안**: 1) trafilatura 실패 시 `readability-lxml` 폴백 → 2) 자주 등장하는 매체 5~10개에 대해 CSS 셀렉터 직접 박기 → 3) JS 렌더링 사이트는 `playwright` 백업 (별도 워커)

## 변하지 않는 부분

- `news-cache-policy-revision-plan.md`에서 결정된 옵션 B 정책 (`news_cache.content` NULL, 본문 SOT는 외부 원본)
- `NewsIngestionService`의 ingestion 정책 (자카드 클러스터링, prefilter/storage filter 2단, Redis lock/cooldown/일일 API 카운터)
- `EvidenceIndexingService.build_news_document`의 1 row = 1 ChromaDocument 매핑
- `EvidenceRetrievalService._search_news`의 Chroma hit id == `news_cache.id` 가정 (청킹을 도입하지 않으므로 변경 없음)
- 임베딩 모델 / 차원 (`huggingface` / `jhgan/ko-sroberta-multitask` / 768차원)

## 작업 순서

1. 운영 Chroma의 `news` 컬렉션 차원/카운트 점검 (1회성)
2. `scripts/reset_news_collection.py` 신설
3. `scripts/reindex_all_news.py` 신설
4. `scripts/validate_news_evidence_retrieval.py` 신설
5. (필요 시) `news` 컬렉션 reset 1회 + reindex 1회 실행
6. e2e 검증 스크립트 실행해 토론 RAG가 뉴스 컬렉션에 정상 조회되는지 확인

## 검증 기준

- `scripts/reset_news_collection.py` 실행 시 현재 카운트 출력 + 삭제 메시지 + 다음 단계 안내 출력
- `scripts/reindex_all_news.py` 실행 시 워치리스트 종목별로 `{scanned_rows, indexed_rows, skipped_rows, failed_rows}` 카운트 출력, `failed_rows`는 0
- `scripts/validate_news_evidence_retrieval.py` 실행 시 `indexed_rows=1`, `collection_count=1`, `fetched_id`가 생성한 row id와 일치, 마지막에 검증용 collection이 정상 삭제됨
- 운영 `news` 컬렉션 카운트가 reindex 전후로 기대치(워치리스트 종목 수 × 종목당 최대 10건) 범위 내

## 주의사항

- 운영 Chroma 컬렉션 reset은 **돌이킬 수 없는 작업**이다. 실행 전 카운트/차원 확인을 반드시 선행한다.
- reindex는 trafilatura 재호출이 발생한다 (`EvidenceIndexingService.reindex_news_for_symbol`이 `row.source_url`로 다시 스크래핑). 외부 사이트가 기사를 내렸다면 해당 row는 `skipped_rows`로 빠지며, 이는 옵션 B 정책의 기지의 위험이다.
- 본 plan은 ingestion 정책을 건드리지 않으므로 Naver API 일일 한도 추적(`_record_daily_api_call`)에 영향 없음.
- e2e 검증 스크립트는 **운영 컬렉션을 건드리지 않도록** 별도 검증 컬렉션(`news_validate` 등)을 사용한다. filing 검증 런북 14~15장과 동일한 격리 원칙.
