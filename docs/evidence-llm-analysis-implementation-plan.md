# 뉴스/공시 로컬 분석 레이어 구현 계획서

> 작성일: 2026-05-28  
> 대상 범위: 뉴스/공시 RAG 검색 결과에 대해 규칙 기반 요약, 로컬 감성 분류, 영향도, 리스크를 분석하여 토론 에이전트 컨텍스트에 반영

---

## 1. 왜 필요한가

현재 RAG 구조는 뉴스와 공시 본문을 Chroma에 임베딩해두고, 토론 시작 시 관련 문서를 검색해서 bull/bear/moderator 에이전트에게 전달한다.

현재 흐름:

```text
news_cache / filing_cache
    ↓
Chroma news / filing 컬렉션
    ↓
EvidenceRetrievalService.search_symbol_evidence()
    ↓
data_agent_node
    ↓
bull / bear / moderator 토론 에이전트
```

이 구조는 "관련 자료를 찾는 것"까지는 해결하지만, 아래 해석은 아직 토론 에이전트가 매번 직접 해야 한다.

- 이 뉴스가 해당 종목에 긍정인지 부정인지
- 공시가 단순 행정 공시인지, 실질적 주가 영향이 있는지
- 핵심 근거가 무엇인지
- 리스크 요인이 무엇인지
- 짧은 요약으로 토론 컨텍스트에 넣을 수 있는지

문제는 토론 시점마다 에이전트가 긴 본문을 다시 해석하면 비용과 시간이 늘고, 같은 자료에 대한 판단이 매번 조금씩 달라질 수 있다는 점이다.

따라서 수집/인덱싱 시점에 LLM 분석을 한 번 수행하고, 그 결과를 DB에 저장한 뒤 토론 RAG에서 함께 전달하는 구조가 필요하다.

---

## 2. 목표

### 2-1. 기능 목표

뉴스와 공시에 대해 로컬 분석 엔진이 아래 정보를 생성한다.

| 항목 | 설명 |
|---|---|
| `summary` | 규칙 기반 추출식 요약. 제목 + 첫 핵심 문장 + 금액/비율 포함 문장 중심 |
| `sentiment` | `positive`, `negative`, `neutral`, `mixed` |
| `impact_score` | 종목 투자 판단 영향도, `-2` ~ `+2` |
| `confidence` | 판단 신뢰도, `0.0` ~ `1.0` |
| `key_points` | 긍정/부정 판단의 핵심 근거 문장/키워드 |
| `risks` | 주의해야 할 리스크 문장/키워드 |
| `model_name` | 분석에 사용한 분류 모델 또는 rule-only 엔진명 |
| `prompt_version` | 분석 로직 버전 |

### 2-2. 토론 품질 목표

변경 전:

```text
[뉴스/공시 근거]
- 기아 타법인주식및출자증권취득결정 ...
```

변경 후:

```text
[뉴스/공시 분석]
- [공시][mixed][영향도 +1] 타법인주식취득결정. 기아 주식회사는 에이치엠지퓨처콤플렉스 주식회사에 대한 타법인 주식 취득을 결정했다. 취득금액은 2조 3,634억원으로 자기자본 대비 3.9% 규모다.
  핵심 근거: 취득금액 2조 3,634억원, 자기자본 대비 3.9%
  리스크: 대규모 현금 유출, 투자 회수 불확실성
```

---

## 3. 권장 아키텍처

### 3-1. 최종 구조

```text
뉴스/공시 수집
    ↓
news_cache / filing_cache 저장
    ↓
Chroma 인덱싱
    ↓
EvidenceAnalysisService
    ├── 규칙 기반 추출식 요약
    ├── 긍정/부정 판단
    ├── 영향도 점수화
    └── 리스크 추출
    ↓
evidence_analysis 테이블 저장
    ↓
EvidenceRetrievalService 검색 시 분석 결과 join
    ↓
data_agent_node에서 토론 컨텍스트 구성
    ↓
bull / bear / moderator
```

### 3-2. 실행 방식: 규칙 기반 + 로컬 HuggingFace 분류 모델 우선

