# DART 표 직렬화 품질 문제 분석 및 해결 방안

> 작성일: 2026-05-30  
> 대상 파일: `app/external/dart/client.py` — `_expand_table_to_grid`, `_serialize_grid`

---

## 1. 현재 흐름

```text
DART OpenAPI → document.xml ZIP 다운로드
    ↓
extract_document_text_v2()
    ├── HTML/XML 파싱
    ├── <table> 태그 → serialize_table_full()
    │       ├── _expand_table_to_grid()   ← rowspan/colspan 2D 펼침
    │       └── _serialize_grid()         ← 2D grid → 텍스트 행
    └── 섹션별 텍스트 결합
    ↓
build_filing_chunks() → Chroma 인덱싱
    ↓
EvidenceAnalysisService → 분석 입력 텍스트
```

---

## 2. 정상 동작 케이스

`_serialize_grid`는 **"첫 행 = 헤더, 나머지 행 = 데이터"** 가정으로 동작한다.

일반 재무표(실적공시 등)는 이 가정이 맞다.

HTML 원본:

```html
<table>
  <tr><th>구분</th><th>당기</th><th>전기</th></tr>
  <tr><td>매출액</td><td>267,627억</td><td>302,231억</td></tr>
  <tr><td>영업이익</td><td>6,567억</td><td>43,376억</td></tr>
</table>
```

`_expand_table_to_grid` 결과:

```
grid[0] = ["구분",   "당기",     "전기"]
grid[1] = ["매출액",  "267,627억", "302,231억"]
grid[2] = ["영업이익", "6,567억",  "43,376억"]
```

`_serialize_grid` 출력:

```text
구분: 매출액 | 당기: 267,627억 | 전기: 302,231억
구분: 영업이익 | 당기: 6,567억 | 전기: 43,376억
```

---

## 3. 문제 케이스

rowspan을 레이블 컬럼으로 사용하는 표에서 가정이 깨진다.

대표 공시 유형:

- `임원ㆍ주요주주특정증권등소유상황보고서`
- `횡령/배임 관련 조회공시` — 피해금액/발생경위/처리현황을 행 레이블로 나열
- `주요사항보고서(소송등의 판결)` — 소송 내용을 항목별 레이블 표로 기술
- rowspan으로 "구분" 컬럼을 만드는 모든 표

### 3-1. HTML 원본 구조 (임원보고서 예시)

```html
<table>
  <tr>
    <td rowspan="3">보고구분</td>   <!-- 3행에 걸쳐 반복 -->
    <td>성명(명칭)</td>
    <td rowspan="3">보고자 구분</td>  <!-- 3행에 걸쳐 반복 -->
    <td>홍길동</td>
    <td rowspan="3">발행회사</td>    <!-- 3행에 걸쳐 반복 -->
    <td>삼성전자</td>
  </tr>
  <tr>
    <td>생년월일</td>
    <td>1990-01-01</td>
    <td>삼성전자</td>
  </tr>
  <tr>
    <td>주소</td>
    <td>서울시 강남구</td>
    <td>삼성전자</td>
  </tr>
</table>
```

### 3-2. `_expand_table_to_grid` 결과

rowspan 값을 모든 행에 복사한다.

```
grid[0] = ["보고구분", "성명(명칭)", "보고자 구분", "홍길동",      "발행회사", "삼성전자"]
grid[1] = ["보고구분", "생년월일",   "보고자 구분", "1990-01-01", "발행회사", "삼성전자"]
grid[2] = ["보고구분", "주소",       "보고자 구분", "서울시 강남구","발행회사", "삼성전자"]
```

`grid[0]`에 헤더(`보고구분`, `보고자 구분`, `발행회사`)와 값(`홍길동`, `삼성전자`)이 **이미 섞여 있다.**

### 3-3. `_serialize_grid` 출력 (현재)

`grid[0]`을 헤더로 쓰고 `grid[1]`부터 데이터로 처리한다.

```text
보고구분: 보고구분 | 성명(명칭): 생년월일 | 보고자 구분: 보고자 구분 | 홍길동: 1990-01-01 | 발행회사: 발행회사 | 삼성전자: 서울시 강남구
```

헤더가 값으로, 값이 헤더로 잘못 배치된다.

---

### 3-4. 실제 테스트 결과

**테스트 조건**

- 종목: 삼성전자 `005930`
- 접수번호: `20260529001627`
- 공시명: `임원ㆍ주요주주특정증권등소유상황보고서`
- provider: `extractive`

**실행 명령**

