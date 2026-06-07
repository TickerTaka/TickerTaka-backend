from __future__ import annotations

from app.domain.evidence_retrieval import format_evidence_context


def main() -> None:
    context = format_evidence_context(
        [
            {
                "source_type": "DART",
                "source_title": "유상증자결정",
                "source_label": "DART",
                "source_url": "https://dart.example.com",
                "excerpt": "조달금액은 3,000억원이며 기존 주주의 지분이 희석된다.",
                "sentiment": "negative",
                "impact_score": -2,
                "analysis_summary": "유상증자결정 주식회사 OO은 운영자금 조달을 목적으로 유상증자를 결정했다.",
                "key_points": ["3,000억원 유상증자", "운영자금 조달"],
                "risks": ["기존 주주 지분 희석"],
            }
        ]
    )
    assert "[negative]" in context
    assert "[영향도 -2]" in context
    assert "핵심 근거:" in context
    assert "리스크:" in context
    assert "원문 발췌:" in context
    print(context)


if __name__ == "__main__":
    main()
