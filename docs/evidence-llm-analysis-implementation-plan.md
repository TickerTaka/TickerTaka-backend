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

뉴스 수집 단계에서는 분석을 호출하지 않는다.

현재 `NewsIngestionService.sync_news_for_ticker()`는 `news_cache`에 제목/URL/요약 메타데이터를 저장하는 역할에 가깝고, 분석에 필요한 충분한 본문은 이 단계에서 안정적으로 확보되지 않는다.

현재 흐름:

```text
NewsIngestionService.sync_news_for_ticker()
    ├── news_cache upsert
    └── 본문 분석은 하지 않음
```

주의:

- 수집 단계에 분석을 붙이면 제목/URL 중심의 빈약한 입력으로 분석될 수 있다.
- 뉴스 분석은 본문이 확보되는 `EvidenceIndexingService.reindex_news_for_symbol()`에서 수행한다.
- 추후 `NewsIngestionService`가 본문을 안정적으로 저장하는 구조로 바뀌면 그때 직접 분석 호출을 재검토한다.

### 5-5. `app/domain/evidence_indexing.py` 수정

뉴스/공시 재색인 후 분석을 호출한다.

뉴스 권장 위치:

```text
reindex_news_for_symbol()
    ├── ArticleScraper.scrape()
    ├── 본문 확보
    ├── Chroma news upsert
    └── EvidenceAnalysisService.analyze_news_row(row, content=scraped.content)
```

공시 권장 위치:

```text
reindex_filing_for_symbol()
    ├── DART ZIP 다운로드
    ├── 본문 추출
    ├── summary 업데이트
    ├── Chroma filing chunks upsert
    └── EvidenceAnalysisService.analyze_filing_row(row, filing_text)
```

주의:

- 뉴스 본문은 `ArticleScraper.scrape()` 이후에 생기므로, 뉴스 분석은 `reindex_news_for_symbol()`에 붙인다.
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
2. DART 보일러플레이트 문장 제거
    ↓
3. 로컬 HF sentiment 모델 실행
    ↓
4. 모델 label을 positive/negative/neutral로 매핑
    ↓
5. 투자 키워드 규칙으로 sentiment 보정
    ↓
6. impact_score 산출
    ↓
7. summary/key_points/risks 추출
    ↓
8. evidence_analysis 저장
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

#### DART 보일러플레이트 제거

DART 공시에는 모든 문서에 반복되는 행정/확인 문구가 들어간다. 특히 `임원ㆍ주요주주특정증권등소유상황보고서` 같은 문서는 앞부분에 아래 문구가 나오고, 실제 내용은 뒤쪽 표에 있다.

```text
[보일러플레이트 헤더]
- 증권선물위원회 귀중
- 보고의무발생일 :
- 보고서작성기준일 :
- 허위기재 또는 기재누락이 없음을 확인합니다

[실제 내용]
- 보고자 성명
- 발행회사
- 변동 전/후 주식수
- 취득/처분 수량
- 변동 사유
```

이 문구를 그대로 요약/감성 판단에 넣으면 `허위기재`, `기재누락` 같은 단어 때문에 부정으로 오분류될 수 있다. 따라서 문장 분리 직후 보일러플레이트 문장을 제거한 뒤 summary/key_points/risks를 만든다.

권장 패턴:

```python
DART_BOILERPLATE_PATTERNS = (
    "허위기재 또는 기재누락",
    "법규 및 기재상의 주의",
    "증권선물위원회 귀중",
    "금융위원회 귀중",
    "한국거래소 귀중",
    "보고서작성기준일",
    "보고의무발생일",
    "※ 보고자 본인",
)

@staticmethod
def _is_boilerplate(sentence: str) -> bool:
    normalized = re.sub(r"\s+", " ", sentence).strip()
    return any(pattern in normalized for pattern in DART_BOILERPLATE_PATTERNS)
```

패턴은 길게 잡기보다 핵심 구절만 짧게 잡는다. 예를 들어 `"허위기재 또는 기재누락이 없음을 확인합니다"`보다 `"허위기재 또는 기재누락"`이 줄바꿈/조사 변형에 강하다.

