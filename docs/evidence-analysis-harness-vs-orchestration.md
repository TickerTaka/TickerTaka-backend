# 분석 레이어 품질 제어 전략: 현재 → 하네스 → 오케스트레이션

> 작성일: 2026-05-31  
> 대상: `app/domain/evidence_analysis.py` — `EvidenceAnalysisService.analyze_text()`

---

## 1. 현재 플로우

```text
analyze_text()
    ├── summary_builder.build()       ← 모든 텍스트에 동일 처리
    ├── sentiment_analyzer.analyze()  ← 6000자 통째로 넣음
    ├── rule_engine.apply()           ← 키워드 탐색
    └── EvidenceAnalysisResult()      ← 검증 없이 바로 저장
```

`analyze_text` 내부는 선형 파이프라인이다. 각 단계의 출력이 다음 단계로 그대로 흐르고, 저장 직전에 품질 검증이 없다.

현재 실패 감지는 Python 예외(`RuntimeError`, `OSError`)만 잡는다. **의미론적 실패(Semantic Failure) — 프로세스는 정상 종료됐지만 결과물이 쓰레기인 경우 — 는 전혀 감지하지 못한다.**

### 입력 A — 타법인취득 (문장 구조)

```
title: "타법인주식및출자증권취득결정"
text:  "기아 주식회사는 에이치엠지퓨처콤플렉스 주식회사에 대한 타법인 주식 취득을 결정했다.
        취득금액은 2조 3,634억원으로 자기자본 대비 3.9% 수준이다."
```

```json
{
  "summary":      "타법인주식및출자증권취득결정 기아 주식회사는 ... 취득금액은 2조 3,634억원으로 자기자본 대비 3.9% 수준이다.",
  "sentiment":    "mixed",
  "impact_score": 1,
  "status":       "pass"
}
```

문장 구조라 정상 동작한다.

### 입력 B — 임원보고서 (rowspan 표 구조)

```
title: "임원ㆍ주요주주특정증권등소유상황보고서"
text:  "보고구분: 성명(명칭) | 보고자 구분: 한 글 | 한자(영문) 보고구분: 성명(명칭) |
        보고자 구분: 생년월일 또는 사업자등록번호 등 | ..."
```

```json
{
  "summary":      "임원ㆍ주요주주특정증권등소유상황보고서 보고구분: 성명(명칭) | 보고자 구분: 한 글 | 한자(영문) 보고구분: 성명(명칭) | 보고자 구분: 생년월일 ...",
  "sentiment":    "neutral",
  "impact_score": 0,
  "status":       "pass"
}
```

표 헤더 텍스트 전체가 요약에 들어간다. 파이썬 예외가 없으므로 `status: pass`로 DB에 저장된다.  
토론 에이전트에게 이 요약이 전달된다.

---

## 2. 하네스 적용

### 무엇이 바뀌는가

플로우 자체는 동일하다. DB 저장 직전에 가드레일 함수 하나만 추가된다.

```text
analyze_text()
    ├── summary_builder.build()
    ├── sentiment_analyzer.analyze()
    ├── rule_engine.apply()
    ├── _validate_analysis_result()   ← 추가된 부분
    │       ├── PASS  → 저장
    │       └── FAIL  → fallback 보정 후 저장 (failure_reason 기록)
    └── EvidenceAnalysisResult()
```

```python
class HarnessValidationError(Exception):
    pass

def _validate_analysis_result(summary: str, sentiment: str, impact_score: int, title: str) -> None:
    # 표 헤더 노이즈: 파이프 4개 이상이고 300자 초과
    if len(summary) > 300 and summary.count("|") >= 4:
        raise HarnessValidationError("err_table_header_noise")

    # 빈 깡통: 제목과 동일하거나 비어 있음
    if not summary.strip() or summary.strip() == title.strip():
        raise HarnessValidationError("err_empty_summary")

    # 논리 모순: sentiment와 impact_score가 반대 방향
    if sentiment == "positive" and impact_score < 0:
        raise HarnessValidationError("err_sentiment_impact_mismatch")
    if sentiment == "negative" and impact_score > 0:
        raise HarnessValidationError("err_sentiment_impact_mismatch")
```

