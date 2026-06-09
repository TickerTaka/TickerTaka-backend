from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
import logging
import re
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import FilingCache, NewsCache
from app.repositories.evidence_analysis_repository import EvidenceAnalysisRepository

logger = logging.getLogger(__name__)

SOURCE_TYPE_NEWS = "news"
SOURCE_TYPE_FILING = "filing"
VALID_SENTIMENTS = {"positive", "negative", "neutral", "mixed"}
EVENT_TYPES = {
    "잠정실적",
    "손익구조변경",
    "유상증자",
    "자사주취득",
    "단일판매공급계약",
    "배당",
    "소송",
    "횡령배임",
    "기타",
}
# Qwen 보강 대상 공시: 표 본문 + 실적/손익 유형 제목만 호출(비용 절감)
FILING_QWEN_TITLE_KEYWORDS = (
    "잠정실적",
    "영업(잠정)실적",
    "영업잠정실적",
    "손익구조",
    "매출액또는손익구조",
    "매출액또는손익구조30",
    "결산실적",
)
POSITIVE_KEYWORDS = (
    "수주",
    "계약 체결",
    "영업이익 증가",
    "매출 증가",
    "흑자전환",
    "배당 확대",
    "자사주 취득",
    "실적 개선",
    "신규 투자",
    "증설",
    "hbm",
    "주식소각",
    "주주가치 제고",
    "소각예정",
)
NEGATIVE_KEYWORDS = (
    "적자전환",
    "영업손실",
    "소송",
    "횡령",
    "배임",
    "관리종목",
    "상장폐지",
    "유상증자",
    "전환사채",
    "감자",
    "매출 감소",
    "영업이익 감소",
    "지분 희석",
    "희석",
    "거래정지",
)
KEY_POINT_KEYWORDS = (
    "억원",
    "조원",
    "%",
    "계약",
    "수주",
    "매출",
    "영업이익",
    "취득금액",
    "자기자본",
    "배당",
    "자사주",
    "유상증자",
    "전환사채",
)
DART_BOILERPLATE_PATTERNS = (
    "허위기재 또는 기재누락",
    "법규 및 기재상의 주의",
    "증권선물위원회 귀중",
    "금융위원회 귀중",
    "한국거래소 귀중",
    "금융감독원장 귀중",
    "금융감독원장 귀하",
    "보고서작성기준일",
    "보고의무발생일",
    "※ 보고자 본인",
    "tel:",
    "(fax)",
    "(전 화)",
    "전 화)",
    "(홈페이지)",
)
NEUTRAL_ADMIN_TITLE_KEYWORDS = (
    "임원ㆍ주요주주특정증권등소유상황보고서",
    "임원·주요주주특정증권등소유상황보고서",
    "주식등의대량보유상황보고서",
    "주주총회소집공고",
    "최대주주등소유주식변동신고서",
    # 정기보고서: 수백 페이지 문서라 앞부분이 비핵심 정보(최대주주 이력 등)
    # 실적 핵심은 별도 잠정실적 공시에 있으므로 분석 스킵
    "사업보고서",
    "분기보고서",
    "반기보고서",
    "감사보고서",
)
HARD_NEGATIVE_TITLE_KEYWORDS = (
    "유상증자",
    "전환사채",
    "감자결정",
    "상장폐지",
    "거래정지",
    "횡령",
    "배임",
)
HARD_POSITIVE_TITLE_KEYWORDS = (
    "자기주식취득",
    "자사주취득",
    "주식소각",
    "현금ㆍ현물배당결정",
    "단일판매ㆍ공급계약체결",
    "단일판매공급계약체결",
)
MIXED_EVENT_TITLE_KEYWORDS = (
    "타법인주식및출자증권취득결정",
)
CRITICAL_NEGATIVE_PHRASES = (
    "담보권 실행",
    "반대매매",
    "최대주주 변경",
    "상장폐지",
    "거래정지",
    # "횡령"/"배임"은 DART 표준양식의 "횡령ㆍ배임사항 기재여부" 라벨로 거의 항상 등장해 오탐.
    # 실제 횡령/배임 공시는 HARD_NEGATIVE_TITLE_KEYWORDS(제목)로 잡는다.
)
# DART 표준양식의 "○○ 발생여부: 아니오" 류 체크리스트 부정문 — 같은 줄에 있으면 치명 문구로 보지 않는다.
NEGATION_MARKERS = (
    "아니오",
    "해당없음",
    "해당 없음",
    "해당사항없음",
    "해당사항 없음",
    "미해당",
)


def _critical_negative_present(text: str) -> bool:
    """본문에 치명 문구가 부정어 없이 존재하는 줄이 있으면 True."""
    for line in text.lower().splitlines():
        if any(neg in line for neg in NEGATION_MARKERS):
            continue
        if any(phrase.lower() in line for phrase in CRITICAL_NEGATIVE_PHRASES):
            return True
    return False


