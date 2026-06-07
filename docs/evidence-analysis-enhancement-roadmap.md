# 공시/뉴스 감성분석 — 향후 고도화 로드맵

> 작성: 2026-06-08
> 기준 커밋: `mergedb` (감성분석 레이어 + 공시 커버리지 개선 머지 완료)
> 관련 문서: [evidence-llm-analysis-implementation-plan.md](./evidence-llm-analysis-implementation-plan.md)

---

## 현재 상태 (한 줄 요약)

감성분석은 **룰엔진(키워드/제목) + FinBERT** 로 동작하고 `evidence_analysis` 테이블에 적재된다.
실데이터(두산로보틱스 454910) 기준 **FinBERT는 표 공시에서 거의 neutral만 찍고, 실제 긍·부정 신호는 룰엔진이 만든다.**
Qwen 요약→FinBERT 경로는 코드에 들어가 있으나 **기본 off**(`analysis_generation_model=None`)다.

---

## 우선순위 요약

| 순위 | 항목 | 해결하는 문제 | 규모 |
|---:|---|---|---|
| 1 | 프론트 노출 (feed API 확장) | 분석 결과가 프론트로 안 나감 | 작음 |
| 2 | Qwen→FinBERT 비동기 고도화 | 표 공시에서 FinBERT 무력 / 동기 경로 느림 | 큼 |
| 3 | 정식 alembic 마이그레이션 | evidence_analysis 테이블 프로비저닝 | 작음 |
| 4 | 키워드 경로 부정문 가드 | "○○ 여부: 아니오" 체크리스트 오탐 잔존 | 작음 |
| 5 | Refresh 스케줄러 | 등록 이후 신규 공시 자동 반영 | 중간 |
| 6 | 뉴스 경로 FinBERT 검증 | FinBERT 본업(prose)이 검증 안 됨 | 중간 |
| 7 | 표 구조화 해석 / grounding 완화 | 실적 표의 숫자 신호 정밀 추출 | 큼 |

---

## 1. 프론트 노출 — feed API에 감성 실어보내기 (우선)

**문제**: `evidence_analysis`(sentiment/impact/key_points/risks)가 API·스키마 어디에도 노출되지 않음. 현재는 토론 컨텍스트 내부용으로만 쓰임.

**할 일**
- [app/schemas/market_data.py](../app/schemas/market_data.py) `WatchlistFeedItem`에 필드 추가: `sentiment`, `impact_score`, `analysis_summary`, `key_points`, `risks`
- [app/api/watchlist.py](../app/api/watchlist.py) `get_watchlist_feed`에서 `EvidenceAnalysisRepository.get_by_sources("news"/"filing", ids)`로 join 후 각 FeedItem에 채움

**효과**: 프론트가 feed 한 번 호출로 뉴스/공시별 `[negative][영향도 -1]` 배지 + 핵심근거/리스크를 렌더링.

**참고**: `impact_score`는 -2~+2 (강도), `sentiment`는 방향(positive/negative/neutral/mixed). 프론트는 sentiment로 색, impact_score로 강도 표시 권장.

---

## 2. Qwen 요약 → FinBERT 비동기 고도화 (핵심)

**문제**: DART 공시 본문은 100% 직렬화된 표(`필드: 값 | 필드: 값`)라 자연어 문장이 없음. FinBERT가 표를 못 읽고 neutral만 반환 → 실데이터 56건 중 FinBERT가 비-neutral을 낸 건 사실상 1건뿐.

**검증된 사실 (이번 세션)**: Qwen이 표를 자연어로 요약하면 FinBERT가 제대로 판정함.
- 잠정실적: `FinBERT(raw표)=neutral` → `Qwen요약("영업이익 26.6% 감소")→FinBERT=negative` ✅

**현재 구현 상태**: 코드 존재, 기본 off.
- `LocalQwenSummarizer` ([app/domain/evidence_analysis.py](../app/domain/evidence_analysis.py))
- 게이트: 제목 규칙 없음 + 표 본문(`_title_has_rule`, `_is_table_heavy`)
- Qwen 요약 → FinBERT 입력 + (grounding 통과 시) 저장 summary
- on/off: `ANALYSIS_GENERATION_MODEL` 설정

