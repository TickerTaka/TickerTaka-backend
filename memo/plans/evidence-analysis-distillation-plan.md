# 증거 분석 분류기 Teacher-Student 학습 계획 (KR-FinBERT 재학습 + Qwen LoRA)

## 목표

뉴스·공시 한 건을 받아 **투자 관점 영향도**를 구조화 JSON으로 산출하는 로컬 분석기를, 강한 teacher 모델로 라벨을 만들고 작은 로컬 student를 학습시키는 distillation 방식으로 고도화한다.

핵심 원칙:
- **일반 문장 감성이 아니라 투자 영향도를 학습**한다. 유상증자·대규모 투자·기업 인수처럼 해석이 갈리는 사례를 위해 `neutral`/`mixed`/`impact_score`를 유지한다.
- **현재 layering 구조를 유지**한다. `모델 출력 → InvestmentImpactRuleEngine 보정 → impact_score`. 키워드 규칙은 갈아엎지 않고 **가드레일**로 남긴다.
- **감성값**은 KR-FinBERT 재학습으로, **요약·근거·리스크 생성**은 Qwen LoRA로 분담한다. 처음부터 LLM 한 개로 다 하지 않는다.
- **라이선스를 먼저 확정**한다. teacher 모델 약관, 뉴스 API 약관, 공시 수집 경로를 학습 시작 전에 정리한다.
- **사람 검증셋은 학습에 넣지 않고 별도 보관**한다 (데이터 누수 방지).

## 배경: 현재 구현 현황 (코드 확인 기준)

| 구성 | 위치 | 현황 |
|---|---|---|
| 분석 서비스 | `app/domain/evidence_analysis.py` | `EvidenceAnalysisService` 단일 진입점 |
| 감성 분류기 | `app/domain/evidence_analysis.py:102` `LocalHFSentimentAnalyzer` | KR-FinBERT 파이프라인 lazy 로드 + 실패 시 rule-only 폴백 |
| 영향도 규칙 엔진 | `app/domain/evidence_analysis.py:193` `InvestmentImpactRuleEngine` | 키워드 기반 sentiment/impact 보정 (가드레일) |
| 추출 요약기 | `ExtractiveSummaryBuilder` | DART 보일러플레이트 제거 + 숫자 문장 추출 |
| 기본 모델 | `app/config.py:29` | `analysis_model=snunlp/KR-FinBert-SC`, `analysis_provider=local_hf` |
| 생성 모델 슬롯 | `app/config.py:34` | `analysis_generation_model` (현재 `None`) — Qwen 붙일 자리 |
| Qwen 프로토타입 | `scripts/prototype_evidence_analysis_qwen.py` | `Qwen2.5-3B-Instruct`, extractive/qwen provider, JSON 추출 + 수치 grounding 검증까지 구현됨 |
| 벤치마크 | `scripts/run_analysis_benchmark.py` | 3개 케이스 회귀 테스트 (mixed/negative/neutral) |
| DB 스키마 | `app/models/evidence_analysis.py` | sentiment, impact_score, confidence, summary, key_points(JSONB), risks(JSONB), model_name, prompt_version, raw_response |
| 외부 API | `app/config.py:64-66` | `DART_API_KEY`(OpenDART), `NAVER_NEWS_CLIENT_*` 이미 배선됨 |

**즉, 이 계획은 새 시스템을 만드는 게 아니라 기존 분류기/규칙/프로토타입을 학습 가능한 형태로 잇는 작업이다.**

## 목표 스키마

리뷰 제안 스키마를 채택한다. 현재 컬럼(`sentiment`, `impact_score`, `confidence`)은 유지하고 `horizon`, `event_type`, `evidence`를 추가한다.

```json
{
  "sentiment": "positive | negative | neutral | mixed",
  "impact_score": -2,
  "horizon": "short | mid | long",
  "event_type": "rights_offering",
  "evidence": ["기존 주주의 지분이 희석된다"],
  "confidence": 0.91
}
```

- `sentiment` / `impact_score`(-2~2) / `confidence`(0~1): **기존 유지**.
- `horizon`, `event_type`: **신규**. 우선 `raw_response`(JSONB)에 넣어 무중단 도입 후, 안정화되면 정식 컬럼 + alembic 마이그레이션으로 승격.
- `evidence`: 기존 `key_points`/`risks`와 매핑 검토 (중복 신설 대신 재활용 우선).
- `event_type` 어휘는 가치 평가가 아닌 **사건 유형 분류**로 고정한다 (예: `rights_offering`, `cb_issuance`, `mna`, `buyback`, `earnings_up`, `earnings_down`, `litigation`, `admin_disclosure`, `capital_reduction`, ...). 확정 목록은 라벨 기준표에서 동결한다.