class HarnessValidationError(Exception):
    """Raised when an analysis result is semantically unsafe to persist as-is."""


@dataclass(slots=True)
class EvidenceAnalysisResult:
    source_type: str
    source_id: str
    symbol: str
    sentiment: str
    impact_score: int
    confidence: float | None
    summary: str
    key_points: list[str]
    risks: list[str]
    model_name: str
    prompt_version: str
    event_type: str | None = None
    evidence: list[str] = field(default_factory=list)
    raw_response: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class InvestmentImpactDecision:
    sentiment: str
    impact_score: int
    confidence: float
    source: str
    positive_hits: list[str]
    negative_hits: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class LocalHFSentimentAnalyzer:
    """Lazy HuggingFace sentiment classifier with rule-only fallback."""

    _pipelines: dict[str, Any] = {}

    def __init__(self, model_name: str) -> None:
        self.model_name = model_name

    def analyze(self, text: str) -> tuple[str | None, float | None, dict[str, Any]]:
        try:
            pipe = self._get_pipeline()
            output = pipe(text[:512], truncation=True, max_length=512)
            item = output[0] if isinstance(output, list) and output else output
            label = str(item.get("label", "")) if isinstance(item, dict) else ""
            score = float(item.get("score", 0.0)) if isinstance(item, dict) else None
            return self._map_label(label), score, {"status": "pass", "label": label, "score": score}
        except Exception as exc:
            logger.info("local HF sentiment unavailable, falling back to rules: %s", exc)
            return None, None, {
                "status": "fallback",
                "failure_reason": type(exc).__name__,
                "failure_message": str(exc),
            }

    def _get_pipeline(self) -> Any:
        if self.model_name not in self._pipelines:
            import torch
            from transformers import pipeline

            device: str | int = "mps" if torch.backends.mps.is_available() else -1
            self._pipelines[self.model_name] = pipeline("text-classification", model=self.model_name, device=device)
        return self._pipelines[self.model_name]

    @staticmethod
    def _map_label(label: str) -> str | None:
        normalized = label.strip().lower()
        if any(token in normalized for token in ("positive", "pos", "긍정", "label_2", "5 stars", "4 stars")):
            return "positive"
        if any(token in normalized for token in ("negative", "neg", "부정", "label_0", "1 star", "2 stars")):
            return "negative"
        if any(token in normalized for token in ("neutral", "neu", "중립", "label_1", "3 stars")):
            return "neutral"
        return None


class LocalQwenEvidenceAnalyzer:
    """Lazy local Qwen(sLLM) 구조화 분석기.

    직렬화된 표 본문은 FinBERT가 못 읽으므로 Qwen이 표/문맥을 해석해
    구조화 JSON(summary/event_type/sentiment/impact/key_points/risks/evidence)을 직접 낸다.
    공시는 전체 필드, 뉴스는 summary/key_points/risks/evidence 만(감성은 FinBERT 권위 유지).
    """

    _models: dict[str, Any] = {}

    def __init__(self, model_name: str) -> None:
        self.model_name = model_name

    def analyze(self, title: str, text: str, *, kind: str, max_new_tokens: int = 768) -> dict[str, Any] | None:
        try:
            import torch

            tokenizer, model, device = self._get_model()
            messages = [
                {"role": "system", "content": "너는 사실 기반 한국 금융 공시/뉴스 분석기다. JSON 한 개만 출력한다."},
                {"role": "user", "content": self._build_prompt(title, text, kind)},
            ]
            chat = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
            inputs = tokenizer([chat], return_tensors="pt").to(device)
            with torch.no_grad():
                generated = model.generate(
                    **inputs,
                    max_new_tokens=max_new_tokens,
                    do_sample=False,
                    temperature=None,
                    top_p=None,
                    top_k=None,
                )
            output_ids = generated[0][inputs.input_ids.shape[-1] :]
            raw = tokenizer.decode(output_ids, skip_special_tokens=True)
            return self._parse_json(raw)
        except Exception as exc:
            logger.info("local Qwen analysis unavailable: %s", exc)
            return None

    def _get_model(self) -> Any:
        if self.model_name not in self._models:
            import torch
            from transformers import AutoModelForCausalLM, AutoTokenizer

            device = "mps" if torch.backends.mps.is_available() else "cpu"
            tokenizer = AutoTokenizer.from_pretrained(self.model_name)
            dtype = torch.float16 if device == "mps" else torch.float32
            model = AutoModelForCausalLM.from_pretrained(self.model_name, torch_dtype=dtype)
            model.to(device)
            model.eval()
            self._models[self.model_name] = (tokenizer, model, device)
        return self._models[self.model_name]

    @staticmethod
    def _build_prompt(title: str, text: str, kind: str) -> str:
        rules = (
            "규칙:\n"
            "- 본문에 없는 사실/숫자는 절대 만들지 마라. 금액·비율·증감은 원문 그대로 유지하라.\n"
            "- 표의 핵심 수치(매출, 영업이익, 증감율, 취득금액 등)와 방향(증가/감소/흑자/적자)을 자연어로 풀어라.\n"
            "- key_points 는 최대 3개, 각 40자 이내.\n"
            "- evidence 는 판단 근거가 된 핵심 수치만 1~2개, 각 40자 이내로 짧게 인용하라(표 전체를 붙여넣지 마라).\n"
            "- JSON 외 다른 텍스트(설명/코드펜스)는 출력하지 마라.\n"
        )
        if kind == SOURCE_TYPE_FILING:
            schema = (
                '{"summary": "한 문장 요약", '
                '"event_type": "잠정실적|손익구조변경|유상증자|자사주취득|단일판매공급계약|배당|소송|횡령배임|기타", '
                '"sentiment": "positive|negative|neutral|mixed", '
                '"impact_score": -2~2 사이 정수, '
                '"key_points": ["핵심 근거"], "risks": ["리스크"], "evidence": ["원문 인용"]}'
            )
        else:
            schema = (
                '{"summary": "한 문장 요약", '
                '"key_points": ["핵심 근거"], "risks": ["리스크"], "evidence": ["원문 인용"]}'
            )
        return (
            "다음 자료를 분석해 아래 스키마의 JSON 한 개만 출력하라.\n"
            f"{rules}"
            f"출력 스키마: {schema}\n\n"
            f"제목: {title}\n"
            f"본문: {text[:6000]}\n\n"
            "JSON:"
        )

    @staticmethod
    def _parse_json(raw: str) -> dict[str, Any] | None:
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```(?:json)?", "", cleaned).strip().rstrip("`").strip()
        start = cleaned.find("{")
        if start == -1:
            return None
        snippet = cleaned[start:]
        end = snippet.rfind("}")
        if end != -1:
            try:
                parsed = json.loads(snippet[: end + 1])
                if isinstance(parsed, dict):
                    return parsed
            except (ValueError, TypeError):
                pass
        # 토큰 한도로 잘린(닫는 괄호 없는) JSON 복구 시도
        repaired = _salvage_truncated_json(snippet)
        if repaired is None:
            return None
        try:
            parsed = json.loads(repaired)
        except (ValueError, TypeError):
            return None
        return parsed if isinstance(parsed, dict) else None


