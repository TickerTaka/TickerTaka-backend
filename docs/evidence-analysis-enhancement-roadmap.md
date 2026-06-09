# 공시/뉴스 감성분석 — 향후 고도화 로드맵

> 작성: 2026-06-08
> 기준: 감성분석 레이어 + 공시 커버리지 개선 + feed API 노출 완료
> 관련 문서:
> [기존 구현 계획](./evidence-llm-analysis-implementation-plan.md),
> [Qwen 구조화 분석 상세 계획](../memo/plans/evidence-qwen-structured-analysis-plan.md)

---

## 현재 상태

분석 결과는 **FinBERT + `InvestmentImpactRuleEngine`**으로 생성되어 `evidence_analysis`에 저장되고, 관심종목 feed API에도 노출된다.

- 뉴스와 자연어 문장: FinBERT가 감성 기준점 역할을 한다.
- 명확한 사건 제목: 룰엔진의 hard override가 최종 정책을 보장한다.
- 표 중심 공시: FinBERT가 대부분 neutral을 반환하며 룰엔진 의존도가 높다.
- Qwen: 현재 `표 요약 -> FinBERT` 경로로 구현되어 있지만 기본 off(`ANALYSIS_GENERATION_MODEL=None`)다.

실데이터 두산로보틱스(454910) 검증에서는 표 공시 56건 중 FinBERT가 비-neutral을 반환한 건이 사실상 1건이었다. 따라서 **Qwen은 표 공시의 의미를 복원하는 필수 해석기**로 사용하되, 모든 생성 결과는 하네스로 검증한다.

---

## 목표 구조

```text
[동기 baseline]
뉴스/공시 -> FinBERT 가능한 입력 분류 -> 룰엔진 보정 -> 즉시 저장
                                      -> 조건 통과 시 Qwen enrichment job enqueue

[비동기 enrichment]
job -> Qwen 구조화 분석 -> 스키마/근거/수치/일관성 검증
    -> 룰엔진 정책 보정 -> 검증 성공 필드만 baseline에 병합
```

역할 분담:

- **Qwen**: 표 공시 구조화 해석, 요약, 사건 유형, 근거와 리스크 생성
- **FinBERT**: 뉴스와 자연어형 입력의 빠른 감성 기준점
- **룰엔진**: 명확한 사건과 정책적 영향도 보정
- **하네스**: JSON 오류, 환각, 수치 불일치, 감성-영향도 충돌 차단

핵심 원칙은 **Qwen을 표 공시 분석에 반드시 사용하되, 검증 없이 최종값으로 믿지 않는 것**이다.

---

## 우선순위 요약

| 순위 | 항목 | 해결하는 문제 | 규모 |
|---:|---|---|---|
| 1 | Qwen 구조화 분석 + 비동기 enrichment | 표 공시 FinBERT 무력, 2-hop 정보 손실, 동기 경로 지연 | 큼 |
| 2 | 정식 Alembic 마이그레이션 + `analysis_job` | 수동 테이블 생성, 워커 잡 상태 부재 | 중간 |
| 3 | 키워드 경로 부정문 가드 확장 | `"○○ 여부: 아니오"` 체크리스트 오탐 | 작음 |
| 4 | 공시 refresh 스케줄러 | 등록 이후 신규·정정 공시 미반영 | 중간 |
| 5 | 뉴스 FinBERT 검증 + Qwen 조건부 보강 | 실제 뉴스 정확도 미검증, 근거·요약 부족 | 중간 |
| 6 | 표 필드 파서 + grounding 정교화 | 생성형 의존도와 수치 오탐 감소 | 큼 |

---

## 1. Qwen 구조화 분석 + 비동기 Enrichment

### 문제

현재 구현은 표 공시를 Qwen이 한 문장으로 요약한 뒤 FinBERT가 다시 분류한다. 이 경로는 다음 한계가 있다.

- 요약 단계에서 사건 유형, 근거, 리스크가 손실될 수 있다.
- Qwen 요약이 grounding에 실패해도 현재는 FinBERT 분류 입력으로 사용된다.
- Qwen을 동기 인덱싱에서 호출하면 건당 약 10초가 추가되어 365일 백필이 느려진다.
- 현재 Qwen은 `summary`만 생성하며 `sentiment`, `impact_score`, `event_type`, `evidence`를 직접 출력하지 않는다.

### 할 일

- [ ] `LocalQwenSummarizer`를 구조화 출력용 `LocalQwenEvidenceAnalyzer`로 확장
- [ ] 공시 출력에 `summary`, `event_type`, `sentiment`, `impact_score`, `confidence`, `key_points`, `risks`, `evidence` 포함
- [ ] 동기 `baseline` 분석과 비동기 Qwen `enrichment` 분리
- [ ] 표 중심 공시는 Qwen enrichment 필수 enqueue
- [ ] 명확한 제목 hard rule 공시는 판정용 Qwen을 생략하고 필요 시 요약만 생성
- [ ] Qwen 실패·타임아웃·잘못된 JSON 발생 시 기존 baseline 보존
- [ ] 모델 프로세스당 1회 로딩 및 워커 동시성 1로 시작

### 전환 정책

Qwen의 sentiment와 impact는 처음부터 최종값으로 사용하지 않는다.

1. shadow mode에서 Qwen 결과를 `raw_response`에 기록한다.
2. 사람 검증셋으로 정확도, impact MAE, JSON 유효율, evidence 일치율, grounding 통과율을 측정한다.
3. 기준 통과 후 표 공시에 한해 Qwen 분류를 기준값으로 승격한다.
4. hard rule과 critical event 정책은 항상 최종 가드레일로 유지한다.

---