이 기능은 초기에 OpenRouter 같은 외부 생성형 LLM을 붙이지 않고, HuggingFace에서 받을 수 있는 작은 로컬 분류 모델과 규칙 기반 요약으로 시작한다.

권장 방향:

```text
초기 MVP
    ├── 요약: 규칙 기반 extractive summary
    │       └── 제목 + 첫 문장 + 금액/비율/증감 문장
    ├── 긍정/부정: 로컬 HuggingFace 금융 감성 분류 모델
    ├── 영향도: sentiment + 키워드 규칙 조합
    └── key_points/risks: 키워드/문장 추출 기반

추후 고도화
    └── 필요 시 Qwen 3B/4B GGUF로 summary/key_points/risks 품질 개선
```

이유:

- 로컬 모델은 서버 비용이 낮고 API 키 없이 동작한다.
- 뉴스/공시 분석은 매번 긴 자연어 생성을 할 필요가 없다.
- sentiment/impact는 생성형 LLM보다 분류 모델 + 규칙이 더 예측 가능하다.
- 금융 공시는 환각 없는 수치/사실 전달이 중요하므로, 추출식 요약이 초기 production에 더 안전하다.
- 토론 에이전트가 이미 생성형 역할을 하므로, 수집 파이프라인에서는 가볍고 안정적인 전처리 분석이 적합하다.

추천 모델 계열:

| 용도 | 모델 후보 | 설명 |
|---|---|---|
| 한국어 금융 감성 분류 | `snunlp/KR-FinBert-SC` | 한국어 금융 뉴스 특화 감성 분류. 1순위 후보 |
| 한국어 감성 분류 | `tabularisai/multilingual-sentiment-analysis` | 다국어 감성 분류. KR-FinBert-SC 실패 시 보조 후보 |
| 한국어 감성 분류 | `nlptown/bert-base-multilingual-uncased-sentiment` | 별점 기반 다국어 감성. 최후 보조 후보 |
| 한국어 임베딩 | `jhgan/ko-sroberta-multitask` | 이미 Chroma 임베딩에 사용 중 |
| 로컬 생성 요약 실험 | `Qwen2.5-3B-Instruct-GGUF` | 16GB M2 Pro에서 우선 검증할 생성형 요약 후보 |
| 로컬 생성 요약 실험 | `Qwen3-4B-GGUF` | 품질 개선이 필요할 때 검증할 상위 후보 |

주의:

- 일반 감성 모델의 `positive/negative`는 "문장 분위기" 기준이다.
- 투자 관점의 긍정/부정과 완전히 같지 않다.
- 따라서 최종 `impact_score`는 모델 결과만 쓰지 말고 공시/뉴스 키워드 규칙을 같이 반영한다.
- KoBART 계열 요약 모델은 금융 공시/뉴스 샘플에서 반복, 접합 오류, 의미 손상이 확인되어 production 후보에서 제외한다.
- `digit82/kobart-summarization` 등 KoBART 변형은 별도 벤치마크 전까지 보류한다.

예:

```text
모델 sentiment = positive
본문에 "유상증자", "소송", "관리종목", "적자전환" 포함
    → mixed 또는 negative로 보정

모델 sentiment = neutral
본문에 "자사주 취득", "배당 확대", "수주", "영업이익 증가" 포함
    → positive로 보정
```

### 3-3. 왜 저장형 분석인가

토론 실행 중 실시간으로 분석을 하면 아래 문제가 있다.

- 토론 시작 시간이 길어진다.
- 같은 뉴스/공시에 대해 매번 다른 분석이 나올 수 있다.
- 사용자 요청마다 로컬 추론 시간이 반복 발생한다.
- 추후 생성형 요약을 붙일 경우 LLM 비용이 반복 발생한다.
- RAG 검색과 분석 실패가 토론 전체 실패로 이어질 수 있다.

따라서 수집/인덱싱 시점에 분석을 저장하는 방식이 낫다. 특히 로컬 HuggingFace 모델을 쓰더라도 공시 1건마다 분석 시간이 발생하므로, 매 토론마다 다시 돌리는 방식은 피한다.