class ExtractiveSummaryBuilder:
    def build(self, *, title: str, text: str) -> tuple[str, list[str], list[str]]:
        sentences = self.meaningful_sentences(text)
        first_sentence = sentences[0] if sentences else ""
        numeric_sentence = self._find_numeric_sentence(sentences)
        parts = [title.strip(), first_sentence]
        if numeric_sentence and numeric_sentence != first_sentence:
            parts.append(numeric_sentence)
        summary = " ".join(part for part in parts if part).strip()[:800]
        key_points = [
            sentence
            for sentence in sentences
            if self._contains_any(sentence, KEY_POINT_KEYWORDS + POSITIVE_KEYWORDS)
        ][:4]
        risks = [sentence for sentence in sentences if self._contains_any(sentence, NEGATIVE_KEYWORDS)][:4]
        return summary or title.strip() or text[:180].strip(), key_points, risks

    def clean_text(self, text: str) -> str:
        return "\n".join(self.meaningful_sentences(text))

    def meaningful_sentences(self, text: str) -> list[str]:
        raw_sentences = self._split_sentences(text)
        return [sentence for sentence in raw_sentences if not self._is_dart_boilerplate(sentence)]

    @staticmethod
    def _split_sentences(text: str) -> list[str]:
        if not text.strip():
            return []
        result: list[str] = []
        for line in text.splitlines():
            line = re.sub(r"\s+", " ", line).strip()
            if not line:
                continue
            for part in re.split(r"(?<=[.!?。！？])\s+", line):
                part = part.strip()
                if len(part) >= 8:
                    result.append(part)
        return result

    @staticmethod
    def _contains_any(text: str, keywords: tuple[str, ...]) -> bool:
        lower = text.lower()
        return any(keyword.lower() in lower for keyword in keywords)

    @staticmethod
    def _is_dart_boilerplate(sentence: str) -> bool:
        normalized = re.sub(r"\s+", " ", sentence).strip()
        return any(pattern in normalized for pattern in DART_BOILERPLATE_PATTERNS)

    def _find_numeric_sentence(self, sentences: list[str]) -> str:
        money_or_ratio_pattern = re.compile(r"\d+(?:,\d{3})*(?:\.\d+)?\s*(?:조|억|만|천)?\s*(?:원|%)")
        for sentence in sentences:
            if money_or_ratio_pattern.search(sentence):
                return sentence
        # 테이블 직렬화 행: 매출액/영업이익 + 쉼표 구분 숫자 (백만원 단위 잠정실적 등)
        table_financials_pattern = re.compile(
            r"(?:매출액|영업이익|당기순이익|순이익).*\d{3,}(?:,\d{3})+"
        )
        for sentence in sentences:
            if table_financials_pattern.search(sentence):
                return sentence
        amount_pattern = re.compile(r"\d+(?:,\d{3})*(?:\.\d+)?\s*(?:조|억|만|천)?\s*(?:주|명|배)")
        for sentence in sentences:
            if amount_pattern.search(sentence):
                return sentence
        return next((sentence for sentence in sentences if self._contains_any(sentence, KEY_POINT_KEYWORDS)), "")