## DB 저장 — 새 테이블·컬럼 불필요 (현황 정리)

**지금 당장 DB를 손댈 필요는 없다.** 분석 결과를 담는 `evidence_analysis` 테이블과 `sentiment` 컬럼이 이미 있고, 저장 코드도 **모델 비종속적으로** 이미 작성돼 있다.

```
[분석기]               [공통 결과 객체]               [저장]
KR-FinBERT ─┐
            ├─→  EvidenceAnalysisResult  ─→  repository.upsert_analysis()  ─→ evidence_analysis
Qwen       ─┘
```

- 저장 진입점: `app/domain/evidence_analysis.py:315` — `persist=True`면 `repository.upsert_analysis(**result.to_dict())`가 이미 호출된다.
- 따라서 KR-FinBERT를 Qwen으로 바꿔도 **저장 코드를 새로 짜는 게 아니라**, 분석기(`LocalHFSentimentAnalyzer`)만 교체하면 결과가 같은 경로로 DB에 들어간다.

**그래도 코드를 새로 짜야 하는 부분은 정확히 둘뿐이다:**

| 저장 대상 | 현재 저장 코드 | 작업 필요? |
|---|---|---|
| sentiment, impact_score, summary, key_points, risks, confidence | 있음 | ❌ 그대로 |
| **horizon, event_type, evidence** (신규 필드) | 없음 | ✅ 필요 |
| **Qwen 분석기 본체** (KR-FinBERT 대체/병행) | 없음 | ✅ 필요 |

신규 필드 저장 방식 (목표 스키마 절과 동일 원칙):
- **시작점(무중단)**: `raw_response`(JSONB)에 넣는다 → **DB 마이그레이션 0**, `EvidenceAnalysisResult`/`upsert_analysis`에 필드만 추가.
- **승격(후)**: 안정화되면 alembic 마이그레이션으로 정식 컬럼화.

## 라이선스 — 학습 시작 전 게이트 (Phase 0에서 차단)

> **이 단계가 통과되기 전에는 어떤 라벨 생성도 시작하지 않는다.**

1. **Teacher 모델**: Claude로 라벨을 만들어 다른 모델을 학습시키는 것은 Anthropic 약관(경쟁 모델 학습 제한)에 걸릴 수 있는 회색지대다. 본 계획은 **법률 자문이 아니며**, 안전한 기본값으로 **오픈 웨이트 teacher**(예: Qwen2.5/Qwen3 계열, Apache 2.0)를 사용한다. Claude를 teacher로 쓰려면 Anthropic 서면 허가 후에만 진행한다.
   - 참고: 코드의 `judge_llm_model=anthropic/claude-haiku-4-5`는 **실시간 추론용**이며 학습 라벨 생성과 용도가 다르다. 혼동 금지.
2. **뉴스**: 라이선스가 허용된 API만 사용. 이미 배선된 Naver News API의 약관상 **저장/재학습 가능 범위**를 확인한다. 불명확하면 본문 저장 대신 분석 결과만 보관하는 방식을 검토.
3. **공시**: 무작정 스크래핑 금지. **OpenDART API**(`DART_API_KEY`)만 사용. 이미 `app/external/dart/`에 수집 경로 존재.

산출물: `memo/process/distillation-license-review.md` (teacher/뉴스/공시 3축 결론 + 근거 링크).

## 단계별 계획

### Phase 0 — 라이선스 검토 + 라벨 기준표 (사람 작업)

- 위 라이선스 게이트 통과.
- **라벨 기준표(rubric) 작성**: sentiment/impact_score/horizon/event_type 각 값의 판정 기준과 경계 사례(유상증자=부정, 타법인 취득=mixed 등)를 문서화. 현재 `InvestmentImpactRuleEngine`의 키워드 규칙과 벤치마크 케이스가 초안 역할을 한다.
- 산출물: `memo/process/evidence-label-rubric.md`.

### Phase 1 — 데이터 수집 + 중복 제거 (1,000건)

- OpenDART 공시 + 라이선스 뉴스 합쳐 **1,000건** 수집. 종목·이벤트 유형이 한쪽에 쏠리지 않게 stratify.
- 중복 제거: 제목/본문 정규화 후 해시 + 임베딩(`jhgan/ko-sroberta-multitask`, 이미 사용 중) 유사도로 near-dup 제거.
- 산출물: `seeds/` 또는 `data/distill/raw/` 에 정규화 JSONL. 신규 스크립트 `scripts/collect_distill_dataset.py`.

### Phase 2 — Teacher 초벌 라벨 생성

