# 공시/뉴스 감성분석 — sLLM(Qwen) 구조화 보강 구현 및 성과

> 작성: 2026-06-09
> 관련: [고도화 로드맵](./evidence-analysis-enhancement-roadmap.md), [상세 계획](../memo/plans/evidence-qwen-structured-analysis-plan.md)
> 상태: 실 prod DB(`stock_debate`)에 적용·검증 완료, 워커 가동 중

---

## 1. 배경 / 문제

기존 감성분석은 **룰엔진 + FinBERT**로 동작했다. 그러나 DART 공시 본문은 100% 직렬화된 표(`필드: 값 | 필드: 값`)라 자연어 문장이 없어, **FinBERT가 표를 못 읽고 거의 neutral만 반환**했다(실데이터 56건 중 비-neutral 1건). 즉 표 중심 공시에서 실제 투자 신호가 분석 결과에 반영되지 못했다.

**목표**: 역할을 분리해 sLLM이 표를 해석하게 한다.
- **Qwen(sLLM)**: 표/문맥 해석, 요약, event_type·근거·리스크 생성 (공시는 구조화 JSON 직접 분석)
- **FinBERT**: 뉴스(prose) 감성의 빠르고 안정적인 기준점
- **룰엔진**: 유상증자·자사주취득 등 정책적 판정 hard override
- **하네스**: JSON 형식·수치 grounding·감성 일관성 검증

원칙: **Qwen을 반드시 호출하되, 검증 없이 믿지 않는다.**

---

## 2. 무엇을 / 어떻게 바꿨나

### 2.1 아키텍처 — 2-phase (동기 baseline + 비동기 Qwen 보강)

```
[동기, 인덱싱 중]
 공시/뉴스 → 룰엔진 + FinBERT → baseline evidence_analysis 즉시 저장
           → 게이트 통과 시 analysis_jobs 큐잉
[비동기, 별도 워커 프로세스]
 job claim → Qwen 구조화 분석(JSON) → grounding·노이즈·일관성 검증
           → 룰엔진 보정 → evidence_analysis 갱신 → job done
```

- baseline을 동기로 먼저 저장 → **피드/API 응답은 즉시**, Qwen 보강은 워커가 따라잡음.
- 워커는 **별도 프로세스**라 무거운 Qwen 모델이 웹 프로세스 메모리를 압박하지 않음. Redis 불필요(Postgres 큐 + `FOR UPDATE SKIP LOCKED`).

### 2.2 게이팅 (Qwen 호출 비용 절감)
- **공시**: 표 본문(`_is_table_heavy`) + 실적·손익 유형 제목(`잠정실적/손익구조변경/결산실적…`)만 호출.
- **뉴스**: FinBERT가 비-neutral 또는 |impact|≥임계일 때만 호출(감성은 FinBERT 권위 유지, Qwen은 요약·근거만).

### 2.3 품질·안정화 하네스
- **수치 grounding**: 요약/근거의 숫자 토큰이 원문에 그대로 존재하는지 검증 → 환각 차단.
- **JSON salvage**: 토큰 한도로 잘려 닫는 괄호가 없는 출력을 마지막 완성 값까지 복구.
- **A. 입력 트리밍**(`_trim_table_for_llm`): 공시 표에서 신호 행만 추려 LLM 입력 축소 → prefill 단축(속도) + 헤더 덤프 유혹 감소(품질).
- **B. 출력 노이즈 필터**(`_filter_noise`): grounding을 통과해도 표 헤더/셀 덤프(`단위 :`, `|` 다수, 한글 없는 조각)는 근거가 아니므로 제거.
- **C. 일관성 가드**(`_consistency_guard`): 감성과 근거 방향이 모순(부정인데 근거가 전부 "증가")이면 `mixed/0`으로 강등.