class InvestmentImpactRuleEngine:
    def apply(
        self,
        *,
        title: str,
        text: str,
        model_sentiment: str | None,
        model_confidence: float | None,
        model_impact: int | None = None,
    ) -> InvestmentImpactDecision:
        title_lower = title.lower()
        lower = text.lower()
        haystack = f"{title_lower}\n{lower}"
        positive_hits = [keyword for keyword in POSITIVE_KEYWORDS if keyword.lower() in haystack]
        negative_hits = [keyword for keyword in NEGATIVE_KEYWORDS if keyword.lower() in haystack]
        confidence = model_confidence or 0.62

        # 1. 명확한 사건 제목 먼저 (본문 우연 문구보다 우선)
        if any(keyword.lower() in title_lower for keyword in HARD_NEGATIVE_TITLE_KEYWORDS):
            impact = -2 if len(negative_hits) >= 2 else -1
            return self._decision(
                "negative", impact, confidence, "hard_negative_title", positive_hits, negative_hits
            )
        if any(keyword.lower() in title_lower for keyword in HARD_POSITIVE_TITLE_KEYWORDS):
            impact = 2 if len(positive_hits) >= 2 else 1
            return self._decision(
                "positive", impact, confidence, "hard_positive_title", positive_hits, negative_hits
            )
        if any(keyword.lower() in title_lower for keyword in MIXED_EVENT_TITLE_KEYWORDS):
            return self._decision("mixed", 1, confidence, "mixed_event_title", positive_hits, negative_hits)
        # 2. 사건 제목이 아닐 때만 본문 치명 문구 검사 (부정문 체크리스트는 제외)
        if _critical_negative_present(text):
            return self._decision("negative", -2, confidence, "critical_negative_event", positive_hits, negative_hits)
        # 3. 행정 제목 기본 neutral
        if any(keyword.lower() in title_lower for keyword in NEUTRAL_ADMIN_TITLE_KEYWORDS):
            return self._decision("neutral", 0, confidence, "admin_default", positive_hits, negative_hits)

        base_sentiment = model_sentiment if model_sentiment in VALID_SENTIMENTS else "neutral"
        # Qwen 등 모델이 강도(impact)를 직접 주면 그 부호가 sentiment와 일치할 때 사용(예: 영업이익 급감 → -2).
        default_impact = {"positive": 1, "negative": -1, "neutral": 0, "mixed": 0}[base_sentiment]
        if (
            model_impact is not None
            and ((base_sentiment == "positive" and model_impact > 0)
                 or (base_sentiment == "negative" and model_impact < 0)
                 or base_sentiment in ("neutral", "mixed"))
        ):
            base_impact = max(-2, min(2, int(model_impact)))
        else:
            base_impact = default_impact

        if positive_hits and negative_hits:
            if len(positive_hits) > len(negative_hits):
                adjustment = 1
            elif len(negative_hits) > len(positive_hits):
                adjustment = -1
            else:
                adjustment = 0
            impact = max(-1, min(1, base_impact + adjustment))
            return self._decision("mixed", impact, confidence, "mixed_keyword_adjustment", positive_hits, negative_hits)
        if positive_hits:
            if base_sentiment == "negative":
                return self._decision("mixed", 0, confidence, "model_rule_conflict", positive_hits, negative_hits)
            impact = 2 if len(positive_hits) >= 2 else 1
            return self._decision(
                "positive", impact, confidence, "positive_keyword_adjustment", positive_hits, negative_hits
            )
        if negative_hits:
            if base_sentiment == "positive":
                return self._decision("mixed", 0, confidence, "model_rule_conflict", positive_hits, negative_hits)
            impact = -2 if len(negative_hits) >= 2 else -1
            return self._decision(
                "negative", impact, confidence, "negative_keyword_adjustment", positive_hits, negative_hits
            )
        if model_sentiment in VALID_SENTIMENTS:
            return self._decision(base_sentiment, base_impact, confidence, "model", positive_hits, negative_hits)
        return self._decision("neutral", 0, confidence, "default", positive_hits, negative_hits)

    @staticmethod
    def _decision(
        sentiment: str,
        impact_score: int,
        confidence: float,
        source: str,
        positive_hits: list[str],
        negative_hits: list[str],
    ) -> InvestmentImpactDecision:
        return InvestmentImpactDecision(
            sentiment=sentiment,
            impact_score=impact_score,
            confidence=max(0.0, min(1.0, confidence)),
            source=source,
            positive_hits=positive_hits,
            negative_hits=negative_hits,
        )


