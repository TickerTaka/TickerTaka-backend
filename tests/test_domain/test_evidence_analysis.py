from __future__ import annotations

import pytest

from app.domain.evidence_analysis import (
    EvidenceAnalysisService,
    HarnessValidationError,
    _validate_analysis_result,
)


class DummySentimentAnalyzer:
    def analyze(self, text: str) -> tuple[None, None, dict[str, str]]:
        return None, None, {"status": "disabled"}


class StaticSentimentAnalyzer:
    def __init__(self, sentiment: str, confidence: float = 0.91) -> None:
        self.sentiment = sentiment
        self.confidence = confidence
        self.last_text = ""

    def analyze(self, text: str) -> tuple[str, float, dict[str, object]]:
        self.last_text = text
        return self.sentiment, self.confidence, {
            "status": "pass",
            "label": self.sentiment,
            "score": self.confidence,
        }


def test_analyze_text_falls_back_for_table_header_noise() -> None:
    title = "임원ㆍ주요주주특정증권등소유상황보고서"
    noisy_row = (
        "| 보고자 | 회사와의 관계 | 주식등의 종류 | 변동 전 | 변동 후 | 변동일 | "
        "취득 단가 | 취득 금액 | 보유 비율 | 비고 | "
    )
    text = noisy_row * 8
    service = EvidenceAnalysisService(sentiment_analyzer=DummySentimentAnalyzer())

    result = service.analyze_text(
        source_type="filing",
        source_id="b4ef8cb9-4254-4d6b-9603-9291795e90a9",
        symbol="000000",
        title=title,
        text=text,
        persist=False,
    )

    assert result.summary == title
    assert result.sentiment == "neutral"
    assert result.impact_score == 0
    assert result.key_points == []
    assert result.risks == []
    assert result.raw_response is not None
    assert result.raw_response["status"] == "fallback"
    assert result.raw_response["failure_reason"] == "err_table_header_noise"


def test_analyze_text_passes_meaningful_numeric_filing_summary() -> None:
    title = "타법인주식및출자증권취득결정"
    text = (
        "기아 주식회사는 전략적 투자를 위해 타법인 주식을 취득합니다. "
        "취득금액은 2조 3,634억원입니다."
    )
    service = EvidenceAnalysisService(sentiment_analyzer=DummySentimentAnalyzer())

    result = service.analyze_text(
        source_type="filing",
        source_id="c2a3c70c-6d95-4f97-a31e-1cf28ffefe67",
        symbol="000270",
        title=title,
        text=text,
        persist=False,
    )

    assert result.summary.startswith(title)
    assert result.sentiment == "mixed"
    assert result.impact_score == 1
    assert result.raw_response is not None
    assert result.raw_response["status"] == "pass"
    assert "failure_reason" not in result.raw_response


def test_summary_fallback_preserves_classification() -> None:
    title = "타법인주식및출자증권취득결정"
    meaningful_table_row = (
        "발행회사: 에이치엠지퓨처콤플렉스 | "
        "취득목적: 미래 모빌리티 연구개발 거점 확보 | "
        "취득금액: 2조 3,634억원 | 자기자본 대비: 3.9% | 취득주식수: 100,000주 | "
    )
    service = EvidenceAnalysisService(sentiment_analyzer=StaticSentimentAnalyzer("positive"))

    result = service.analyze_text(
        source_type="filing",
        source_id="7fe2a609-cf14-4b17-948b-51a1f65fa1cc",
        symbol="000270",
        title=title,
        text=meaningful_table_row * 8,
        persist=False,
    )

    assert result.summary == title
    assert result.sentiment == "mixed"
    assert result.impact_score == 1
    assert result.raw_response is not None
    assert result.raw_response["status"] == "fallback"
    assert result.raw_response["fallback_components"] == ["summary"]
    assert result.raw_response["components"]["classification"]["status"] == "pass"


def test_critical_event_overrides_admin_default() -> None:
    service = EvidenceAnalysisService(sentiment_analyzer=StaticSentimentAnalyzer("neutral"))

    result = service.analyze_text(
        source_type="filing",
        source_id="8a49ba8b-c0e4-4293-8dd7-e7e0d36e5521",
        symbol="000000",
        title="임원ㆍ주요주주특정증권등소유상황보고서",
        text="담보권 실행으로 최대주주 변경과 반대매매가 발생했습니다.",
        persist=False,
    )

    assert result.sentiment == "negative"
    assert result.impact_score == -2
    assert result.raw_response is not None
    assert result.raw_response["decision"]["source"] == "critical_negative_event"


def test_soft_keyword_conflict_becomes_mixed() -> None:
    service = EvidenceAnalysisService(sentiment_analyzer=StaticSentimentAnalyzer("positive"))

    result = service.analyze_text(
        source_type="news",
        source_id="3d4c4515-daa2-4420-af4b-a32e8cf868ea",
        symbol="000000",
        title="분기 실적 전망",
        text="시장 기대는 높지만 매출 감소 가능성이 제기됐습니다.",
        persist=False,
    )

    assert result.sentiment == "mixed"
    assert result.impact_score == 0
    assert result.raw_response is not None
    assert result.raw_response["decision"]["source"] == "model_rule_conflict"


def test_hard_negative_title_overrides_model() -> None:
    service = EvidenceAnalysisService(sentiment_analyzer=StaticSentimentAnalyzer("positive"))

    result = service.analyze_text(
        source_type="filing",
        source_id="780902eb-51e4-48af-90cb-aab8e838304d",
        symbol="000000",
        title="유상증자결정",
        text="회사는 운영자금 확보를 위해 신주 발행을 결정했습니다.",
        persist=False,
    )

    assert result.sentiment == "negative"
    assert result.impact_score == -1
    assert result.raw_response is not None
    assert result.raw_response["decision"]["source"] == "hard_negative_title"


def test_sentiment_analyzer_receives_cleaned_text() -> None:
    analyzer = StaticSentimentAnalyzer("neutral")
    service = EvidenceAnalysisService(sentiment_analyzer=analyzer)

    service.analyze_text(
        source_type="filing",
        source_id="60ecc915-fd79-479b-9db5-26f7a0df9516",
        symbol="000000",
        title="기타경영사항",
        text=(
            "금융위원회 귀중. 허위기재 또는 기재누락이 없음을 확인합니다. "
            "회사는 신규 사업을 검토합니다."
        ),
        persist=False,
    )

    assert "금융위원회 귀중" not in analyzer.last_text
    assert "허위기재 또는 기재누락" not in analyzer.last_text
    assert "회사는 신규 사업을 검토합니다." in analyzer.last_text


def test_validate_analysis_result_rejects_empty_summary() -> None:
    with pytest.raises(HarnessValidationError, match="err_empty_summary"):
        _validate_analysis_result(
            summary="주주총회소집공고",
            sentiment="neutral",
            impact_score=0,
            title="주주총회소집공고",
        )


def test_validate_analysis_result_rejects_sentiment_impact_mismatch() -> None:
    with pytest.raises(HarnessValidationError, match="err_sentiment_impact_mismatch"):
        _validate_analysis_result(
            summary="매출 증가에도 비용 부담이 남아 있습니다.",
            sentiment="positive",
            impact_score=-1,
            title="분기 실적 발표",
        )