적용 예:

```python
sentences = [
    sentence
    for sentence in split_sentences(text)
    if not _is_boilerplate(sentence)
]

first_sentence = sentences[0] if sentences else ""
numeric_sentence = _find_numeric_sentence(sentences)

parts = [title.strip()]
if first_sentence:
    parts.append(first_sentence)
if numeric_sentence and numeric_sentence != first_sentence:
    parts.append(numeric_sentence)

summary = " ".join(parts)[:800]
```

보일러플레이트 제거 후 실제 표 내용이 구조화되어 있으면 아래처럼 요약한다.

```text
요약: 임원ㆍ주요주주특정증권등소유상황보고서. 성명: 홍길동 | 발행회사: 삼성전자 | 소유주식수: 변동 후 | 수량: 60,000주
```

남은 내용이 없으면 제목만 요약으로 둔다.

```text
요약: 임원ㆍ주요주주특정증권등소유상황보고서
```

이 공시 유형은 기본적으로 행정성 보고서이므로 기본 판단은 `neutral`, `impact_score = 0`이다. 단, 실제 표 내용에 `장내매도`, `담보권 실행`, `반대매매`, `최대주주 변경`처럼 투자 리스크가 되는 사건이 명확히 있으면 별도 규칙으로 `negative`, `-1`까지 보정할 수 있다.

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

### Phase 3. 뉴스 인덱싱 파이프라인 연결

1. `EvidenceIndexingService.reindex_news_for_symbol()`에서 분석 호출
2. `ArticleScraper.scrape()`로 확보한 본문을 분석 입력으로 전달
3. `news_cache.id` 기준 분석 결과 저장
4. 로컬 모델 실패 시 인덱싱 성공 유지

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

---

## 12. 분석 하네스 설계

분석 레이어는 AI 모델이 포함되므로 결과가 불안정할 수 있다.

분석 하네스는 로컬 모델과 규칙 엔진의 결과를 그대로 믿지 않고, 저장 전에 검증하고, 실패 시 fallback으로 전환하며, 실패 원인을 기록해 이후 규칙/모델 개선에 쓰는 운영 안전장치다.

```text
분석 하네스 사고 = AI 결과를 믿지 말고 검증하라
→ 검증에서 걸리면 어떻게 처리할 건데?
→ 처리 결과를 어떻게 기록할 건데?
→ 기록을 보고 어떻게 개선할 건데?
```

---

### 12-0. 에러 핸들러와 진짜 하네스의 차이

> 이 절은 구현 전에 반드시 읽어야 한다.

**현재 구현된 코드는 하네스가 아니다. 평범한 `try-except` 예외 처리다.**

```python
# 현재 코드 — 이건 하네스가 아니다
except Exception as exc:
    return None, None, {
        "status": "fallback",
        "failure_reason": type(exc).__name__,  # RuntimeError, OSError 같은 예외 클래스명
    }
```

이 코드는 "파이썬 프로세스가 죽었는가?"만 감시한다. AI 시스템에서 진짜 위험한 실패는 예외를 던지지 않는다.

| 실패 유형 | 파이썬 예외 | 현재 감지 여부 |
|---|---|---|
| 모델 로딩 크래시 | `RuntimeError` 발생 | 감지됨 |
| 추론 중 메모리 부족 | `OOM` 발생 | 감지됨 |
| 표 헤더가 요약에 통째로 들어감 | 예외 없음, `status: pass` | **감지 안 됨** |
| 유상증자를 `positive`로 오분류 | 예외 없음, `status: pass` | **감지 안 됨** |
| 요약이 제목 한 줄만 남음 | 예외 없음, `status: pass` | **감지 안 됨** |
| sentiment와 impact_score가 논리 모순 | 예외 없음, `status: pass` | **감지 안 됨** |

**"의미 있는 실패는 예외를 던지지 않는다."** 이것이 일반 소프트웨어와 AI 시스템을 가르는 핵심이다.