- 오픈 웨이트 teacher로 1,000건 전체에 Phase 0 스키마 JSON 라벨 부착.
- **기존 자산 재활용**: `prototype_evidence_analysis_qwen.py`의 `build_qwen_prompt`, `extract_json_object`, `validate_payload`(수치 grounding 검증), `coerce_payload`를 라벨 생성 파이프라인으로 승격. 새로 만들지 말 것.
- 산출물: `data/distill/labeled_teacher.jsonl`.

### Phase 3 — 사람 검수 (≥20%) + 검증셋 격리

- 무작위 추출 표본 **20% 이상** 사람 검수, 애매 케이스 수정.
- **사람이 직접 만든/수정한 검증 데이터는 학습셋에 넣지 않고 별도 보관**한다.
- 분할: train / dev / **held-out human-verified test**. test는 학습·튜닝에서 완전 격리.
- 산출물: `data/distill/{train,dev,test}.jsonl` + 검수 로그.

### Phase 4 — KR-FinBERT 재학습 (감성 우선)

- `snunlp/KR-FinBert-SC` (또는 동급 한국어 금융 분류기)를 sentiment(+impact 보조 라벨)로 fine-tune.
- 산출물 모델을 `LocalHFSentimentAnalyzer`가 그대로 로드하도록 `ANALYSIS_MODEL`만 교체 (코드 변경 최소화).
- **`InvestmentImpactRuleEngine`은 그대로 가드레일로 유지** — 모델 출력 위에 보정 레이어로 계속 작동.
- 산출물: 학습 스크립트 `scripts/train_finbert_sentiment.py`, 모델 아티팩트.

### Phase 5 — 평가 (게이트)

- **별도 held-out 검증셋**에서: macro-F1, **event_type별 오분류율**, impact_score MAE.
- 기존 `run_analysis_benchmark.py` 회귀 케이스 전원 통과 확인 (mixed/negative/neutral).
- 기준 미달이면 Phase 1~3로 회귀 (데이터 보강/라벨 정제).
- 산출물: `memo/results/distillation-eval-v1.md`.

### Phase 6 — Qwen LoRA (요약·근거·리스크 생성) — 조건부

> **진입 조건**: Phase 5 통과 후, 추출 요약(`ExtractiveSummaryBuilder`)·규칙 기반 근거의 품질이 부족할 때만 착수. 감성/영향도는 KR-FinBERT로 이미 충분하므로, Qwen은 **"요약 + 근거(evidence) + 리스크 + event_type 설명"의 자연어 생성**만 맡는다.

#### 6-1. 베이스 모델 선택
- 후보 비교: `Qwen2.5-3B-Instruct`(프로토타입 기존) vs **`Qwen3-4B-Instruct-2507`(Apache 2.0)**.
- 선택 기준: 한국어 금융 텍스트 JSON 생성 품질, 로컬 추론 메모리(VRAM/통합메모리), 라이선스. 졸프 환경(맥 MPS / 단일 GPU) 기준으로 메모리 안 터지는 가장 큰 모델.

#### 6-2. 학습 데이터 포맷 (instruction → JSON)
- Phase 3 산출물(`data/distill/{train,dev}.jsonl`)을 **instruction 튜닝 포맷**으로 변환.
- 입력: `build_qwen_prompt`(프로토타입에 이미 있음)로 만든 프롬프트. 출력(정답): 사람이 검수한 목표 스키마 JSON(`summary`/`evidence`/`risks`/`event_type` 중심).
- **감성/impact는 학습 타깃에서 제외하거나 보조로만** — 그건 KR-FinBERT 담당. 역할 중복 방지.
- 출력 형식 강제: JSON만 생성하도록 system 프롬프트 고정 + 학습 정답도 순수 JSON. (추론 시 `extract_json_object`/`validate_payload`가 가드레일)

#### 6-3. 학습 방식
- **LoRA** 우선, 메모리 부족하면 **Q-LoRA**(4-bit 양자화 + LoRA). Qwen 공식 학습 문서/`peft`+`transformers`+`trl(SFTTrainer)` 스택.
- 초기 하이퍼파라미터(출발점, 데이터 보고 조정): LoRA `r=16, alpha=32, dropout=0.05`, target_modules = attention/MLP 투영층, lr `1e-4~2e-4`, epoch `2~3`, max_seq_len은 `analysis_max_chars`(6000자) 고려해 토큰 길이 산정.
- 1,000건 규모에서는 **과적합 경계** — dev셋 loss/JSON 유효율로 early stop, epoch 과다 금지.