### 2.4 주요 변경 파일
| 영역 | 파일 |
|---|---|
| 도메인 | [app/domain/evidence_analysis.py](../app/domain/evidence_analysis.py) — `LocalQwenEvidenceAnalyzer`(구조화 JSON), baseline/enrich 분리, 게이팅·하네스·A/B/C |
| 인덱싱 | [app/domain/evidence_indexing.py](../app/domain/evidence_indexing.py) — baseline 저장 + `_maybe_enrich`(큐잉/인라인) |
| 큐 | [app/repositories/analysis_jobs_repository.py](../app/repositories/analysis_jobs_repository.py) — enqueue/claim_batch/mark_done/mark_failed |
| 워커 | [app/workers/analysis_worker.py](../app/workers/analysis_worker.py) — `python -m app.workers.analysis_worker` |
| 모델 | [app/models/analysis_jobs.py](../app/models/analysis_jobs.py), [app/models/evidence_analysis.py](../app/models/evidence_analysis.py)(+`event_type`/`evidence`) |
| 마이그레이션 | [alembic/versions/20260608_b1f2a3c4d5e6_evidence_analysis_enrichment.py](../alembic/versions/20260608_b1f2a3c4d5e6_evidence_analysis_enrichment.py)(멱등) |
| 설정/노출 | [app/config.py](../app/config.py)(Qwen·async·prompt_version v2), [app/api/watchlist.py](../app/api/watchlist.py)·[app/schemas/market_data.py](../app/schemas/market_data.py)(feed에 event_type/evidence) |

---

## 3. 운영 이슈 해결 (배포 중)

- **깨진 alembic 고아 revision**: prod DB의 `alembic_version`이 파일/git 어디에도 없는 `b1c2d3e4f5a6`를 가리켜 `alembic upgrade`가 실패. → `alembic stamp --purge a8a60fcd0ed2` → `upgrade head`로 정상화(`b1f2a3c4d5e6`). 마이그레이션이 멱등이라 기존 데이터(72건) 무손실.
- **큰 실적표 `qwen_no_output`**: 1차 실행 시 영업(잠정)실적 표에서 Qwen이 evidence에 표를 통째 복사 → 512토큰 초과 절단 → JSON 미완성으로 실패. → 프롬프트 evidence 제한 + `max_new_tokens` 768 + JSON salvage로 해결.

---

## 4. 성과 (실 prod 측정)

### 4.1 핵심 — 표 공시 신호 회복
FinBERT만 쓸 때 neutral만 찍던 표 공시를 Qwen이 읽어 판정:
- 두산로보틱스(454910) 손익구조변경 → `negative -2`, key_points `매출액/영업이익/당기순이익 감소`
- 영업(잠정)실적 다수 → `negative`, event_type `잠정실적`

### 4.2 품질 개선 (A/B/C 적용 전 → 후, 실 데이터)
| 사례 | 적용 전 | 적용 후 |
|---|---|---|
| 000990 | key_points가 표 헤더 덤프(`연결실적내용: … \| 단위 : 백만원 …`) | `매출액/영업이익/당기순이익 감소` |
| 000270 | `negative`인데 근거가 `매출액 증가/영업이익 증가`(모순) | `negative` + 근거 `매출액/영업이익 감소`(일관) |

### 4.3 집계 (게이트 공시 15건, 신코드·라이브 워커)
- **key_points 노이즈 잔존: 0 / 24** (적용 전 다수 → 0)
- 일관성 충돌: 0건
- sentiment 분포: negative 7 · mixed 3 · neutral 5
- event_type 분포: 잠정실적 6 · 손익구조변경 1 · 기타 8(결산예고)
- 처리: 15건 289초 (≈ 건당 12~13초, 트리밍으로 23s→12s대)
- 기존 단위테스트 9개 통과

---

## 5. 활성화 / 운영 방법

1. `ANALYSIS_GENERATION_MODEL`에 모델 지정(예: `Qwen/Qwen2.5-1.5B-Instruct`) — 미설정이면 baseline(FinBERT)만 동작.
2. DB 마이그레이션: `alembic upgrade head`.
3. 워커 상시 실행: `python -m app.workers.analysis_worker` (별도 프로세스/서비스).

안전장치: `ANALYSIS_ASYNC_ENABLED=False`면 동기 폴백, Qwen 미설정이면 현행 동작 유지. `prompt_version`을 v2로 분리해 v1 데이터와 충돌 없음.

---

## 6. 남은 과제

- **모델 품질 천장**: 1.5B는 지저분한 표에 한계. 더 높이려면 3B/7B 상향 또는 증류 기반 LoRA 파인튜닝([증류 계획](../memo/plans/evidence-analysis-distillation-plan.md)).
- **속도**: 트리밍은 입력만 줄여 출력이 긴 건(~30s)엔 한계. `max_new_tokens` 하향/evidence 개수 제한으로 추가 단축 가능(품질 trade).
- **워커 영구화**: 현재 수동 기동. systemd/docker/Procfile로 서비스화 필요.
- **뉴스 경로 벤치**: FinBERT prose 정확도 + 게이팅된 Qwen 보강 품질 실데이터 검증 미실시.
