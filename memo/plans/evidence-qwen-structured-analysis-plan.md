# Qwen 구조화 증거 분석 및 비동기 보강 계획

## 1. 목표와 역할

현재 표 중심 DART 공시는 `Qwen 요약 -> FinBERT 분류` 경로를 선택적으로 사용한다. 이 구조는 요약 과정에서 투자 판단에 필요한 정보가 손실될 수 있고, Qwen 결과가 수치 grounding에 실패해도 FinBERT 입력으로 사용되는 문제가 있다.

목표 구조는 다음과 같다.

- Qwen: 표 공시 구조화 해석, 요약, 사건 유형, 근거와 리스크 생성
- FinBERT: 뉴스와 자연어형 입력의 감성 기준점
- `InvestmentImpactRuleEngine`: 명확한 사건 및 정책적 영향도 보정
- 하네스: JSON 형식, 원문 근거, 수치 grounding, 감성-영향도 일관성 검증

Qwen 호출 범위는 다음처럼 정의한다.

- 표 중심 공시 enrichment: Qwen 필수
- 뉴스 enrichment: FinBERT 결과가 non-neutral이거나 영향도 임계값을 넘을 때 조건부 호출
- 명확한 제목 하드룰 공시: Qwen 판정은 생략 가능하며, 요약이 필요할 때만 enrichment

따라서 "Qwen을 반드시 사용한다"는 시스템의 표 공시 해석기로 반드시 사용한다는 의미이며, 모든 뉴스 건을 무조건 호출한다는 의미는 아니다.

## 2. 목표 처리 흐름

### 동기 baseline

```text
뉴스/공시 수집 및 인덱싱
-> 정제 텍스트 생성
-> FinBERT 가능한 입력 분류
-> 룰엔진 보정
-> baseline evidence_analysis 즉시 저장
-> 사전 게이트 통과 시 analysis_job enqueue
```

baseline은 Qwen 장애나 워커 중단과 무관하게 항상 제공한다.

### 비동기 Qwen enrichment

```text
analysis_job claim
-> 원문 다시 조회
-> Qwen 구조화 JSON 생성
-> 스키마/근거/수치/일관성 검증
-> 룰엔진 정책 보정
-> 검증 성공 필드만 기존 baseline에 병합
-> evidence_analysis 갱신
-> job 완료
```

Qwen 실패 시 기존 baseline을 덮어쓰지 않는다.

## 3. 주요 설계 결정

### 3.1 별도 `analysis_job` DB 큐

저장소에는 Redis와 Celery 의존성이 이미 있지만 Celery 워커 구성은 없다. 또한 기존 `data_refresh_job`은 종목 단위 데이터 갱신용이며, 분석 대상 `source_id`, `prompt_version`, enrichment 단계 상태를 표현하지 못한다.

따라서 첫 구현은 별도 프로세스가 Postgres의 `analysis_job`을 폴링하는 방식으로 한다.

- API 프로세스와 Qwen 모델 메모리를 분리한다.
- `SELECT ... FOR UPDATE SKIP LOCKED`로 중복 처리를 방지한다.
- 잡 상태와 실패 원인을 DB에서 감사할 수 있다.
- 처리량 증가 시 repository 인터페이스를 유지한 채 Celery로 교체할 수 있다.

테이블명은 기존 단수형 규칙(`data_refresh_job`)에 맞춰 `analysis_job`으로 한다.

### 3.2 `event_type`과 `evidence` 정식 컬럼

- `event_type`: 게이팅, 필터링, 룰 정책에 쓰이는 1급 분류값
- `evidence`: Qwen 판정 근거가 되는 원문 문장 또는 직렬화된 표 행

두 필드는 `raw_response`에만 묻지 않고 `evidence_analysis` 정식 컬럼으로 저장한다. 원본 Qwen 출력과 검증 상세는 계속 `raw_response`에 보존한다.

### 3.3 사전 게이트와 Qwen 사건 유형 분리

Qwen 실행 여부를 Qwen이 생성한 `event_type`으로 판단할 수는 없다. enqueue 전에는 저비용 결정 함수가 필요하다.

```python
detect_event_type_hint(title, cleaned_text) -> str
```

- 제목과 알려진 필드명으로 사전 `event_type_hint`를 생성한다.
- 이 힌트와 `_is_table_heavy` 결과로 enqueue 여부를 정한다.
- Qwen의 최종 `event_type`은 enrichment 단계에서 검증 후 저장한다.