### 입력 A — 동일

```json
{
  "summary":      "타법인주식및출자증권취득결정 기아 주식회사는 ... 2조 3,634억원 ...",
  "sentiment":    "mixed",
  "impact_score": 1,
  "status":       "pass"
}
```

가드레일 조건 미해당. 동일하게 통과한다.

### 입력 B — 달라짐

```
_validate_analysis_result() 호출
    → len(summary) = 682 > 300
    → summary.count("|") = 21 >= 4
    → HarnessValidationError("err_table_header_noise") raise
```

```json
{
  "summary":        "임원ㆍ주요주주특정증권등소유상황보고서",
  "sentiment":      "neutral",
  "impact_score":   0,
  "status":         "fallback",
  "failure_reason": "err_table_header_noise"
}
```

쓰레기 요약이 DB에 들어가지 않는다. 그리고 `failure_reason`이 기록되어 이후 집계가 가능하다.

```sql
-- 일주일간 failure_reason 패턴 조회
SELECT
    raw_response->>'failure_reason' AS reason,
    count(*)                        AS cnt
FROM evidence_analysis
WHERE raw_response->>'status' = 'fallback'
  AND analyzed_at > now() - interval '7 days'
GROUP BY reason
ORDER BY cnt DESC;
```

```
reason                       | cnt
-----------------------------|-----
err_table_header_noise       |  47
err_empty_summary            |   8
err_sentiment_impact_mismatch|   3
```

이 집계가 "임원보고서에서 표 헤더 노이즈가 반복된다"는 패턴을 드러낸다.  
패턴이 보여야 개선 방향이 보이고, 개선 후 골든 테스트셋으로 회귀를 막는다.

---

## 3. 오케스트레이션 적용

### 무엇이 바뀌는가

`analyze_text` 내부를 3단계로 분리한다. 각 단계가 좁고 단순한 출력을 만들어 다음 단계에 넘긴다.

```text
analyze_text()
    ├── Step 1: extract_facts(title, text)
    │           → 수치 / 키워드 / 주체를 구조화된 dict로 추출
    │           → 검증: facts가 비어 있는가? (빈 경우 행정성 공시로 분류)
    │
    ├── Step 2: classify(title, facts)
    │           → facts 기반으로 sentiment / impact 판단
    │           → 검증: valid sentiment set 안에 있는가?
    │
    ├── Step 3: build_summary(title, facts)
    │           → facts에서만 문장 조합 (원문 텍스트 직접 접근 안 함)
    │           → 검증: facts에 없는 수치가 들어갔는가?
    │
    └── EvidenceAnalysisResult()
```

### 입력 A — 개선됨

```python
# Step 1: facts 추출
facts = {
    "금액":  "2조 3,634억원",
    "비율":  "3.9%",
    "주체":  "기아",
    "대상":  "에이치엠지퓨처콤플렉스",
    "목적":  "미래 모빌리티 연구개발 거점 확보",
}

# Step 2: facts 기반 분류
# "취득금액" 키 → positive 요인
# 대규모 지출 → negative 요인
# → mixed, impact +1

# Step 3: facts에서만 요약 조합
summary = "타법인주식취득결정. 기아가 에이치엠지퓨처콤플렉스를 2조 3,634억원(자기자본 대비 3.9%)에 취득 결정."
```

```json
{
  "summary":      "타법인주식취득결정. 기아가 에이치엠지퓨처콤플렉스를 2조 3,634억원(자기자본 대비 3.9%)에 취득 결정.",
  "sentiment":    "mixed",
  "impact_score": 1,
  "status":       "pass"
}
```

facts에서만 요약을 조합하므로 원문 노이즈가 끼어들 경로가 없다.

### 입력 B — 구조적으로 막힘