---

## 4. DB 설계

### 4-1. 신규 테이블: `evidence_analysis`

```sql
CREATE TABLE evidence_analysis (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_type VARCHAR(30) NOT NULL,
    source_id UUID NOT NULL,
    symbol VARCHAR(30) NOT NULL,
    sentiment VARCHAR(20) NOT NULL,
    impact_score INTEGER NOT NULL,
    confidence NUMERIC(4, 3),
    summary TEXT NOT NULL,
    key_points JSONB NOT NULL DEFAULT '[]'::jsonb,
    risks JSONB NOT NULL DEFAULT '[]'::jsonb,
    model_name VARCHAR(150) NOT NULL,
    prompt_version VARCHAR(50) NOT NULL,
    raw_response JSONB,
    analyzed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (source_type, source_id, prompt_version)
);

CREATE INDEX idx_evidence_analysis_symbol ON evidence_analysis (symbol);
CREATE INDEX idx_evidence_analysis_source ON evidence_analysis (source_type, source_id);
CREATE INDEX idx_evidence_analysis_sentiment ON evidence_analysis (sentiment);
```

### 4-2. `source_type`

| 값 | 연결 대상 |
|---|---|
| `news` | `news_cache.id` |
| `filing` | `filing_cache.id` |

기존 `news_cache.summary`, `filing_cache.summary` 컬럼은 유지한다. 단, 구조화된 분석 결과는 `evidence_analysis`에 저장한다.

---

## 5. 파일별 구현 계획

### 5-1. `app/models/evidence_analysis.py` 신규

역할:

- `EvidenceAnalysis` SQLAlchemy 모델 정의
- `source_type`, `source_id`, `symbol`, `sentiment`, `impact_score`, `summary`, `key_points`, `risks` 저장

예상 모델:

```python
class EvidenceAnalysis(Base):
    __tablename__ = "evidence_analysis"

    id = mapped_column(PGUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    source_type = mapped_column(String(30), nullable=False)
    source_id = mapped_column(PGUUID(as_uuid=True), nullable=False)
    symbol = mapped_column(String(30), nullable=False)
    sentiment = mapped_column(String(20), nullable=False)
    impact_score = mapped_column(Integer, nullable=False)
    confidence = mapped_column(Numeric(4, 3))
    summary = mapped_column(Text, nullable=False)
    key_points = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    risks = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    model_name = mapped_column(String(150), nullable=False)
    prompt_version = mapped_column(String(50), nullable=False)
    raw_response = mapped_column(JSONB)
    analyzed_at = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
```

### 5-2. `app/repositories/evidence_analysis_repository.py` 신규

역할:

- 분석 결과 upsert
- source id 목록으로 분석 결과 조회
- symbol 기준 최근 분석 조회

필요 메서드:

```python
def upsert_analysis(...)
def get_by_sources(source_type: str, source_ids: list[str]) -> dict[str, EvidenceAnalysis]
def list_recent_by_symbol(symbol: str, limit: int = 20) -> list[EvidenceAnalysis]
```

### 5-3. `app/domain/evidence_analysis.py` 신규

역할:

- 로컬 HuggingFace sentiment 모델 호출
- 규칙 기반 요약/핵심 문장/리스크 추출
- sentiment/impact_score 보정
- DB 저장

주요 메서드:

```python
class EvidenceAnalysisService:
    def analyze_news_row(self, row: NewsCache) -> EvidenceAnalysisResult: ...
    def analyze_filing_row(self, row: FilingCache, content: str | None = None) -> EvidenceAnalysisResult: ...
    def analyze_text(self, *, source_type, symbol, title, text, source_id) -> EvidenceAnalysisResult: ...
```

내부 구성:

```text
EvidenceAnalysisService
    ├── LocalHFSentimentAnalyzer
    │       └── transformers.pipeline("text-classification")
    ├── ExtractiveSummaryBuilder
    │       └── 제목/첫 문장/금액/증감 키워드 중심 요약
    ├── InvestmentImpactRuleEngine
    │       └── 투자 관점 positive/negative/mixed 보정
    └── EvidenceAnalysisRepository
```