## 4. 데이터 모델과 마이그레이션

### `evidence_analysis` 추가 컬럼

- `event_type VARCHAR(40) NULL`
- `evidence JSONB NOT NULL DEFAULT '[]'::jsonb`
- `idx_evidence_analysis_event_type`

`EvidenceAnalysisResult`, SQLAlchemy 모델, repository upsert, 수동 생성 SQL을 함께 갱신한다.

### 신규 `analysis_job`

- `id UUID PK`
- `source_type VARCHAR(30)`
- `source_id UUID`
- `symbol VARCHAR(30)`
- `event_type_hint VARCHAR(40) NULL`
- `prompt_version VARCHAR(50)`
- `status VARCHAR(20)`: `pending|running|done|failed`
- `attempts INTEGER`
- `max_attempts INTEGER`
- `last_error TEXT NULL`
- `available_at`, `locked_at`, `created_at`, `updated_at`
- unique: `(source_type, source_id, prompt_version)`
- index: `(status, available_at, created_at)`

현재 `evidence_analysis`는 Alembic 초기 마이그레이션에 누락되어 있고 수동 SQL로 존재할 수 있다. 신규 마이그레이션은 기존 환경을 보존하도록 `CREATE TABLE IF NOT EXISTS` 및 `ADD COLUMN IF NOT EXISTS` 전략을 사용한다. `alembic/env.py`에는 두 모델을 명시적으로 import한다.

## 5. Qwen 구조화 분석기

기존 `LocalQwenSummarizer`를 모델 실행 공통부와 구조화 분석부로 나눈다.

```text
LocalQwenRuntime
  - tokenizer/model lazy load
  - MPS/CPU 선택
  - deterministic generation

LocalQwenEvidenceAnalyzer
  - filing prompt
  - news enrichment prompt
  - JSON extraction/coercion
```

공시 출력 스키마:

```json
{
  "summary": "string",
  "event_type": "earnings_down",
  "sentiment": "negative",
  "impact_score": -1,
  "confidence": 0.82,
  "key_points": ["string"],
  "risks": ["string"],
  "evidence": ["원문에 존재하는 문장 또는 표 행"]
}
```

뉴스 출력은 `summary`, `key_points`, `risks`, `evidence`만 권위 있게 사용한다. 뉴스의 최종 sentiment와 impact는 FinBERT와 룰엔진 결과를 유지한다.

## 6. 검증 및 병합 정책

검증 순서는 다음과 같다.

1. JSON 파싱 및 필수 필드 타입 검증
2. sentiment, impact score, confidence 범위 검증
3. `event_type` 허용 목록 검증
4. evidence가 정규화된 원문에 실제로 존재하는지 검증
5. summary/key points/risks/evidence의 수치 grounding 검증
6. sentiment와 impact 부호 일관성 검증
7. 룰엔진 정책 보정

필드별 실패 정책:

- JSON 전체 파싱 실패: enrichment 폐기, baseline 유지
- evidence 원문 불일치: evidence 폐기, 나머지 필드는 별도 검증 후 사용
- 수치 grounding 실패: 해당 생성 필드만 폐기
- sentiment/impact 불일치: Qwen 분류 폐기, baseline 분류 유지
- hard rule 충돌: hard rule 결과 우선, 충돌 내용은 `raw_response`에 기록

현재 Qwen 요약이 grounding에 실패해도 FinBERT 입력으로 사용되는 경로는 제거한다.

## 7. 서비스 분리

`EvidenceAnalysisService`에 다음 경계를 둔다.

- `analyze_baseline(...)`: 빠른 동기 분석 및 저장
- `should_enqueue_enrichment(...)`: 사전 게이트
- `enrich_with_qwen(...)`: 워커 전용 구조화 분석 및 병합

공시 Qwen의 `impact_score`를 실제로 활용하려면 현재 sentiment만 입력받는 룰엔진 결합 계약도 확장해야 한다.

```python
apply(
    *,
    title: str,
    text: str,
    model_sentiment: str | None,
    model_impact_score: int | None,
    model_confidence: float | None,
) -> InvestmentImpactDecision
```

일반 모델 결과는 기준값으로 사용하고, hard title/critical event 정책만 강제 override한다.