일반 소프트웨어는 잘못된 입력이나 네트워크 오류처럼 "프로세스 관점의 실패"를 다루면 된다. AI 시스템은 거기에 더해 "결과물이 비즈니스 로직에 쓸 수 있는 품질인가?"라는 **의미론적 실패(Semantic Failure)**를 추가로 감시해야 한다.

---

### 12-1. 가드레일: 의미론적 실패를 강제 실패로 전환

진짜 하네스가 되려면 "이건 명백히 쓰레기"라고 확신할 수 있는 경계선 몇 개를 코드에서 명시적으로 `raise`해야 한다. 그래야 기존 `except` 블록이 의미론적 실패도 낚아채서 `failure_reason`으로 기록하고 fallback을 트리거한다.

```python
class HarnessValidationError(Exception):
    pass


def _validate_analysis_result(
    summary: str,
    sentiment: str,
    impact_score: int,
    title: str,
) -> None:
    """DB 저장 직전 호출. 명백한 품질 실패를 강제 예외로 전환한다."""

    # 1. 표 헤더 노이즈 — 파이프가 4개 이상이고 300자 초과면 파서 오염 의심
    if len(summary) > 300 and summary.count("|") >= 4:
        raise HarnessValidationError("err_table_header_noise")

    # 2. 빈 깡통 — 제목만 남거나 완전히 비어 있음
    if not summary.strip() or summary.strip() == title.strip():
        raise HarnessValidationError("err_empty_summary")

    # 3. 논리 모순 — sentiment와 impact_score가 반대 방향
    if sentiment == "positive" and impact_score < 0:
        raise HarnessValidationError("err_sentiment_impact_mismatch")
    if sentiment == "negative" and impact_score > 0:
        raise HarnessValidationError("err_sentiment_impact_mismatch")
```

이 `_validate_analysis_result`를 `analyze_text` 안에서 DB 저장 직전에 호출한다.

```python
# analyze_text 내부 — DB 저장 직전
try:
    _validate_analysis_result(summary, sentiment, impact_score, title)
except HarnessValidationError as exc:
    failure_reason = str(exc)
    # fallback: 제목만 남기고 neutral/0으로 보정
    summary = title.strip()
    sentiment = "neutral"
    impact_score = 0
    raw_response["status"] = "fallback"
    raw_response["failure_reason"] = failure_reason
```

이렇게 되면 `raw_response`의 `failure_reason`은 파이썬 예외 이름(`RuntimeError`)이 아니라 비즈니스 의미가 있는 코드(`err_table_header_noise`)가 된다.

**가드레일 설계 원칙**

- "이건 100% 쓰레기다"라고 확신할 수 있는 것만 넣는다. 애매한 케이스는 넣지 않는다.
- 가드레일이 많아질수록 false positive(정상 결과를 걸러냄)가 늘어나므로 처음엔 3개 이하로 시작한다.
- 새 케이스를 추가할 때는 반드시 골든 테스트셋으로 기존 PASS 케이스가 여전히 통과하는지 확인한다.

---

### 12-2. 실시간 피드백 루프 (런타임 자가 교정)

가드레일이 있어야 이 루프가 실제로 돌아간다. 가드레일 없이 오프라인 피드백(12-3)만 두면, 모든 결과가 `status: pass`로 저장되어 어느 로그를 들여다봐야 할지 알 수 없다.

```text
뉴스/공시 입력
    ↓
분석 실행
    ├── extractive summary 생성
    ├── HF sentiment 분류
    └── rule engine 보정
    ↓
_validate_analysis_result()  ← 가드레일
    ├── PASS → raw_response: {status: pass} → DB 저장
    └── FAIL (HarnessValidationError) → failure_reason 기록 → fallback 보정 → DB 저장
    
(optional: generative summary provider 켤 때)
    ↓
수치 정합성 검증 (_verify_numerical_grounding)
    ├── PASS → raw_response: {status: pass}
    └── FAIL → failure_reason: numerical_grounding_failed → extractive fallback
```