### 5-4. `app/domain/news_ingestion.py` 수정

뉴스 수집 후 Chroma upsert가 성공한 시점에 분석 호출을 붙인다.

권장 위치:

```text
NewsIngestionService.sync_news_for_ticker()
    ├── news_cache upsert
    ├── Chroma news upsert
    └── EvidenceAnalysisService.analyze_news_row(row)
```

주의:

- 로컬 모델 실패가 뉴스 수집 전체 실패로 이어지면 안 된다.
- 실패 시 로그만 남기고 수집은 성공 처리한다.

### 5-5. `app/domain/evidence_indexing.py` 수정

공시 재색인 후 분석을 호출한다.

권장 위치:

```text
reindex_filing_for_symbol()
    ├── DART ZIP 다운로드
    ├── 본문 추출
    ├── summary 업데이트
    ├── Chroma filing chunks upsert
    └── EvidenceAnalysisService.analyze_filing_row(row, filing_text)
```

주의:

- 공시 본문은 길 수 있으므로 전체를 모델에 넣지 않는다.
- `filing_text[:6000]` 또는 중요 섹션만 넣는 방식으로 시작한다.
- 추후에는 Chroma 상위 청크를 다시 뽑아 분석 입력으로 사용할 수 있다.

### 5-6. `app/domain/evidence_retrieval.py` 수정

현재 `RetrievedEvidence`에 분석 필드를 추가한다.

추가 필드:

```python
sentiment: str | None = None
impact_score: int | None = None
analysis_summary: str | None = None
key_points: list[str] | None = None
risks: list[str] | None = None
```

검색 결과 생성 시:

```text
Chroma hit
    ↓
news_cache / filing_cache row join
    ↓
evidence_analysis join
    ↓
RetrievedEvidence.to_dict()
```

### 5-7. `app/agents/nodes/data_node.py` 수정

`format_evidence_context()` 결과에 분석 결과가 들어가도록 한다.

예상 출력:

```text
[근거 1][공시][mixed][영향도 +1]
제목: 타법인주식및출자증권취득결정
요약: 타법인주식취득결정. 기아 주식회사는 에이치엠지퓨처콤플렉스 주식회사에 대한 타법인 주식 취득을 결정했다. 취득금액은 2조 3,634억원으로 자기자본 대비 3.9% 규모다.
핵심 근거: 취득금액 2조 3,634억원, 자기자본 대비 3.9%
리스크: 대규모 현금 유출, 투자 회수 불확실성
원문 발췌: ...
```

---

## 6. 로컬 분석 엔진 설계

### 6-1. 분석 버전

초기 버전:

```text
evidence-analysis-v1
```

프롬프트 기반 생성형 LLM을 사용하지 않더라도 `prompt_version` 컬럼명은 유지한다. 실제 의미는 "분석 로직 버전"이다. 모델과 규칙이 바뀌면 이 값을 올려 재분석할 수 있다.

### 6-2. 신규 설정값

`app/config.py`에 아래 설정을 추가한다.

```python
analysis_provider: str = Field(default="local_hf", alias="ANALYSIS_PROVIDER")
analysis_model: str = Field(
    default="snunlp/KR-FinBert-SC",
    alias="ANALYSIS_MODEL",
)
analysis_enabled: bool = Field(default=True, alias="ANALYSIS_ENABLED")
analysis_max_chars: int = Field(default=6000, alias="ANALYSIS_MAX_CHARS")
analysis_prompt_version: str = Field(default="evidence-analysis-v1", alias="ANALYSIS_PROMPT_VERSION")
analysis_summary_provider: str = Field(default="extractive", alias="ANALYSIS_SUMMARY_PROVIDER")
analysis_generation_model: str | None = Field(default=None, alias="ANALYSIS_GENERATION_MODEL")
```

### 6-3. 의존성

`requirements.txt`에 아래 패키지가 필요하다.

```text
transformers
torch
```

이미 `sentence-transformers`가 있으므로 PyTorch 계열 의존성은 일부 환경에 존재할 수 있다. 다만 text-classification pipeline을 직접 쓰려면 `transformers`를 명시하는 것이 안전하다.