```python
# Step 1: facts 추출
# 파이프 구분 텍스트 → 수치/금액/주체 없음
facts = {}

# Step 2: facts 없음 → 제목으로 분류
# NEUTRAL_ADMIN_TITLE_KEYWORDS 해당 → neutral, 0

# Step 3: facts 없음 → 제목만
summary = "임원ㆍ주요주주특정증권등소유상황보고서"
```

```json
{
  "summary":      "임원ㆍ주요주주특정증권등소유상황보고서",
  "sentiment":    "neutral",
  "impact_score": 0,
  "status":       "pass"
}
```

표 헤더 텍스트가 Step 1에서 facts로 추출되지 않으므로, 이후 단계에 아예 도달하지 않는다.  
하네스처럼 사후에 잡는 게 아니라 구조적으로 들어갈 경로가 없다.

---

## 4. 결과 비교

### 출력 비교

| | 입력 A (문장 구조) | 입력 B (표 헤더 구조) |
|---|---|---|
| **현재** | 정상 ✓ | 쓰레기 `status: pass` ✗ |
| **하네스** | 동일 ✓ | 쓰레기 차단 + `failure_reason` 기록 ✓ |
| **오케스트레이션** | 요약 품질 개선 ✓ | 구조적으로 차단 ✓ |

### 역할 비교

| | 하네스 | 오케스트레이션 |
|---|---|---|
| 접근 방식 | 사후(post-hoc) — 나온 결과를 검증 | 사전(pre-hoc) — 경로 자체를 설계 |
| 실패 감지 | 가드레일로 명시적 감지 | 단계 분리로 실패 경로 차단 |
| 실패 기록 | `failure_reason`으로 DB에 남음 | 각 단계 실패로 격리 |
| 피드백 루프 | 있음 — 기록 → 분석 → 개선 | 없음 — 설계로만 해결 |
| 구현 복잡도 | 낮음 (가드레일 함수 1개) | 높음 (3단계 분리 + 인터페이스 설계) |
| 기존 코드 변경 | 최소 | 전면 재구성 |

### 무엇을 해결하고 무엇을 못 하는가

| 문제 | 하네스 | 오케스트레이션 |
|---|---|---|
| 표 헤더 노이즈가 요약에 들어감 | fallback으로 차단 ✓ | Step 1에서 애초에 차단 ✓ |
| 쓰레기 데이터가 DB에 저장됨 | 가드레일이 막음 ✓ | 단계 분리로 막음 ✓ |
| 어디서 왜 실패하는지 패턴 파악 | `failure_reason` 집계로 가능 ✓ | 불가 (패턴 기록 없음) ✗ |
| 규칙/모델 개선 후 회귀 방지 | 골든셋 + 피드백 루프 ✓ | 불가 (루프 없음) ✗ |
| 요약 품질 자체 개선 | 하지 않음 (차단만) | facts 기반으로 개선 ✓ |

---

## 5. 권장 순서

```text
지금 (1~2일)
    └── 하네스: _validate_analysis_result() 추가
        ├── 가드레일 3개 (표 헤더 노이즈 / 빈 요약 / 논리 모순)
        └── failure_reason이 DB에 쌓이기 시작

1~2주 후
    └── failure_reason 집계로 실제 실패 패턴 확인
        └── "어느 단계에서 무엇이 자주 실패하는가?" 데이터로 파악

데이터 기반으로
    └── 오케스트레이션: 실패가 집중된 단계를 분리
        └── 추측이 아닌 관측된 패턴 기반으로 설계
```

**하네스 없이 오케스트레이션을 먼저 하면**, 어디를 쪼개야 하는지 데이터가 없어 추측으로 설계하게 된다.  
**하네스가 먼저 실패 지점을 찾아줘야 오케스트레이션 설계 근거가 생긴다.**

---

## 6. 한 줄 요약

**하네스는 "무엇이 실패하는가"를 보는 눈이고, 오케스트레이션은 "실패 경로를 없애는 구조"다. 눈이 먼저다.**