```bash
PYTHONPATH=. .venv/bin/python scripts/prototype_evidence_analysis_qwen.py \
  --provider extractive \
  --output simple \
  --symbol 005930 \
  --title '임원ㆍ주요주주특정증권등소유상황보고서' \
  --dart-receipt-no 20260529001627
```

**실제 출력**

```text
[분석 1]
요약: 임원ㆍ주요주주특정증권등소유상황보고서 보고자 : | 김경석 [회 사 명] 법인구분: 발행주식 총수
보고구분: 성명(명칭) | 보고자 구분: 한 글 | 한자(영문) 보고구분: 성명(명칭) | 보고자 구분: 생년월일
또는 사업자등록번호 등 | 생년월일 또는 사업자등록번호 등 보고구분: 주소(본점소재지)[읍ㆍ면ㆍ동까지만
기재] 보고구분: 발행회사와의 관계 | 보고자 구분: 임원(등기여부) | 직위명 보고구분: 발행회사와의
관계 | 보고자 구분: 선임일 | 퇴임일 보고구분: 발행회사와의 관계 | 보고자 구분: 주요주주 보고구분:
업무상 연락처및 담당자 | 보고자 구분: 소속회사 | 부 서 보고구분: 업무상 연락처및 담당자 | 보고자
구분: 직 위 | 전화번호 보고구분: 업무상 연락처및 담당자 | 보고자 구분: 성 명 | 팩스번호 보고구분:
업무상 연락처및 담당자 | 보고자 구분: 이메일 주소 보고서작성 기준일: 보고서작성 기준일 | 특정증권등:
특정증권등의수(주) | 특정증권등: 비율(%) | 주권: 주식수(주) | 주권: 비율(%) 직전보고서 이번보고서
증 감 | 보고서작성 기준일: 증 감 특정증권등의 내역: 주 권 | 특정증권등의 내역: 신주인수권이표시된것
| 특정증권등의 내역: 전환사채권 | 특정증권등의 내역: 신주인수권부사채권 | 특정증권등의 내역:
이익참가부사채권 | 특정증권등의 내역: 교환사채권 | 특정증권등의 내역: 증권예탁증권
긍부정: neutral
영향도: +0
하네스: pass
```

**무엇이 문제인가**

- `긍부정: neutral`, `영향도: +0`, `하네스: pass` — 분류와 하네스 자체는 정상이다.
- 하지만 `요약` 필드가 표 헤더 텍스트 전체를 하나의 긴 문자열로 담고 있다.
- `보고구분: 성명(명칭) | 보고자 구분: 한 글` — 헤더가 값처럼, 값이 헤더처럼 뒤섞였다.
- `보고서작성 기준일: 보고서작성 기준일` — 헤더와 값이 동일한 오탐이 그대로 노출된다.
- 이 요약이 토론 에이전트에게 전달되면 의미 있는 정보가 없다.

**이상적인 출력 (기대값)**

```text
[분석 1]
요약: 임원ㆍ주요주주특정증권등소유상황보고서. 보고자: 김경석 | 발행회사: 삼성전자 | 소유주식수 증감: -1,000주
긍부정: neutral
영향도: +0
하네스: pass
```

또는 표 내용을 의미 있게 뽑아낼 수 없는 경우:

```text
[분석 1]
요약: 임원ㆍ주요주주특정증권등소유상황보고서
긍부정: neutral
영향도: +0
하네스: pass
```

---

### 3-5. 재무표 비교 — 정상 동작하는 경우

동일한 방식으로 실적공시나 타법인취득 공시를 분석하면 문제가 없다.

```bash
PYTHONPATH=. .venv/bin/python scripts/prototype_evidence_analysis_qwen.py \
  --provider extractive \
  --output simple \
  --source-type filing \
  --symbol 000270 \
  --title '타법인주식및출자증권취득결정' \
  --text '기아 주식회사는 에이치엠지퓨처콤플렉스 주식회사에 대한 타법인 주식 취득을 결정했다. 취득금액은 2조 3,634억원으로 자기자본 대비 3.9% 수준이다.'
```

```text
[분석 1]
요약: 타법인주식및출자증권취득결정 기아 주식회사는 에이치엠지퓨처콤플렉스 주식회사에 대한 타법인
주식 취득을 결정했다. 취득금액은 2조 3,634억원으로 자기자본 대비 3.9% 수준이다.
긍부정: mixed
영향도: +1
하네스: pass
```

이 경우는 문장 구조라 `_split_sentences`가 정상 작동하고, 수치도 요약에 보존된다.

**차이 요약**

| 공시 유형 | 본문 구조 | 요약 품질 |
|---|---|---|
| 타법인취득, 유상증자, 실적공시 | 문장 구조 | 정상 — 수치/핵심 내용 보존 |
| 임원보고서, 횡령조회, 소송판결 | rowspan 표 구조 | 문제 — 표 헤더 텍스트 전체 노출 |