**남은 고도화 (켜기 전 필요)**
- [ ] **동기 인덱싱에서 분리** — Qwen은 건당 ~10초라 최초 365일 백필 시 수 분 소요. 룰+FinBERT로 즉시 분석 저장 후, Qwen 보강은 큐/워커 비동기 후처리로 (설계문서 13-3).
- [ ] **게이트 축소** — "제목 규칙 없는 표 공시" 전부가 아니라 **실적·손익 유형(잠정실적/손익구조변경)** 으로 한정해 호출 건수 감소.
- [ ] 실패/타임아웃 폴백, 모델 로딩 1회 amortize 확인.

---

## 3. 정식 alembic 마이그레이션

**문제**: `evidence_analysis` 테이블이 [scripts/create_evidence_analysis_table.sql](../scripts/create_evidence_analysis_table.sql) 수동 SQL로만 프로비저닝됨. (main 머지로 alembic 툴링은 이제 들어옴: `alembic.ini`, `alembic/versions/`)

**할 일**: `alembic revision`으로 `evidence_analysis` 생성 마이그레이션 추가 (`op.execute(<SQL>)` 또는 `op.create_table`), `alembic upgrade head` 검증.

---

## 4. 키워드 경로 부정문 가드 확장

**문제**: critical 경로엔 부정문 가드(`_critical_negative_present`)를 넣어 `횡령ㆍ배임사항 기재여부: 아니오` 류 오탐을 막았으나, **`NEGATIVE_KEYWORDS` 경로엔 가드가 없음.** 제목이 행정공시가 아닌 표 공시가 같은 체크리스트를 포함하면 여전히 negative 오탐 가능 (실무상 admin 제목이 덮어 위험은 낮음).

**할 일**: `negative_hits`/`positive_hits` 계산 시 부정어가 있는 줄의 키워드는 제외하는 가드를 동일하게 적용.

---

## 5. Refresh 스케줄러

**문제**: 현재 공시 동기화는 관심종목 **최초 등록 시점**에만 자동 실행됨. 등록 이후 발생하는 신규/정정 공시는 수동 동기화 전까지 시스템이 모름.

**할 일**: 주기 스케줄러로 관심종목별 최근 30일(refresh) 공시 조회 → 신규만 인덱싱/분석. 실패 재시도·종목 격리·DART 호출 한도 관리 포함.

---

## 6. 뉴스 경로 FinBERT 검증

**근거**: FinBERT는 prose(자연어)엔 정확히 반응함을 확인 (호재 문장→positive 1.00, 악재 문장→negative 1.00). 뉴스 본문은 prose라 FinBERT 본업이 제대로 작동할 가능성이 높음. 그러나 실제 뉴스 데이터로 검증/벤치가 안 됨.

**할 일**: 관심종목 뉴스 수집 후 `analyze_news_row` 결과의 sentiment 분포·정확도 점검. 표 공시와 달리 뉴스는 Qwen 없이도 FinBERT가 신호를 낼 것으로 기대.

---

## 7. 표 구조화 해석 / grounding 완화 (선택)

- **표 셀 직접 해석(B안)**: 잠정실적 표의 `흑자적자전환여부`, `증감율` 같은 신호 셀을 직접 읽어 sentiment 결정. Qwen 없이 정확하지만 공시유형별 필드맵 필요(비용 큼). Qwen 경로의 대안/보완.
- **grounding 완화**: 현재 `_verify_numerical_grounding`이 `17.6%`(요약) vs `17.6`(원문, %는 헤더 분리)를 불일치로 봐 Qwen 요약이 저장 안 됨(sentiment엔 사용됨). 단위 분리 케이스 허용하도록 완화하면 저장 품질↑.

---

## 이미 완료된 것 (참고)

- ✅ 감성분석 레이어 (FinBERT + 룰엔진 결합, 필드별 하네스 분리)
- ✅ 모델 중심 결합: 명확한 사건 제목만 hard override, 일반 키워드는 보정, 충돌 시 mixed/0
- ✅ critical 오탐 수정 (사건 제목 우선 / 부정문 가드 / 횡령·배임 본문 제외) — 두산 검증 오탐 0
- ✅ 공시 커버리지: 최초 365일 백필 + DART 페이지네이션
- ✅ DB 적재 확인 (`evidence_analysis`, model_name/hf 라벨/decision.source 보존)
- ✅ origin/main 머지 (충돌 0)
