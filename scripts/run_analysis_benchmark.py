from __future__ import annotations

from uuid import uuid4

from app.domain.evidence_analysis import EvidenceAnalysisService


TEST_CASES = [
    {
        "title": "타법인주식및출자증권취득결정",
        "text": "기아 주식회사는 에이치엠지퓨처콤플렉스 주식회사에 대한 타법인 주식 취득을 결정했다. 취득금액은 2조 3,634억원으로 자기자본 대비 3.9% 수준이다.",
        "expected_sentiment": "mixed",
        "expected_impact_range": (-1, 2),
        "expected_keywords_in_summary": ["2조 3,634억", "3.9%"],
    },
    {
        "title": "유상증자결정",
        "text": "주식회사 OO은 운영자금 조달을 목적으로 보통주 5,000만주를 발행하는 유상증자를 결정했다. 조달금액은 3,000억원이며 기존 주주의 지분이 희석된다.",
        "expected_sentiment": "negative",
        "expected_impact_range": (-2, 0),
        "expected_keywords_in_summary": ["3,000억원"],
    },
    {
        "title": "임시주주총회 소집결의",
        "text": "2026년 7월 8일 임시주주총회를 개최한다.",
        "expected_sentiment": "neutral",
        "expected_impact_range": (-1, 1),
        "expected_keywords_in_summary": [],
    },
]


class NullSentimentAnalyzer:
    def analyze(self, text: str):
        return None, None, {"status": "disabled-for-benchmark"}


def main() -> None:
    service = EvidenceAnalysisService(session=None, sentiment_analyzer=NullSentimentAnalyzer())
    passed_count = 0
    for case in TEST_CASES:
        result = service.analyze_text(
            source_type="filing",
            symbol="000000",
            title=case["title"],
            text=case["text"],
            source_id=uuid4(),
            persist=False,
        ).to_dict()
        checks = {
            "sentiment_match": result["sentiment"] == case["expected_sentiment"],
            "impact_in_range": case["expected_impact_range"][0] <= result["impact_score"] <= case["expected_impact_range"][1],
            "keywords_in_summary": all(k in result["summary"] for k in case["expected_keywords_in_summary"]),
            "valid_payload": all(k in result for k in ["summary", "sentiment", "impact_score"]),
        }
        passed = all(checks.values())
        passed_count += int(passed)
        print(f"{'PASS' if passed else 'FAIL'}  {case['title'][:30]}  {checks}  {result}")
    print({"passed": passed_count, "total": len(TEST_CASES)})
    if passed_count != len(TEST_CASES):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
