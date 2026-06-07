from __future__ import annotations

from uuid import uuid4

from app.domain.evidence_analysis import EvidenceAnalysisService


class NullSentimentAnalyzer:
    def analyze(self, text: str):
        return None, None, {"status": "disabled-for-validation"}


def main() -> None:
    service = EvidenceAnalysisService(session=None, sentiment_analyzer=NullSentimentAnalyzer())
    result = service.analyze_text(
        source_type="filing",
        symbol="000000",
        title="유상증자결정",
        text="주식회사 OO은 운영자금 조달을 목적으로 보통주 5,000만주를 발행하는 유상증자를 결정했다. 조달금액은 3,000억원이며 기존 주주의 지분이 희석된다.",
        source_id=uuid4(),
        persist=False,
    )
    payload = result.to_dict()
    assert payload["sentiment"] == "negative"
    assert payload["impact_score"] <= -1
    assert "3,000억원" in payload["summary"]
    assert payload["risks"]
    print(payload)


if __name__ == "__main__":
    main()