class EvidenceAnalysisService:
    def __init__(
        self,
        session: Session | None = None,
        *,
        repository: EvidenceAnalysisRepository | None = None,
        sentiment_analyzer: LocalHFSentimentAnalyzer | None = None,
        analyzer: LocalQwenEvidenceAnalyzer | None = None,
    ) -> None:
        self.settings = get_settings()
        self.session = session
        self.repository = repository or (EvidenceAnalysisRepository(session) if session is not None else None)
        self.sentiment_analyzer = sentiment_analyzer or LocalHFSentimentAnalyzer(self.settings.analysis_model)
        self.analyzer = analyzer
        if self.analyzer is None and self.settings.analysis_generation_model:
            self.analyzer = LocalQwenEvidenceAnalyzer(self.settings.analysis_generation_model)
        self.summary_builder = ExtractiveSummaryBuilder()
        self.rule_engine = InvestmentImpactRuleEngine()

    # ------------------------------------------------------------------
    # Qwen 가용성 / 게이팅
    # ------------------------------------------------------------------
    @property
    def qwen_available(self) -> bool:
        return bool(
            self.settings.analysis_enabled
            and self.settings.analysis_generation_model
            and self.analyzer is not None
        )

    def filing_needs_qwen(self, title: str, text: str) -> bool:
        """공시 Qwen 보강 게이트: 표 본문 + 실적/손익 유형 제목만(룰 제목 제외)."""
        if not self.qwen_available:
            return False
        if _title_has_rule(title):
            return False
        cleaned = self.summary_builder.clean_text(text or title)
        if not _is_table_heavy(cleaned):
            return False
        lowered = title.replace(" ", "").lower()
        return any(keyword.replace(" ", "").lower() in lowered for keyword in FILING_QWEN_TITLE_KEYWORDS)

    def news_needs_qwen(self, baseline: EvidenceAnalysisResult) -> bool:
        """뉴스 Qwen 보강 게이트: FinBERT 비-neutral 또는 |impact|>=임계."""
        if not self.qwen_available:
            return False
        if baseline.sentiment != "neutral":
            return True
        return abs(baseline.impact_score) >= self.settings.analysis_news_qwen_min_impact

    # ------------------------------------------------------------------
    # baseline (동기: 룰 + FinBERT, Qwen 없음)
    # ------------------------------------------------------------------
    def analyze_news_row(
        self, row: NewsCache, *, content: str | None = None, persist: bool = True
    ) -> EvidenceAnalysisResult:
        text = content or row.content or row.summary or ""
        return self.analyze_text(
            source_type=SOURCE_TYPE_NEWS, symbol=row.symbol, title=row.title,
            text=text, source_id=row.id, persist=persist, use_qwen=False,
        )

    def analyze_filing_row(
        self, row: FilingCache, content: str | None = None, *, persist: bool = True
    ) -> EvidenceAnalysisResult:
        text = content or row.content or row.summary or ""
        return self.analyze_text(
            source_type=SOURCE_TYPE_FILING, symbol=row.symbol, title=row.filing_title,
            text=text, source_id=row.id, persist=persist, use_qwen=False,
        )

    # ------------------------------------------------------------------
    # enrich (비동기 워커: Qwen 구조화 보강)
    # ------------------------------------------------------------------
    def enrich_news_row(
        self, row: NewsCache, *, content: str | None = None, persist: bool = True
    ) -> EvidenceAnalysisResult:
        text = content or row.content or row.summary or ""
        return self.analyze_text(
            source_type=SOURCE_TYPE_NEWS, symbol=row.symbol, title=row.title,
            text=text, source_id=row.id, persist=persist, use_qwen=True,
        )

    def enrich_filing_row(
        self, row: FilingCache, content: str | None = None, *, persist: bool = True
    ) -> EvidenceAnalysisResult:
        text = content or row.content or row.summary or ""
        return self.analyze_text(
            source_type=SOURCE_TYPE_FILING, symbol=row.symbol, title=row.filing_title,
            text=text, source_id=row.id, persist=persist, use_qwen=True,
        )

    def analyze_text(
        self,
        *,
        source_type: str,
        symbol: str,
        title: str,
        text: str,
        source_id: str | UUID,
        persist: bool = True,
        use_qwen: bool = False,
    ) -> EvidenceAnalysisResult:
        cleaned_text = self.summary_builder.clean_text(text or title)
        analysis_text = f"{title}\n{cleaned_text}".strip()[: self.settings.analysis_max_chars]
        if not analysis_text:
            analysis_text = title
        summary, key_points, risks = self.summary_builder.build(title=title, text=text or title)
        summary_provider = self.settings.analysis_summary_provider
        event_type: str | None = None
        evidence: list[str] = []

        # 1) FinBERT baseline (raw 입력)
        model_sentiment: str | None = None
        model_confidence: float | None = None
        model_impact: int | None = None
        hf_raw: dict[str, Any] = {"status": "disabled"}
        model_name = "rule-only-extractive"
        if self.settings.analysis_enabled and self.settings.analysis_provider == "local_hf":
            model_sentiment, model_confidence, hf_raw = self.sentiment_analyzer.analyze(analysis_text)
            if hf_raw.get("status") == "pass":
                model_name = self.settings.analysis_model

        # 2) Qwen 구조화 보강 (게이트 통과 시)
        qwen_raw: dict[str, Any] = {"status": "disabled"}
        gate = False
        if use_qwen and self.qwen_available:
            if source_type == SOURCE_TYPE_FILING:
                gate = self.filing_needs_qwen(title, text or title)
            else:
                gate = model_sentiment not in (None, "neutral")
        if gate and self.analyzer is not None:
            # A) 입력 트리밍: 공시 표는 신호 행만 추려 prefill 축소(속도) + 헤더 덤프 유혹 감소(품질)
            llm_input = _trim_table_for_llm(text or title) if source_type == SOURCE_TYPE_FILING else (text or title)
            parsed = self.analyzer.analyze(title, llm_input, kind=source_type)
            if parsed:
                original = text or title  # grounding 은 항상 전체 원문 기준
                cand_summary = _grounded_text(original, str(parsed.get("summary", "")).strip())
                # B) 출력 노이즈 필터: grounding 통과해도 표 헤더/셀 덤프는 근거가 아니므로 제거
                cand_keys = _filter_noise(_grounded_list(original, parsed.get("key_points")))
                cand_risks = _filter_noise(_grounded_list(original, parsed.get("risks")))
                cand_evidence = _filter_noise(_grounded_list(original, parsed.get("evidence")))
                if cand_summary:
                    summary = f"{title} {cand_summary}".strip()[:800]
                    summary_provider = "generative"
                if cand_keys:
                    key_points = cand_keys
                if cand_risks:
                    risks = cand_risks
                evidence = cand_evidence
                consistency: str | None = None
                if source_type == SOURCE_TYPE_FILING:
                    qwen_sentiment = str(parsed.get("sentiment", "")).strip()
                    if qwen_sentiment in VALID_SENTIMENTS:
                        # C) 일관성 가드: 감성과 근거 방향이 모순이면 mixed/0 으로 강등
                        qwen_sentiment, consistency = _consistency_guard(
                            qwen_sentiment, cand_keys + cand_evidence
                        )
                        model_sentiment = qwen_sentiment
                        model_name = self.settings.analysis_generation_model or model_name
                    raw_impact = parsed.get("impact_score")
                    if isinstance(raw_impact, (int, float)):
                        model_impact = 0 if consistency == "conflict" else max(-2, min(2, int(raw_impact)))
                    event_type = _normalize_event_type(parsed.get("event_type"))
                qwen_raw = {
                    "status": "pass",
                    "model": self.settings.analysis_generation_model,
                    "event_type": parsed.get("event_type"),
                    "sentiment": parsed.get("sentiment"),
                    "impact_score": parsed.get("impact_score"),
                    "summary_grounded": bool(cand_summary),
                    "consistency": consistency,
                    "llm_input_chars": len(llm_input),
                }
            else:
                qwen_raw = {"status": "fallback", "failure_reason": "qwen_no_output"}
        elif use_qwen and self.qwen_available:
            qwen_raw = {"status": "skipped", "reason": "gate_not_passed"}

        # 3) 룰엔진 보정 (정책/치명 hard override 우선)
        decision = self.rule_engine.apply(
            title=title,
            text=cleaned_text,
            model_sentiment=model_sentiment,
            model_confidence=model_confidence,
            model_impact=model_impact,
        )
        sentiment = decision.sentiment if decision.sentiment in VALID_SENTIMENTS else "neutral"
        impact_score = max(-2, min(2, int(decision.impact_score)))
        confidence = decision.confidence

        raw_response: dict[str, Any] = {
            "status": "pass",
            "provider": self.settings.analysis_provider,
            "summary_provider": summary_provider,
            "hf": hf_raw,
            "generative": qwen_raw,
            "model_sentiment": model_sentiment,
            "model_impact": model_impact,
            "decision": decision.to_dict(),
            "components": {
                "summary": {"status": "pass"},
                "classification": {"status": "pass"},
            },
        }

        # 4) 하네스 검증
        try:
            _validate_summary(summary=summary, title=title)
        except HarnessValidationError as exc:
            summary = title.strip()
            key_points = []
            risks = []
            evidence = []
            raw_response["status"] = "fallback"
            raw_response["failure_reason"] = str(exc)
            raw_response["fallback_components"] = ["summary"]
            raw_response["components"]["summary"] = {"status": "fallback", "failure_reason": str(exc)}

        try:
            _validate_sentiment_impact(sentiment=sentiment, impact_score=impact_score)
        except HarnessValidationError as exc:
            sentiment = "neutral"
            impact_score = 0
            confidence = 0.62
            raw_response["status"] = "fallback"
            raw_response["failure_reason"] = str(exc)
            raw_response.setdefault("fallback_components", []).append("classification")
            raw_response["components"]["classification"] = {"status": "fallback", "failure_reason": str(exc)}

        result = EvidenceAnalysisResult(
            source_type=source_type,
            source_id=str(source_id),
            symbol=symbol,
            sentiment=sentiment,
            impact_score=impact_score,
            confidence=max(0.0, min(1.0, confidence)) if confidence is not None else None,
            summary=summary,
            key_points=_dedupe_strings(key_points),
            risks=_dedupe_strings(risks),
            model_name=model_name,
            prompt_version=self.settings.analysis_prompt_version,
            event_type=event_type,
            evidence=_dedupe_strings(evidence),
            raw_response=raw_response,
        )

        if persist and self.repository is not None:
            self.repository.upsert_analysis(**result.to_dict())
        return result