공통 가드레일 항목:

| 체크 | failure_reason | 비고 |
|---|---|---|
| summary에 `\|` 4개 이상 + 300자 초과 | `err_table_header_noise` | DART rowspan 파서 오염 |
| summary가 비거나 제목과 동일 | `err_empty_summary` | 본문 추출 실패 |
| sentiment와 impact_score 논리 모순 | `err_sentiment_impact_mismatch` | 규칙 엔진 오류 |

생성형 요약 전용 추가 항목:

| 체크 | failure_reason | 비고 |
|---|---|---|
| JSON 필수 키 누락 | `err_invalid_json_output` | 생성형 모델 출력 형식 불안정 |
| 요약 수치가 원문에 없음 | `err_numerical_grounding_failed` | 환각 감지 |

fallback 결과도 최종 분석 결과로 저장한다. `raw_response`에는 실패 원인과 원래 응답을 남겨 이후 개선에 사용한다.

```json
{
  "provider": "extractive",
  "status": "fallback",
  "failure_reason": "err_table_header_noise",
  "fallback_summary": "임원ㆍ주요주주특정증권등소유상황보고서"
}
```

---

### 12-3. 수치 정합성 검증 (`_verify_numerical_grounding`)

생성형 요약(Qwen 등)에서 수치 환각을 잡는 검증 함수다.

원리: 요약문에 등장한 숫자/단위 토큰이 원문에 독립된 숫자로 존재하는지 확인한다.

```python
import re

def _verify_numerical_grounding(original_text: str, summary_text: str) -> bool:
    clean_original = re.sub(r'[\s,]', '', original_text)
    clean_summary = re.sub(r'[\s,]', '', summary_text)

    summary_tokens = re.findall(r'\d+(?:\.\d+)?[조억만천원%년월일주명배]+', clean_summary)

    for token in summary_tokens:
        pattern = r'(?<!\d)' + re.escape(token) + r'(?!\d)'
        if not re.search(pattern, clean_original):
            return False

    return True
```

extractive 요약은 원문 문장을 그대로 쓰므로 이 검증이 필요 없다. 생성형 모델이 새 문장을 만들 때만 적용한다.

---

### 12-4. 오프라인 피드백 루프 (개선 사이클)

가드레일(12-1)이 `failure_reason`을 쌓아줘야 이 루프가 의미 있다. 모든 결과가 `status: pass`이면 어느 로그를 봐야 할지 알 수 없다.

```text
evidence_analysis.raw_response에 failure_reason 기록 (가드레일이 해줌)
    ↓
실패 패턴 쿼리로 어떤 케이스가 자주 실패하는지 확인
    ↓
원인 분석
(예: "err_table_header_noise가 임원보고서에서 반복 → 파서 개선 필요")
(예: "err_sentiment_impact_mismatch가 전환사채 공시에서 반복 → 키워드 규칙 수정 필요")
    ↓
규칙 엔진 수정 또는 가드레일 임계값 조정
    ↓
골든 테스트셋으로 전체 재검증
    ↓
prompt_version = "evidence-analysis-v2"로 올려 반영
```

**실패 패턴 쿼리 예시**:

```sql
SELECT
    raw_response->>'failure_reason'  AS reason,
    count(*)                         AS cnt,
    array_agg(DISTINCT symbol)       AS symbols
FROM evidence_analysis
WHERE raw_response->>'status' = 'fallback'
  AND analyzed_at > now() - interval '7 days'
GROUP BY reason
ORDER BY cnt DESC;
```

`prompt_version` 컬럼이 있으면 v1 결과와 v2 결과를 나란히 비교할 수 있다.

---

### 12-5. 골든 테스트셋 (`scripts/run_analysis_benchmark.py`)

가드레일을 추가하거나 규칙을 바꿀 때 기존 케이스가 여전히 통과하는지 확인하는 회귀 테스트다.

현재 `scripts/run_analysis_benchmark.py`에 3개 케이스가 구현되어 있다. 규칙 엔진이나 가드레일을 바꿀 때마다 반드시 실행한다.