## 8. 워커와 잡 저장소

신규 구성:

- `app/models/analysis_job.py`
- `app/repositories/analysis_job_repository.py`
- `app/workers/analysis_worker.py`

repository 메서드:

- `enqueue(...)`
- `claim_batch(...)`
- `mark_done(...)`
- `mark_retry(...)`
- `mark_failed(...)`

워커는 동시성 1로 시작하고 모델을 프로세스당 한 번만 로드한다. 재시도는 지수 backoff를 적용하고, 오래된 `running` 잡을 다시 `pending`으로 되돌리는 복구 경로를 둔다.

`ANALYSIS_ASYNC_ENABLED=False`일 때는 요청 경로에서 Qwen을 동기 호출하지 않고 baseline만 저장한다. 느린 Qwen 동기 폴백은 관심종목 등록 지연 문제를 되살리므로 제공하지 않는다.

## 9. 설정

추가 설정:

- `ANALYSIS_ASYNC_ENABLED`
- `ANALYSIS_QWEN_ENABLED`
- `ANALYSIS_WORKER_POLL_INTERVAL`
- `ANALYSIS_WORKER_BATCH_SIZE`
- `ANALYSIS_WORKER_MAX_ATTEMPTS`
- `ANALYSIS_NEWS_QWEN_MIN_IMPACT`
- `ANALYSIS_PROMPT_VERSION=evidence-analysis-v2`

`ANALYSIS_QWEN_ENABLED=True`인데 `ANALYSIS_GENERATION_MODEL`이 없으면 enqueue하지 않고 명확한 경고를 기록한다. 운영 배포 검증에서는 이를 실패 조건으로 취급한다.

## 10. API 노출

피드 API는 분석 결과를 이미 join하지만 `event_type`과 `evidence`는 노출하지 않는다. 다음을 함께 변경한다.

- `WatchlistFeedItem`에 `event_type`, `evidence` 추가
- `get_watchlist_feed` 분석 매핑에 두 필드 추가

## 11. 구현 순서

1. Qwen 구조화 출력 스키마, event type 목록, 병합 규칙을 단위 테스트로 고정
2. `EvidenceAnalysisResult`와 `evidence_analysis` 모델/repository 확장
3. `analysis_job` 모델/repository 및 Alembic 마이그레이션 추가
4. `LocalQwenEvidenceAnalyzer`와 검증 하네스 구현
5. baseline/enrichment 서비스 경계 분리
6. 인덱싱 경로에 enqueue 연결
7. 워커 프로세스 구현
8. 피드 API 필드 노출
9. shadow mode 비교 후 Qwen 분류 권위 활성화

## 12. 검증 게이트

단위 테스트:

- Qwen JSON 정상/코드펜스/깨진 JSON
- evidence 원문 포함 여부
- 수치 grounding 성공/실패
- hard rule과 Qwen 충돌
- Qwen 실패 시 baseline 보존
- enqueue 중복 방지
- `SKIP LOCKED` claim과 재시도 상태 전이
- 뉴스 게이팅

shadow mode 평가:

- 기존 baseline 대비 sentiment 정확도
- event type 정확도
- impact score MAE
- JSON 유효율
- evidence 원문 일치율
- 수치 grounding 통과율
- 건당 지연시간

shadow mode에서 기준을 통과하기 전에는 Qwen의 sentiment와 impact를 최종값으로 승격하지 않는다. 그 전까지 Qwen 분류는 `raw_response`에 기록하고 summary/evidence 보강만 적용한다.

통합 검증:

- `alembic upgrade head`
- 전체 테스트
- 실제 표 공시 enqueue 및 워커 완료
- Qwen 중단 상태에서 baseline 정상 제공
- 피드의 event type/evidence 노출

## 13. 완료 기준

- 표 중심 공시는 baseline 저장 후 Qwen enrichment 잡이 생성된다.
- 검증된 Qwen 결과만 baseline에 병합된다.
- Qwen 실패, 워커 중단, 잘못된 JSON이 기존 분석 결과를 훼손하지 않는다.
- 뉴스는 설정된 게이트를 통과한 건만 Qwen으로 보강된다.
- `event_type`과 원문 `evidence`를 DB 및 피드에서 조회할 수 있다.
- shadow mode 평가 결과로 Qwen 분류 권위 활성화 여부를 결정할 수 있다.