def _validate_analysis_result(
    *,
    summary: str,
    sentiment: str,
    impact_score: int,
    title: str,
) -> None:
    """Convert obvious semantic failures into explicit fallback reasons."""
    _validate_summary(summary=summary, title=title)
    _validate_sentiment_impact(sentiment=sentiment, impact_score=impact_score)


def _validate_summary(*, summary: str, title: str) -> None:
    normalized_summary = re.sub(r"\s+", " ", summary).strip()
    normalized_title = re.sub(r"\s+", " ", title).strip()

    pipe_density = normalized_summary.count("|") / max(len(normalized_summary), 1) * 100
    if len(normalized_summary) > 300 and pipe_density > 3.0:
        raise HarnessValidationError("err_table_header_noise")
    if not normalized_summary or normalized_summary == normalized_title:
        raise HarnessValidationError("err_empty_summary")


def _validate_sentiment_impact(*, sentiment: str, impact_score: int) -> None:
    if sentiment == "positive" and impact_score < 0:
        raise HarnessValidationError("err_sentiment_impact_mismatch")
    if sentiment == "negative" and impact_score > 0:
        raise HarnessValidationError("err_sentiment_impact_mismatch")


def _is_table_heavy(text: str) -> bool:
    """본문의 40% 이상이 직렬화된 표 행(파이프 포함)이면 표 본문으로 본다."""
    lines = [line for line in text.splitlines() if line.strip()]
    if len(lines) < 3:
        return False
    table_lines = sum(1 for line in lines if "|" in line)
    return table_lines / len(lines) > 0.4