---

### 3-6. 왜 분석 레이어에서 고칠 수 없는가

`EvidenceAnalysisService`의 `_split_sentences`는 `.!?` 기준으로 문장을 자른다.
표 텍스트는 구두점 없이 `|`로 구분되어 통째로 하나의 긴 "문장"으로 인식된다.

분석 레이어에서 `|` 기준 추가 분리나 패턴 필터를 붙여도:

- 공시 유형마다 표 구조가 달라서 패치가 계속 필요하다
- 이미 망가진 `헤더: 값` 매핑은 복원할 수 없다
- 근본 원인은 파서에 있으므로 분석 레이어 수정은 임시방편이다

---

## 4. 해결 방안

### 옵션 1 — span 메타데이터 보존 (근본 해결)

**수정 위치**: `_expand_table_to_grid`, `_serialize_grid`

`grid` 셀을 `str`에서 `(text, is_span_copy: bool)` 튜플로 변경한다.
rowspan/colspan으로 복사된 셀은 `is_span_copy=True`로 표시한다.

```python
# _expand_table_to_grid
for r in range(rowspan):
    for c in range(colspan):
        is_copy = not (r == 0 and c == 0)
        grid[(row_idx + r, col_idx + c)] = (text, is_copy)
```

`_serialize_grid`에서 `is_span_copy=True` 셀은 레이블(prefix), `False` 셀은 값으로 분리한다.

**임원보고서 예시 결과**:

```text
[보고구분] [보고자 구분] [발행회사]
성명(명칭): 홍길동
생년월일: 1990-01-01
주소: 서울시 강남구
```

**장점**: 구조적으로 정확한 해결, 모든 rowspan 표에 일반 적용  
**단점**: `_expand_table_to_grid`와 `_serialize_grid` 인터페이스 모두 변경 필요

---

### 옵션 2 — 반복값 컬럼을 레이블 컬럼으로 감지

**수정 위치**: `_serialize_grid`

grid를 분석해서 모든 행에 걸쳐 동일한 값이 반복되는 컬럼을 rowspan 레이블로 간주한다.

```python
label_cols = {
    col_idx
    for col_idx in range(num_cols)
    if len({grid[r][col_idx] for r in range(num_rows)}) == 1
}
```

레이블 컬럼은 prefix로 묶고, 나머지 컬럼만 `헤더: 값` 직렬화한다.

**장점**: `_expand_table_to_grid` 수정 없이 적용 가능  
**단점**: 실제 모든 행이 동일한 값인 데이터 컬럼(예: 전체 동일 종목코드)을 오탐할 수 있음

---

### 옵션 3 — 직렬화 후 의미 없는 쌍 제거 (포스트 프로세싱)

**수정 위치**: `_serialize_grid`

직렬화 결과에서 `헤더 == 값`인 쌍을 제거한다.

```python
parts = []
for pair in row_pairs:
    header, value = pair.split(":", 1)
    if header.strip() != value.strip():
        parts.append(pair)
```

**장점**: 최소 수정, 빠른 적용  
**단점**: 이미 잘못 배치된 헤더/값 관계를 복원하지 못함. 완전한 해결 아님

---

### 옵션 4 — 표 구조 유형 분류 후 직렬화 방식 분기

**수정 위치**: `_serialize_grid`

표를 직렬화 전에 구조 유형으로 분류한다.

| 유형 | 판단 기준 | 처리 방식 |
|---|---|---|
| Type A (표준 재무표) | 첫 행에 숫자/날짜 없음, 반복 없음 | 현재 방식 유지 |
| Type B (레이블 컬럼 표) | 특정 컬럼 값이 전 행에 반복 | 옵션 2 방식 |
| Type C (단일행/단순표) | 행이 1개 이하 | 셀 나열 |

**장점**: Type A 기존 로직 보존, 타입별 최적화 가능  
**단점**: 중간 케이스 분류가 애매할 수 있음

---

### 옵션 5 — 분석 입력을 Chroma 청크로 대체

**수정 위치**: `EvidenceAnalysisService.analyze_filing_row()`

파서를 고치지 않고, 분석 텍스트를 `extract_document_text_v2` 전체 대신 **이미 RAG용으로 정제된 Chroma 청크**에서 가져온다.

```text
현재: filing 전체 텍스트(파서 원본) → 분석
변경: 해당 filing의 Chroma 상위 청크 N개 → 분석
```