#### 6-4. 평가 (생성 품질 전용 게이트)
- held-out test셋에서: **JSON 파싱 성공률**, **수치 grounding 정확도**(프로토타입 `verify_numerical_grounding` 재사용 — 요약 속 숫자가 원문에 실재하는지), event_type 일치율, 요약/근거의 사람 평가(샘플 rubric 채점).
- 베이스 모델(파인튜닝 전) vs LoRA 적용 후를 같은 셋으로 비교해 **개선폭이 있을 때만 채택**.

#### 6-5. 배선 (추론 통합)
- `analysis_generation_model` 설정 슬롯(이미 존재)에 LoRA 머지/어댑터 경로 주입.
- `analysis_summary_provider`를 `extractive` → `qwen` 으로 전환 가능하게 (기본은 안전한 extractive 유지, 플래그로 점진 전환).
- 추론 가드레일: `extract_json_object` → `validate_payload`(수치 grounding) 실패 시 **추출 요약으로 폴백** (`LocalHFSentimentAnalyzer`의 rule-only 폴백과 동일 철학).
- 결과는 기존 경로(`EvidenceAnalysisResult` → `upsert_analysis`) 그대로 저장. `model_name`에 LoRA 버전 기록.

#### 6-6. 산출물
- `scripts/train_qwen_lora.py` (데이터 변환 → SFT/LoRA 학습 → 어댑터 저장)
- LoRA 어댑터 아티팩트 + 학습 설정 스냅샷
- `memo/results/qwen-lora-eval-v1.md` (베이스 vs LoRA 비교)
- 추론용 `QwenGenerationAnalyzer` 클래스 (프로토타입 `QwenRunner` 승격)

## 코드 변경 지점 (최소 침습)

| 변경 | 위치 | 비고 |
|---|---|---|
| DB 테이블/컬럼 | — | **불필요.** `evidence_analysis` + 저장 코드 이미 존재 |
| 재학습 분류기 교체 | `ANALYSIS_MODEL` 환경변수만 변경 | `LocalHFSentimentAnalyzer`는 그대로 |
| 저장 경로 | `evidence_analysis.py:315` `upsert_analysis` | **이미 모델 비종속.** 그대로 재사용 |
| Qwen 분석기 본체 | `LocalHFSentimentAnalyzer` 대체/병행 클래스 | ✅ 신규 작성 (Phase 6) |
| horizon/event_type/evidence 저장 | `EvidenceAnalysisResult` + `upsert_analysis`에 필드 추가 → 우선 `raw_response`(JSONB) | ✅ 무중단 → 안정화 후 alembic 컬럼 승격 |
| 규칙 엔진 | `InvestmentImpactRuleEngine` **유지** | 가드레일, 손대지 않음 |
| 생성 모델 | `analysis_generation_model` 슬롯 활용 | Phase 6에서만 |
| 라벨/검증 파이프라인 | `prototype_evidence_analysis_qwen.py` 자산 재활용 | 신규 스크립트로 승격 |

## 권장 진행 순서 (요약)

1. 라이선스 검토 + 라벨 기준표 (Phase 0)
2. 공시·뉴스 1,000건 수집 + 중복 제거 (Phase 1)
3. 허용된 teacher로 초벌 라벨 (Phase 2)
4. 사람 20%+ 검수 + 검증셋 격리 (Phase 3)
5. **KR-FinBERT + 규칙 엔진 먼저 재학습** (Phase 4)
6. 별도 검증셋으로 macro-F1·유형별 오분류율 측정 (Phase 5)
7. 설명·요약 품질 부족 시 Qwen LoRA 추가 (Phase 6)

## 리스크 / 미결 사항

- **라이선스 결론(Phase 0)이 전체 진행의 차단 게이트.** 미해결 시 데이터 수집부터 멈춘다.
- 1,000건이 event_type 다양성을 충분히 커버하는지 — 희소 유형(감자, 상장폐지 등)은 의도적 over-sampling 필요할 수 있음.
- `model_name`/`prompt_version` 컬럼 길이(150/50) 내에서 재학습 모델 버저닝 컨벤션 확정 필요.
- Qwen 생성 출력의 수치 hallucination — 프로토타입의 `verify_numerical_grounding`을 운영 가드레일로 승격할지 결정.

## 참고 문서

- Anthropic 상업용 약관 / 개인용 약관 (teacher 사용 가부)
- Qwen LoRA·Q-LoRA 학습 문서
- Qwen3 모델 카드 (Apache 2.0 체크포인트)
- OpenDART API 가이드
- 본 저장소: `evidence-analysis-distillation-plan.md`(본 문서), `scripts/prototype_evidence_analysis_qwen.py`, `run_analysis_benchmark.py`