```bash
PYTHONPATH=. .venv/bin/python scripts/run_analysis_benchmark.py
```

**추가해야 할 케이스** (현재 3개로는 실제 분류 경계 케이스를 커버하지 못함):

```python
# 표 헤더 노이즈 — 가드레일이 잡아야 함
{
    "title": "임원ㆍ주요주주특정증권등소유상황보고서",
    "text": "보고구분: 성명(명칭) | 보고자 구분: 한 글 | 한자(영문) 보고구분: ...",
    "expected_sentinel": "err_table_header_noise",  # fallback 되어야 함
},
# 전환사채 — 부정으로 잡아야 함
{
    "title": "전환사채권부사채발행결정",
    "text": "회사는 운영자금 확보를 위해 200억원 규모의 전환사채를 발행하기로 결의했다.",
    "expected_sentiment": "negative",
    "expected_impact_range": (-2, 0),
},
# 수주 — 긍정으로 잡아야 함
{
    "title": "단일판매공급계약체결",
    "text": "당사는 A사와 3,500억원 규모의 반도체 부품 공급 계약을 체결했다.",
    "expected_sentiment": "positive",
    "expected_impact_range": (0, 2),
    "expected_keywords_in_summary": ["3,500억원"],
},
```

출력 지표:

| 지표 | 의미 |
|---|---|
| sentiment accuracy | 기대 sentiment와 실제 sentiment 일치율 |
| impact range pass rate | 기대 impact_score 범위 통과율 |
| summary keyword retention rate | 요약에 필수 수치/키워드가 남아 있는 비율 |
| guardrail trigger rate | 가드레일이 올바른 케이스에 발동하는 비율 |
| false positive rate | 정상 케이스를 가드레일이 잘못 걸러내는 비율 |

---

### 12-6. 단계별 구현 상태와 진짜 하네스까지의 거리

| 단계 | 내용 | 현재 상태 |
|---|---|---|
| 에러 핸들러 | 파이썬 예외를 `try-except`로 잡아 `failure_reason` 기록 | 구현됨 |
| 가드레일 | 의미론적 실패를 `HarnessValidationError`로 강제 전환 | **미구현** |
| 실시간 루프 | 가드레일 발동 시 fallback 보정 후 저장 | 가드레일 이후 구현 가능 |
| 실패 쿼리 | DB에서 `failure_reason` 패턴을 집계하는 스크립트 | **미구현** |
| 골든 테스트셋 | 회귀 검증 스크립트 (`run_analysis_benchmark.py`) | 구현됨 (케이스 3개, 확충 필요) |
| 오프라인 루프 | 쿼리 → 원인 분석 → 규칙 수정 → 재검증 → 버전 업 | 프로세스만 정의됨, 툴링 미구현 |

**지금 당장 추가해야 진짜 하네스가 되는 것:**

1. `_validate_analysis_result()` 함수 — 가드레일 3개 (표 헤더 노이즈, 빈 요약, 논리 모순)
2. 골든 테스트셋 케이스 추가 — 특히 가드레일 발동 케이스와 경계 케이스

---

### 12-7. 세 가지 상태 비교

| 구분 | 에러 로거 (현재) | 가드레일 추가 후 | 풀 하네스 |
|---|---|---|---|
| 감시 대상 | 파이썬 프로세스 죽음 | 프로세스 죽음 + 명백한 품질 실패 | 프로세스 + 품질 + 분포 이상 |
| 표 헤더 노이즈 | `status: pass`로 통과 | `err_table_header_noise`로 fallback | 동일 + 알림 |
| 오분류 | `status: pass`로 통과 | 논리 모순만 잡음 | 통계적 이상 감지까지 |
| 실패 패턴 분석 | 불가능 (모두 pass) | 가능 (`failure_reason` 집계) | 가능 |
| 회귀 방지 | 불가능 | 골든셋으로 가능 | 골든셋으로 가능 |
| 구현 복잡도 | 낮음 (현재) | 낮음 (함수 1개 추가) | 높음 |