Chroma 청크는 섹션 단위로 잘려 있고 임베딩으로 관련도 높은 부분이 선택되므로 표 노이즈가 줄어든다.
계획서(`evidence-llm-analysis-implementation-plan.md` 9-2절)에도 "추후 Chroma 상위 청크 활용" 방향이 언급되어 있다.

**장점**: 파서 수정 없이 분석 품질 개선, 인덱싱 단계와 분석 단계 분리 유지  
**단점**: 분석 시점에 Chroma 인덱싱이 완료된 상태여야 함 (순서 의존)

---

## 5. 방안 비교

| 옵션 | 수정 위치 | 해결 범위 | 난이도 | 권장 시점 |
|---|---|---|---|---|
| 1. span 메타데이터 보존 | `_expand_table_to_grid` + `_serialize_grid` | 근본 해결 | 높음 | 중장기 |
| 2. 반복값 컬럼 감지 | `_serialize_grid` | 대부분 해결 | 중간 | 단기 |
| 3. 포스트 프로세싱 필터 | `_serialize_grid` | 부분 개선 | 낮음 | 임시 |
| 4. 표 유형 분류 | `_serialize_grid` | 대부분 해결 | 중간 | 단기 |
| 5. Chroma 청크 재활용 | `EvidenceAnalysisService` | 우회 해결 | 낮음 | 단기 |

---

## 6. 권장 로드맵

```text
단기 (MVP 안정화)
    └── 옵션 3 + 5 조합
        ├── 포스트 프로세싱으로 명백한 오탐 제거
        └── Chroma 청크 기반 분석 입력으로 노이즈 감소

중기 (파서 개선)
    └── 옵션 2 또는 4
        └── _serialize_grid에서 반복값 컬럼 감지

장기 (근본 해결)
    └── 옵션 1
        └── _expand_table_to_grid span 메타데이터 보존
```

단기에는 분석 품질에 영향이 큰 공시(실적공시, 유상증자, 타법인취득 등)는 이미 문장 구조라 잘 동작한다.
표 구조가 복잡한 공시(임원보고서 등)는 `NEUTRAL_ADMIN_TITLE_KEYWORDS` 보정으로 `neutral/0`이 되므로 잘못된 요약이 토론 품질에 미치는 영향이 제한적이다.

---

## 7. 실제 적용된 수정 사항

> 에스앤에스텍(101490) 잠정실적 공시(`20260506900335`) 테스트를 통해 발견된 문제를 해결하면서 순차적으로 적용됨.

---

### 수정 1 — `_serialize_grid`: 헤더==값 중복 쌍 제거

**파일**: `app/external/dart/client.py`

**문제**: 2단 헤더 표에서 `_serialize_grid`가 grid[0]을 헤더로 쓰면, 그 다음 행(실제로는 서브헤더)의 값이 헤더와 같아서 `보고사유: 보고사유`, `변동일*: 변동일*` 같은 의미 없는 쌍이 출력됨.

**변경 전**:
```python
parts.append(f"{header}: {value}" if header else value)
```

**변경 후**:
```python
if header and header == value:
    continue
parts.append(f"{header}: {value}" if header else value)
```

**효과**: 임원보고서 변동 내역 테이블에서 `보고사유: 보고사유`, `변동일*: 변동일*` 제거.

---

### 수정 2 — `_serialize_grid`: 모든 셀이 동일한 rowspan 행 스킵

**파일**: `app/external/dart/client.py`

**문제**: `※ 동 정보는 확정치가 아닌 잠정치로서 향후 확정치와는 다를 수 있음.` 같은 텍스트가 rowspan으로 모든 셀에 복사된 행이 headers로 쓰여서 `※ 동 정보는...: 매출액`, `※ 동 정보는...: 61,692` 같은 출력이 나옴. 실제 실적 수치가 있어도 요약에 반영 불가.

**근본 원인**: `_expand_table_to_grid`가 rowspan 값을 모든 행에 텍스트 복사. 9개 열 전체가 동일한 rowspan 값으로 채워진 행이 grid[0]으로 들어옴. `_serialize_grid`는 이를 헤더로 사용.

**변경 전**: rowspan 전용 행도 그대로 헤더로 사용.

**변경 후**:
```python
# 모든 셀이 동일한 값인 행은 rowspan 전용 헤더 행 → 스킵
while remaining and len({c.strip() for c in remaining[0] if c.strip()}) <= 1:
    remaining = remaining[1:]
if not remaining:
    return prefix.strip()
```

**효과**: 에스앤에스텍 잠정실적 공시에서 행0(`※ 동 정보는...` × 9) 및 행1(`1. 연결실적내용` × 9)이 스킵되고, 행2(`구분`, `당기실적`, `전기실적`)가 실제 헤더로 사용됨. 행4 이후 실제 수치 행 직렬화 가능:

```
구분(단위 : 백만원, %): 매출액 | 당기실적: 61,692 | 전기실적: 63,609 | 전기대비: -3.0
구분(단위 : 백만원, %): 영업이익 | 당기실적: 11,376 | 전기실적: 13,309 | 전기대비: -14.5
```

---

### 수정 3 — `ExtractiveSummaryBuilder._split_sentences`: 줄바꿈 기준 분리 추가

**파일**: `app/domain/evidence_analysis.py`

**문제**: 기존 구현이 `re.sub(r"\s+", " ", text)`로 모든 `\n`을 공백으로 치환해서 테이블 직렬화 결과 전체가 하나의 긴 "문장"이 됨. 60~200자짜리 테이블 행들이 합쳐져 수천 자 단일 문자열 → `_find_numeric_sentence`가 개별 행의 수치 추출 불가. 하네스도 오탐.

**변경 전**:
```python
normalized = re.sub(r"\s+", " ", text).strip()  # \n도 공백으로 → 전체가 1문장
sentences = re.split(r"(?<=[.!?。！？])\s+", normalized)
```

**변경 후**:
```python
for line in text.splitlines():  # 줄바꿈으로 먼저 분리
    line = re.sub(r"\s+", " ", line).strip()
    for part in re.split(r"(?<=[.!?。！？])\s+", line):  # 각 줄 안에서 추가 분리
        if len(part.strip()) >= 8:
            result.append(part.strip())
```

**효과**: 테이블 행 각각이 별도 문장으로 처리됨. 67개 문장으로 분리된 잠정실적 공시에서 `매출액`, `영업이익` 포함 행 개별 접근 가능.

---

### 수정 4 — `ExtractiveSummaryBuilder._find_numeric_sentence`: 금융 테이블 패턴 추가

**파일**: `app/domain/evidence_analysis.py`

**문제**: 잠정실적 공시의 수치가 `61,692`, `63,609` 형태 — 단위(`원`, `%`, `주`) 없이 쉼표 구분 숫자만 있어서 기존 패턴에 걸리지 않음.

기존 패턴:
```python
r"\d+(?:,\d{3})*(?:\.\d+)?\s*(?:조|억|만|천)?\s*(?:원|%)"  # 원/% 단위 필수
```

**변경 후**: 패턴 추가
```python
# 매출액/영업이익 키워드 + 쉼표 구분 숫자 (백만원 단위 잠정실적 등)
r"(?:매출액|영업이익|당기순이익|순이익).*\d{3,}(?:,\d{3})+"
```

**효과**: `구분(단위 : 백만원, %): 매출액 | 당기실적: 61,692 | 전기실적: 63,609 | 전기대비: -3.0` 행을 numeric_sentence로 선택. key_points에 실제 수치 포함.

---

### 수정 5 — 하네스 가드레일: 파이프 개수 → 파이프 밀도로 변경

**파일**: `app/domain/evidence_analysis.py`

**문제**: 기존 조건 `len(summary) > 300 and summary.count("|") >= 4`가 잘 직렬화된 테이블 행도 오탐. 테이블 행 하나에 파이프가 4~7개이므로 유의미한 수치가 포함된 요약도 `err_table_header_noise`로 fallback됨.

**변경 전**:
```python
if len(normalized_summary) > 300 and normalized_summary.count("|") >= 4:
```

**변경 후**:
```python
pipe_density = normalized_summary.count("|") / max(len(normalized_summary), 1) * 100
if len(normalized_summary) > 300 and pipe_density > 3.0:
```

**판단 기준**:
- 좋은 테이블 행: `구분: 매출액 | 당기실적: 61,692 | 전기실적: 63,609` → 80자에 파이프 2개 → 밀도 2.5% → 통과
- 나쁜 노이즈: `보고구분: 성명(명칭) | 보고자 구분: 한 글 | ... | 보고자 구분: 이메일 주소` → 200자에 파이프 20개 → 밀도 10% → 차단

---

### 수정 결과 검증

에스앤에스텍 잠정실적 공시:

| | 수정 전 | 수정 후 |
|---|---|---|
| 하네스 | fallback (`err_table_header_noise`) | pass |
| 요약 | 제목만 | 제목 + 헤더 행 |
| 핵심 근거 | 없음 | `매출액 당기 61,692 | 전기 63,609 | 전기대비 -3.0` 포함 |
| 감성 | neutral (fallback) | negative -1 (영업이익 -14.5% 반영) |

벤치마크: 기존 3/3 케이스 회귀 없음.