def _title_has_rule(title: str) -> bool:
    """제목이 하드/믹스드/행정 규칙에 걸리면 True (이 경우 룰엔진이 결정하므로 Qwen 불필요)."""
    lowered = title.lower()
    groups = (
        HARD_NEGATIVE_TITLE_KEYWORDS,
        HARD_POSITIVE_TITLE_KEYWORDS,
        MIXED_EVENT_TITLE_KEYWORDS,
        NEUTRAL_ADMIN_TITLE_KEYWORDS,
    )
    return any(keyword.lower() in lowered for group in groups for keyword in group)


def _normalize_event_type(value: Any) -> str | None:
    """Qwen이 낸 event_type을 허용 집합으로 정규화. 미일치는 '기타', 빈 값은 None."""
    if not value:
        return None
    text_value = re.sub(r"\s+", "", str(value)).strip()
    if not text_value:
        return None
    return text_value if text_value in EVENT_TYPES else "기타"


def _grounded_text(original_text: str, candidate: str) -> str | None:
    """수치 grounding을 통과한 후보 문자열만 반환(환각 차단). 실패 시 None."""
    candidate = candidate.strip()
    if not candidate:
        return None
    return candidate if _verify_numerical_grounding(original_text, candidate) else None


def _grounded_list(original_text: str, values: Any) -> list[str]:
    """리스트 항목 중 수치 grounding을 통과한 것만 남긴다."""
    if not isinstance(values, list):
        return []
    result: list[str] = []
    for value in values:
        text_value = str(value).strip()
        if text_value and _verify_numerical_grounding(original_text, text_value):
            result.append(text_value)
    return result