---

## 13. 추가로 적용할 수 있는 엔지니어링 패턴

분석 하네스 외에도 이 기능에는 아래 패턴을 적용할 수 있다.

### 13-1. 관측성(Observability)

분석이 얼마나 자주 성공/실패하는지, 어떤 provider가 느린지, fallback이 어느 종목/공시 유형에서 많이 발생하는지 지표로 남긴다.

권장 지표:

| 지표 | 설명 |
|---|---|
| `analysis_success_total` | 분석 성공 건수 |
| `analysis_fallback_total` | fallback 발생 건수 |
| `analysis_latency_ms` | 분석 소요 시간 |
| `analysis_provider` | `local_hf`, `rule_only`, `generative` |
| `analysis_failure_reason` | `model_load_failed`, `invalid_output`, `numerical_grounding_failed` 등 |

### 13-2. 멱등성(Idempotency)

같은 `source_type + source_id + prompt_version` 조합은 다시 분석해도 중복 저장되지 않아야 한다.

이미 DB에 `UNIQUE(source_type, source_id, prompt_version)`가 있으므로, 서비스 레벨에서도 아래 정책을 둔다.

```text
분석 결과가 이미 있으면 skip
force=True이면 같은 prompt_version도 재분석
prompt_version이 바뀌면 새 분석으로 저장
```

### 13-3. 큐 기반 백그라운드 처리

뉴스/공시 수집 요청 흐름에서 분석을 직접 오래 기다리지 않도록 분석 작업을 큐로 분리할 수 있다.

```text
수집/인덱싱 성공
    ↓
analysis_jobs enqueue
    ↓
worker가 EvidenceAnalysisService 실행
    ↓
evidence_analysis 저장
```

초기에는 동기 처리로 충분하지만, 운영에서 뉴스 수집량이 늘면 큐 기반 처리가 더 안정적이다.

### 13-4. Provider Strategy 패턴

요약/분류 provider를 인터페이스로 분리하면 `extractive`, `local_hf`, `generative`를 설정값으로 쉽게 바꿀 수 있다.

```text
SummaryProvider
    ├── ExtractiveSummaryProvider
    └── GenerativeSummaryProvider

SentimentProvider
    ├── LocalHFSentimentProvider
    └── RuleOnlySentimentProvider
```

이렇게 두면 Qwen 요약을 나중에 붙일 때 기존 분석 서비스의 흐름을 크게 바꾸지 않아도 된다.

### 13-5. Shadow Mode

새 모델이나 새 규칙을 바로 production 결과로 쓰지 않고, 기존 결과와 나란히 실행해 비교한다.

```text
production result: evidence-analysis-v1 저장
shadow result: evidence-analysis-v2 후보를 raw_response 또는 별도 로그에 기록
비교 후 품질이 더 좋을 때 prompt_version 승격
```

이 방식은 새 모델이 좋아 보이지만 특정 공시에서 오히려 나빠지는 경우를 잡는 데 유용하다.

### 13-6. 골든셋 기반 릴리즈 게이트

분석 규칙이나 모델을 바꿀 때 `scripts/run_analysis_benchmark.py`가 일정 기준을 통과해야 배포한다.

예:

```text
sentiment accuracy >= 0.80
impact range pass rate >= 0.90
summary keyword retention rate >= 0.95
numerical grounding pass rate == 1.00
```

### 13-7. 데이터 계약(Data Contract)

토론 에이전트가 기대하는 분석 필드를 계약으로 고정한다.

필수 계약:

| 필드 | 계약 |
|---|---|
| `sentiment` | `positive/negative/neutral/mixed` 중 하나 또는 `None` |
| `impact_score` | `-2 ~ +2` 정수 또는 `None` |
| `analysis_summary` | 문자열 또는 `None` |
| `key_points` | 문자열 배열 |
| `risks` | 문자열 배열 |