### 6-4. 분석 알고리즘

```text
입력: title + text[:ANALYSIS_MAX_CHARS]
    ↓
1. 문장 분리
    ↓
2. 로컬 HF sentiment 모델 실행
    ↓
3. 모델 label을 positive/negative/neutral로 매핑
    ↓
4. 투자 키워드 규칙으로 sentiment 보정
    ↓
5. impact_score 산출
    ↓
6. summary/key_points/risks 추출
    ↓
7. evidence_analysis 저장
```

### 6-5. 요약 전략

초기 production 요약은 생성형 모델을 사용하지 않는다.

권장 규칙:

```text
summary
    = 제목
    + 첫 번째 의미 있는 본문 문장
    + 금액/비율/증감/계약기간/취득목적 포함 문장 중 1개

key_points
    = 수주, 계약, 매출, 영업이익, 취득금액, 자기자본 대비, 배당, 자사주 등 키워드 포함 문장

risks
    = 유상증자, 전환사채, 소송, 횡령, 배임, 적자, 손실, 감소, 거래정지, 상장폐지 등 키워드 포함 문장
```

이 방식은 문장을 새로 만들지 않으므로 자연스러움은 낮을 수 있지만, 금융 공시에서 가장 중요한 수치/사실 왜곡 위험을 줄인다.

### 6-6. 로컬 생성 요약 후보

요약 품질 개선이 필요하면 별도 feature flag로 생성형 요약을 붙인다.

#### 실행 방식: Ollama 없이 transformers 직접 사용

GGUF 포맷이 아닌 HuggingFace 원본 모델을 transformers로 직접 로딩한다.

이유:

- 이미 KR-FinBert-SC를 transformers로 사용 중이라 코드 일관성 유지
- Ollama 서버를 별도로 관리할 필요 없음
- M2 Pro MPS 가속이 transformers에서 바로 적용됨
- `requirements.txt`만으로 배포 완결

Ollama와 비교:

| | Ollama | transformers 직접 |
|---|---|---|
| 설치 | brew + ollama pull 별도 | pip만으로 끝 |
| 코드 | HTTP 요청 | 지금 KR-FinBert-SC와 동일 방식 |
| 서버 관리 | 별도 프로세스 상시 실행 필요 | 앱 프로세스 안에서 처리 |
| 배포 | 서버 환경에 Ollama 별도 설치 필요 | requirements.txt만으로 끝 |
| 모델 교체 | ollama pull 한 줄 | 코드에서 모델명 변경 |

이 프로젝트에서는 모든 분석이 `EvidenceAnalysisService` 안에 들어가므로 서버 분리 이점이 없다. transformers 직접 사용이 맞다.

로딩 코드:

```python
from transformers import AutoModelForCausalLM, AutoTokenizer
import torch

# 앱 시작 시 1회 로딩 (singleton)
tokenizer = AutoTokenizer.from_pretrained("Qwen/Qwen2.5-3B-Instruct")
model = AutoModelForCausalLM.from_pretrained(
    "Qwen/Qwen2.5-3B-Instruct",
    dtype=torch.float16,
    device_map="mps",  # M2 Metal 가속
)
```

#### 실측치 (16GB M2 Pro 기준)

| 항목 | 측정값 |
|---|---|
| 최초 모델 다운로드 | 약 165초 (1회) |
| 이후 캐시 로딩 | 약 8초 |
| 뉴스 1건 생성 | 9~12초 |

뉴스 수집 시점에 백그라운드로 처리하면 9~12초는 허용 가능하다.  
토론 시작 시 실시간으로 호출하는 방식은 느리므로 적합하지 않다.

#### 실측 요약 품질 (Qwen2.5-3B-Instruct)

삼성전자 실적 뉴스:
```json
{
  "summary": "삼성전자는 2026년 2분기 영업이익이 10조원을 돌파할 전망이며, HBM 수요 증가와 메모리 반도체 가격 상승이 실적 개선의 주요 원인이다.",
  "key_points": ["2분기 영업이익 10조원 돌파 전망", "HBM 수요 증가"],
  "risks": []
}
```