# 표 헤더/셀 덤프로 의심되는 출력 조각 패턴(분석 근거가 아님)
_NOISE_FRAGMENT_TOKENS = (
    "단위 :", "단위:", "전기대비", "전년동기대비", "증감율(%)", "흑자적자전환여부",
    "연결실적내용:", "당기실적:", "재무제표의 종류", "결산대상기간",
)
_DIR_POSITIVE = ("증가", "흑자", "개선", "상승", "성장", "확대", "호조")
_DIR_NEGATIVE = ("감소", "적자", "손실", "악화", "하락", "축소", "둔화", "부진")
_TRIM_SIGNAL_KEYWORDS = (
    "매출액", "매출", "영업이익", "영업손실", "당기순이익", "순이익", "순손실",
    "당기순손실", "증감", "증가", "감소", "흑자", "적자", "손익", "취득금액",
    "계약금액", "공급계약", "배당", "자기주식", "자사주",
)


def _trim_table_for_llm(text: str, *, max_chars: int = 2500, min_chars: int = 300) -> str:
    """공시 표 본문에서 신호 있는 행만 추려 LLM 입력 길이를 줄인다(prefill 단축).

    너무 적게 남으면(min_chars 미만) 신호가 약한 공시이므로 원문 앞부분으로 폴백한다.
    """
    kept: list[str] = []
    total = 0
    for line in text.splitlines():
        s = line.strip()
        if not s:
            continue
        low = s.lower()
        has_signal = any(k.lower() in low for k in _TRIM_SIGNAL_KEYWORDS)
        has_ratio = "%" in s and any(ch.isdigit() for ch in s)
        if has_signal or has_ratio:
            kept.append(s)
            total += len(s)
            if total >= max_chars:
                break
    trimmed = "\n".join(kept)
    if len(trimmed) < min_chars:
        return text[:6000]
    return trimmed[:max_chars]


def _is_noise_fragment(value: str) -> bool:
    """표 헤더/셀 덤프로 보이는 조각이면 True(분석 근거로 부적합)."""
    s = value.strip()
    if not s:
        return True
    if any(tok in s for tok in _NOISE_FRAGMENT_TOKENS):
        return True
    if s.count("|") >= 2:  # 직렬화된 표 셀 행
        return True
    hangul = sum(1 for ch in s if "가" <= ch <= "힣")
    return hangul < 2  # 한글 서술이 거의 없으면 노이즈


def _filter_noise(values: list[str]) -> list[str]:
    return [v for v in values if not _is_noise_fragment(v)]


def _consistency_guard(sentiment: str, basis: list[str]) -> tuple[str, str | None]:
    """감성과 근거(key_points/evidence) 방향이 모순이면 mixed 로 강등.

    반환: (보정된 sentiment, 'conflict' | None).
    """
    joined = " ".join(basis)
    pos = any(k in joined for k in _DIR_POSITIVE)
    neg = any(k in joined for k in _DIR_NEGATIVE)
    if sentiment == "negative" and pos and not neg:
        return "mixed", "conflict"
    if sentiment == "positive" and neg and not pos:
        return "mixed", "conflict"
    return sentiment, None


def _salvage_truncated_json(snippet: str) -> str | None:
    """토큰 한도로 잘려 닫는 괄호가 없는 JSON을, 마지막 완성된 값까지 잘라 닫아 복구한다.

    문자열 상태/괄호 스택을 추적해 '요소 사이'로 안전하게 자를 수 있는 마지막 지점을 찾고,
    뒤따르는 미완성 토큰·콤마를 버린 뒤 열린 괄호를 역순으로 닫는다.
    """
    in_str = False
    esc = False
    stack: list[str] = []
    cut: int | None = None
    cut_stack: list[str] | None = None
    for i, ch in enumerate(snippet):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
                cut, cut_stack = i + 1, list(stack)
            continue
        if ch == '"':
            in_str = True
        elif ch in "{[":
            stack.append("}" if ch == "{" else "]")
        elif ch in "}]":
            if stack:
                stack.pop()
            cut, cut_stack = i + 1, list(stack)
        elif ch.isdigit() or ch in "eltrufalsn.+-":  # 숫자/true/false/null 토큰 끝
            nxt = snippet[i + 1] if i + 1 < len(snippet) else ""
            if nxt in ",}] \n\t" or nxt == "":
                cut, cut_stack = i + 1, list(stack)
    if cut is None or cut_stack is None:
        return None
    repaired = snippet[:cut].rstrip().rstrip(",")
    repaired += "".join(reversed(cut_stack))
    return repaired


def _verify_numerical_grounding(original_text: str, summary_text: str) -> bool:
    """요약문의 숫자 토큰이 원문에 그대로 존재하는지 검증 (생성형 환각 방지)."""
    clean_original = re.sub(r"[\s,]", "", original_text)
    clean_summary = re.sub(r"[\s,]", "", summary_text)
    tokens = re.findall(r"\d+(?:\.\d+)?[조억만천원%년월일주명배]+", clean_summary)
    for token in tokens:
        pattern = r"(?<!\d)" + re.escape(token) + r"(?!\d)"
        if not re.search(pattern, clean_original):
            return False
    return True


def _dedupe_strings(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        normalized = re.sub(r"\s+", " ", str(value)).strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
    return result