이 계약이 있으면 분석 레이어가 실패해도 `data_agent_node`와 토론 에이전트는 같은 형태의 데이터를 안정적으로 받을 수 있다.

---

## 14. 단건 공시/Qwen 프로토타입 실험

정식 구현 전에 기존 수집/인덱싱/토론 파이프라인을 건드리지 않고, 공시 1건 또는 샘플 텍스트 1건만 따로 분석해보는 독립 프로토타입을 둔다.

프로토타입 스크립트:

```bash
scripts/prototype_evidence_analysis_qwen.py
```

목적:

| 확인 항목 | 설명 |
|---|---|
| extractive baseline | 규칙 기반 요약/키워드/리스크 추출이 최소 동작하는지 확인 |
| Qwen 생성 요약 | Qwen이 `summary/key_points/risks` JSON을 안정적으로 생성하는지 확인 |
| 하네스 검증 | 수치 정합성, 필드 타입, fallback 전환이 동작하는지 확인 |
| 실행 시간 | 로컬 MPS/CPU에서 1건 처리 시간이 운영에 넣을 만한지 측정 |
| 기존 시스템 영향 | DB, Chroma, ingestion, retrieval 코드를 수정하지 않고 실험 |

### 14-1. 실행 방식

기본 샘플 중 하나를 extractive 방식으로 분석:

```bash
PYTHONPATH=. .venv/bin/python scripts/prototype_evidence_analysis_qwen.py \
  --provider extractive \
  --case-index 1
```

직접 공시/뉴스 텍스트를 넣어서 분석:

```bash
PYTHONPATH=. .venv/bin/python scripts/prototype_evidence_analysis_qwen.py \
  --provider extractive \
  --source-type filing \
  --symbol 000000 \
  --title 유상증자결정 \
  --text '주식회사 OO은 운영자금 조달을 목적으로 보통주 5,000만주를 발행하는 유상증자를 결정했다. 조달금액은 3,000억원이며 기존 주주의 지분이 희석된다.'
```

텍스트 파일에 저장된 공시 본문을 분석:

```bash
PYTHONPATH=. .venv/bin/python scripts/prototype_evidence_analysis_qwen.py \
  --provider extractive \
  --source-type filing \
  --symbol 005930 \
  --title '주요사항보고서' \
  --text-file /path/to/filing.txt
```

DART 접수번호로 공시 본문을 받아와 분석:

```bash
PYTHONPATH=. .venv/bin/python scripts/prototype_evidence_analysis_qwen.py \
  --provider extractive \
  --symbol 005930 \
  --title 'DART 공시 테스트' \
  --dart-receipt-no 20260528000000
```

Qwen 로컬 모델로 생성 요약을 실험:

```bash
PYTHONPATH=. .venv/bin/python scripts/prototype_evidence_analysis_qwen.py \
  --provider qwen \
  --case-index 0 \
  --device mps \
  --max-new-tokens 180
```

### 14-2. 프로토타입 출력

프로토타입은 DB에 저장하지 않고 JSON만 출력한다.

예상 출력 필드:

```json
{
  "summary": "한 문장 요약",
  "sentiment": "negative",
  "impact_score": -2,
  "confidence": 0.78,
  "key_points": ["핵심 사실"],
  "risks": ["주의점"],
  "model_name": "rule-only-extractive",
  "prompt_version": "prototype-evidence-analysis-v1",
  "raw_response": {
    "provider": "extractive",
    "status": "pass",
    "validation_errors": []
  },
  "elapsed_ms": 0
}
```

### 14-3. 프로토타입 판정 기준

Qwen provider는 아래 기준을 만족할 때만 정식 `EvidenceAnalysisService`의 optional provider 후보로 승격한다.

| 기준 | 통과 조건 |
|---|---|
| JSON 안정성 | `summary/key_points/risks`를 파싱 가능한 JSON으로 출력 |
| 수치 정합성 | 요약에 나온 금액/비율/날짜가 원문에 존재 |
| 처리 시간 | 1건 생성 시간이 운영 허용 범위 안에 있음 |
| fallback 동작 | 검증 실패 시 extractive 결과로 대체 |
| 품질 개선 | extractive baseline보다 요약이 명확하게 좋아야 함 |