유상증자 공시:
```json
{
  "summary": "주식회사 OO은 3000억원 규모의 유상증자를 통해 채무 상환과 운영자금 확보를 목표로 했다.",
  "key_points": ["3000억원 유상증자", "채무 상환 및 운영자금 확보"],
  "risks": ["기존 주주의 지분 희석", "주당 가치 하락"]
}
```

extractive 방식 대비 문장이 자연스럽고 맥락 보존이 확실히 낫다.

#### 16GB M2 Pro 우선순위

| 우선순위 | 모델 | 실행 방식 | 비고 |
|---:|---|---|---|
| 1 | `Qwen/Qwen2.5-3B-Instruct` | transformers 직접 | 실측 검증 완료 |
| 2 | `Qwen/Qwen2.5-7B-Instruct` | transformers 직접 | 메모리 여유 있을 때 |

운영 반영 기준:

```text
1. 10~20개 뉴스/공시 샘플로 JSON 출력 안정성 검증
2. 본문에 없는 내용 생성 여부 확인
3. 금액/비율/일자 보존 여부 확인
4. 규칙 기반 summary보다 명확히 나은 경우에만 optional provider로 추가
```

생성형 요약 프롬프트 원칙:

```text
너는 한국 상장사 공시/뉴스 요약기다.
본문에 없는 내용은 만들지 마라.
금액, 비율, 일자는 원문 그대로 유지하라.
아래 JSON 형식으로만 출력하라.

{
  "summary": "한 문장 요약",
  "key_points": ["핵심 사실 1", "핵심 사실 2"],
  "risks": ["주의점 1"]
}
```

### 6-7. 판단 기준

| sentiment | 기준 |
|---|---|
| `positive` | 실적 개선, 수주, 성장 투자, 주주환원, 재무 안정성 개선 |
| `negative` | 실적 악화, 비용 증가, 소송/규제, 지분 희석, 재무 부담 |
| `neutral` | 단순 행정 공시, 영향 불명확, 반복성 낮은 일반 뉴스 |
| `mixed` | 긍정과 부정 요인이 함께 존재 |

| impact_score | 의미 |
|---|---|
| `+2` | 강한 긍정 |
| `+1` | 약한 긍정 |
| `0` | 중립 |
| `-1` | 약한 부정 |
| `-2` | 강한 부정 |

### 6-8. 투자 키워드 규칙

긍정 키워드 예:

```text
수주, 계약 체결, 영업이익 증가, 매출 증가, 흑자전환, 배당 확대, 자사주 취득, 실적 개선, 신규 투자, 증설
```

부정 키워드 예:

```text
적자전환, 영업손실, 소송, 횡령, 배임, 관리종목, 상장폐지, 유상증자, 전환사채, 감자, 매출 감소, 영업이익 감소
```

보정 예:

```text
HF 모델: positive
본문: "유상증자", "운영자금 조달", "주식 희석"
결과: mixed 또는 negative

HF 모델: neutral
본문: "자사주 취득", "배당 확대"
결과: positive
```

---

## 7. MVP 구현 순서

### Phase 1. 저장 구조 추가

1. `EvidenceAnalysis` 모델 추가
2. migration 작성
3. repository 추가
4. 간단한 upsert/get 테스트 작성

검증:

```bash
python scripts/validate_evidence_analysis_repository.py
```

### Phase 2. 로컬 HuggingFace 분석 서비스 추가

1. `EvidenceAnalysisService` 추가
2. `LocalHFSentimentAnalyzer` 추가
3. `ExtractiveSummaryBuilder` 추가
4. `InvestmentImpactRuleEngine` 추가
5. 실패 시 rule-only fallback summary 생성

검증:

```bash
python scripts/validate_evidence_analysis_service.py
```

### Phase 3. 뉴스 수집 파이프라인 연결

1. `NewsIngestionService`에서 분석 호출
2. 로컬 모델 실패 시 수집 성공 유지
3. `news_cache.id` 기준 분석 결과 저장

검증:

```bash
python scripts/validate_news_analysis_flow.py
```