## 2. 정식 Alembic 마이그레이션 + `analysis_job`

### 문제

- `evidence_analysis`는 SQLAlchemy 모델이 있지만 Alembic 초기 마이그레이션에는 누락되어 수동 SQL로 생성된다.
- `event_type`, 원문 근거 `evidence`를 저장할 정식 컬럼이 없다.
- Qwen enrichment 상태와 실패 원인을 관리할 잡 테이블이 없다.

### 할 일

- [ ] `evidence_analysis` 생성 및 기존 환경 보존형 Alembic 마이그레이션 추가
- [ ] `event_type VARCHAR(40)`, `evidence JSONB` 컬럼과 인덱스 추가
- [ ] 별도 `analysis_job` 테이블 및 repository 추가
- [ ] `SELECT ... FOR UPDATE SKIP LOCKED` 기반 claim 구현
- [ ] 재시도, backoff, 오래된 running job 복구 구현
- [ ] `scripts/create_evidence_analysis_table.sql` 동기화

저장소는 Redis와 Celery 의존성을 이미 갖고 있지만 실제 Celery 워커 구성은 없다. 첫 구현은 별도 프로세스 + Postgres 잡 큐로 시작하고, 처리량이 증가하면 repository 경계를 유지한 채 Celery로 교체한다.

---

## 3. 키워드 경로 부정문 가드 확장

### 문제

critical 경로에는 `_critical_negative_present`가 있어 `횡령ㆍ배임사항 기재여부: 아니오` 같은 오탐을 차단한다. 일반 `NEGATIVE_KEYWORDS`/`POSITIVE_KEYWORDS` hit 계산에는 동일한 부정문 가드가 없다.

### 할 일

- [ ] 키워드가 등장한 문장 또는 표 행 단위로 hit 계산
- [ ] `아니오`, `없음`, `해당사항 없음` 등 부정 표현이 있는 행의 hit 제외
- [ ] 부정 표현 자체가 사건 설명인 사례와 체크리스트 응답을 구분하는 회귀 테스트 추가

---

## 4. 공시 Refresh 스케줄러

### 문제

관심종목 등록 시 최초 365일 공시는 수집하지만, 이후 발생하는 신규·정정 공시는 자동 반영되지 않는다.

### 할 일

- [ ] 관심종목별 최근 30일 공시 주기 조회
- [ ] 신규·변경 공시만 인덱싱, baseline 분석, enrichment enqueue
- [ ] 종목별 실패 격리와 재시도
- [ ] DART API 호출 한도 및 Redis lock 활용
- [ ] 정정 공시가 기존 분석을 갱신하는 정책 정의

---

## 5. 뉴스 FinBERT 검증 + Qwen 조건부 보강

### 근거

FinBERT는 자연어 테스트 문장에는 강하게 반응했지만 실제 수집 뉴스에 대한 분포와 정확도는 아직 검증되지 않았다. 뉴스 전건에 Qwen을 호출하면 비용과 처리 지연이 커지므로 조건부 보강이 적합하다.

### 할 일

- [ ] 실제 뉴스 검증셋으로 sentiment 분포와 macro-F1 측정
- [ ] 종목명만 언급한 기사, 과장 제목, 부정문, 인용문 회귀 케이스 추가
- [ ] FinBERT non-neutral 또는 `|impact_score|` 임계 이상 뉴스만 Qwen enqueue
- [ ] 뉴스 Qwen은 `summary`, `key_points`, `risks`, `evidence` 보강에 사용
- [ ] 뉴스 최종 sentiment와 impact는 FinBERT + 룰엔진 결과 유지

---

## 6. 표 필드 파서 + Grounding 정교화

### 표 필드 직접 해석

잠정실적의 `증감율`, `흑자적자전환여부`, 손익구조변경의 주요 수치처럼 안정적인 필드는 직접 파싱한다.

- Qwen 입력을 압축하고 명확한 수치 신호를 제공한다.
- Qwen 결과 검증 기준으로도 활용한다.
- 공시 유형별 필드맵과 단위 정규화가 필요하므로 점진적으로 확장한다.

### Grounding 개선

- [ ] `17.6%`와 원문의 `17.6` + 분리된 `%` 헤더를 동일 수치로 인식
- [ ] 숫자뿐 아니라 Qwen `evidence`가 원문에 실제 존재하는지 검증
- [ ] summary/key points/risks/evidence를 필드별로 검증하고 실패 필드만 폐기
- [ ] grounding 실패 결과를 FinBERT 입력이나 최종 분류에 사용하지 않음

---

## 이미 완료된 것

- ✅ 감성분석 레이어 및 `evidence_analysis` 적재
- ✅ 모델 중심 결합: 명확한 사건 제목만 hard override, 일반 키워드는 보정, 충돌 시 mixed/0
- ✅ critical 오탐 수정: 사건 제목 우선, 부정문 가드, 횡령·배임 본문 제외
- ✅ 공시 커버리지: 최초 365일 백필 + DART 페이지네이션
- ✅ feed API 분석 결과 노출: sentiment, impact, confidence, summary, key points, risks
- ✅ DB에 model name, HF 라벨, decision source 보존

---

## 완료 기준

- 표 중심 공시는 baseline 저장 후 Qwen enrichment 잡이 생성된다.
- 검증된 Qwen 결과만 baseline에 병합된다.
- Qwen 실패나 워커 중단이 기존 분석 결과를 훼손하지 않는다.
- 뉴스는 게이트를 통과한 건만 Qwen으로 보강된다.
- `event_type`과 원문 `evidence`를 DB와 feed API에서 조회할 수 있다.
- 실제 검증셋 결과로 Qwen 분류 권위 활성화 여부를 결정할 수 있다.