### 14-4. 현재 프로토타입 상태

현재 상태:

| 항목 | 상태 |
|---|---|
| extractive 샘플 실행 | 통과 |
| 직접 `--title/--text` 입력 | 통과 |
| `--text-file` 입력 | 코드 지원, 별도 파일로 실행 가능 |
| `--dart-receipt-no` 입력 | 실제 OpenDART 공시 1건 fetch/분석 확인 |
| Qwen 실행 | 모델 로딩 확인. 생성 속도/출력 안정성은 추가 측정 필요 |

실제 테스트 사례:

```text
종목: 삼성전자 005930
접수번호: 20260529001627
공시명: 임원ㆍ주요주주특정증권등소유상황보고서
결과: neutral, impact_score 0, harness pass
```

프로토타입 실행 명령:

```bash
PYTHONPATH=. .venv/bin/python scripts/prototype_evidence_analysis_qwen.py \
  --provider extractive \
  --output simple \
  --symbol 005930 \
  --title '임원ㆍ주요주주특정증권등소유상황보고서' \
  --dart-receipt-no 20260529001627
```

현재 출력 예시:

```text
[분석 1]
요약: 임원ㆍ주요주주특정증권등소유상황보고서 보고자 : | 김경석 [회 사 명] 법인구분: 발행주식 총수 보고구분: 성명(명칭) | 보고자 구분: 한 글 | 한자(영문) 보고구분: 성명(명칭) | 보고자 구분: 생년월일 또는 사업자등록번호 등 | 생년월일 또는 사업자등록번호 등 보고구분: 주소(본점소재지)[읍ㆍ면ㆍ동까지만 기재] 보고구분: 발행회사와의 관계 | 보고자 구분: 임원(등기여부) | 직위명 보고구분: 발행회사와의 관계 | 보고자 구분: 선임일 | 퇴임일 보고구분: 발행회사와의 관계 | 보고자 구분: 주요주주 보고구분: 업무상 연락처및 담당자 | 보고자 구분: 소속회사 | 부 서 보고구분: 업무상 연락처및 담당자 | 보고자 구분: 직 위 | 전화번호 보고구분: 업무상 연락처및 담당자 | 보고자 구분: 성 명 | 팩스번호 보고구분: 업무상 연락처및 담당자 | 보고자 구분: 이메일 주소 보고서작성 기준일: 보고서작성 기준일 | 특정증권등: 특정증권등의수(주) | 특정증권등: 비율(%) | 주권: 주식수(주) | 주권: 비율(%) 직전보고서 이번보고서 증 감 | 보고서작성 기준일: 증 감 특정증권등의 내역: 주 권 | 특정증권등의 내역: 신주인수권이표시된것 | 특정증권등의 내역: 전환사채권 | 특정증권등의 내역: 신주인수권부사채권 | 특정증권등의 내역: 이익참가부사채권 | 특정증권등의 내역: 교환사채권 | 특정증권등의 내역: 증권예탁증권
긍부정: neutral
영향도: +0
하네스: pass
```

이 출력은 보일러플레이트 제거와 행정성 공시 neutral 보정은 동작하지만, 표 헤더가 아직 길게 남는다는 한계도 보여준다. 다음 개선은 표 헤더보다 실제 값이 있는 행(`보고자`, `발행회사`, `증감`, `변동사유`, `소유주식수`)을 우선 선택하는 규칙이다.

이 테스트에서 보일러플레이트 문구의 `허위기재`, `기재누락` 때문에 처음에는 negative로 오분류될 수 있음을 확인했다. 따라서 DART 보일러플레이트 제거와 행정성 공시 neutral 보정은 MVP 규칙에 포함한다.

따라서 다음 단계는 Qwen을 바로 production 경로에 넣는 것이 아니라, 단건 공시 3~5개로 생성 시간과 JSON 안정성을 먼저 측정하는 것이다.