### Phase 4. 공시 인덱싱 파이프라인 연결

1. `EvidenceIndexingService.reindex_filing_for_symbol()`에서 분석 호출
2. `filing_cache.id` 기준 분석 결과 저장
3. 긴 공시는 앞부분 또는 핵심 청크만 분석 입력으로 사용

검증:

```bash
python scripts/validate_filing_analysis_flow.py
```

### Phase 5. 토론 RAG 반영

1. `EvidenceRetrievalService`에서 analysis join
2. `RetrievedEvidence`에 분석 필드 추가
3. `format_evidence_context()` 출력 개선
4. `data_agent_node`에서 bull/bear/moderator에게 분석 포함 컨텍스트 전달

검증:

```bash
python scripts/validate_debate_evidence_analysis_context.py
```

---

## 8. 실패 처리 정책

분석은 보조 기능이므로, 실패해도 수집/인덱싱은 성공해야 한다.

| 실패 지점 | 처리 |
|---|---|
| 모델 로딩 실패 | 로그 기록, rule-only fallback |
| 모델 추론 실패 | 로그 기록, rule-only fallback |
| 분석 결과 이상 | 기본값 보정 후 저장 |
| sentiment 값 이상 | `neutral`로 보정 |
| impact_score 범위 이상 | `0`으로 보정 |
| DB upsert 실패 | 로그 기록, 수집/인덱싱은 계속 |

---

## 9. 비용/성능 전략

### 9-1. 중복 분석 방지

`UNIQUE(source_type, source_id, prompt_version)`로 같은 프롬프트 버전의 분석은 한 번만 저장한다.

### 9-2. 긴 공시 입력 제한

공시는 수십만 자가 될 수 있으므로 전체를 모델에 넣지 않는다.

초기 전략:

```text
분석 입력 = filing_title + filing_text 앞 6000자
```

개선 전략:

```text
분석 입력 = 제목 + 주요 섹션 + Chroma 상위 청크 N개
```

### 9-3. 비동기/백그라운드 처리

초기에는 동기 처리로 시작할 수 있지만, 운영에서는 백그라운드 작업으로 분리하는 것이 좋다.

### 9-4. 모델 캐싱

HuggingFace 모델은 프로세스 시작 후 한 번만 로딩해야 한다.

```text
LocalHFSentimentAnalyzer
    ├── class-level singleton
    ├── pipeline lazy load
    └── 이후 요청은 같은 pipeline 재사용
```

매 뉴스/공시마다 모델을 다시 로딩하면 분석 시간이 매우 길어진다.

---

## 10. 최종 기대 결과

토론 에이전트는 더 이상 뉴스/공시 본문만 받지 않는다.

변경 후 전달 컨텍스트:

```text
[RAG 근거]
1. [공시][mixed][영향도 +1]
   제목: 타법인주식및출자증권취득결정
   요약: 타법인주식취득결정. 기아 주식회사는 에이치엠지퓨처콤플렉스 주식회사에 대한 타법인 주식 취득을 결정했다. 취득금액은 2조 3,634억원으로 자기자본 대비 3.9% 규모다.
   핵심 근거: 취득금액 2조 3,634억원, 자기자본 대비 3.9%
   리스크: 대규모 현금 유출, 투자 회수 불확실성
   발췌: 1. 발행회사 ... 취득금액 ...

2. [뉴스][negative][영향도 -1]
   제목: 반도체 업황 둔화 우려
   요약: 반도체 업황 둔화 우려. AI 서버 수요 증가에도 일부 메모리 제품의 수요 둔화 가능성이 제기됐다.
```

이렇게 되면 bull/bear/moderator는 단순 원문 검색 결과가 아니라, 사전에 정리된 투자 관점의 해석을 기반으로 토론할 수 있다.

---

## 11. 한 줄 요약

Chroma가 "관련 뉴스/공시를 찾는 역할"이라면, 로컬 분석 레이어는 "그 근거가 종목에 긍정인지 부정인지 규칙과 분류 모델로 해석해서 저장하고 토론 에이전트에게 전달하는 역할"이다.
