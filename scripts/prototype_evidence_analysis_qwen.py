from __future__ import annotations

import argparse
import json
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


PROMPT_VERSION = "prototype-evidence-analysis-v1"
DEFAULT_MODEL = "Qwen/Qwen2.5-3B-Instruct"
VALID_SENTIMENTS = {"positive", "negative", "neutral", "mixed"}
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
)
SUMMARY_KEYWORDS = (
    "억원",
    "조원",
    "%",
    "주",
    "계약",
    "수주",
    "매출",
    "영업이익",
    "취득금액",
    "자기자본",
    "배당",
    "자사주",
    "유상증자",
)
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
NEUTRAL_ADMIN_TITLE_KEYWORDS = (
    "임원ㆍ주요주주특정증권등소유상황보고서",
    "임원·주요주주특정증권등소유상황보고서",
    "주식등의대량보유상황보고서",
    "주주총회소집공고",
)


@dataclass(slots=True)
class AnalysisResult:
    summary: str
    sentiment: str
    impact_score: int
    confidence: float | None
    key_points: list[str]
    risks: list[str]
    model_name: str
    prompt_version: str
    raw_response: dict[str, Any]
    elapsed_ms: int


@dataclass(slots=True)
class EvidenceInput:
    source_type: str
    symbol: str
    title: str
    text: str
    source_id: str | None = None


TEST_CASES = [
    {
        "source_type": "filing",
        "symbol": "000270",
        "title": "타법인주식및출자증권취득결정",
        "text": (
            "기아 주식회사는 에이치엠지퓨처콤플렉스 주식회사에 대한 타법인 주식 취득을 결정했다. "
            "취득금액은 2조 3,634억원으로 자기자본 대비 3.9% 수준이다. "
            "취득 목적은 미래 모빌리티 관련 연구개발 거점 확보와 그룹 차원의 신사업 투자다."
        ),
    },
    {
        "source_type": "filing",
        "symbol": "000000",
        "title": "유상증자결정",
        "text": (
            "주식회사 OO은 운영자금 조달을 목적으로 보통주 5,000만주를 발행하는 유상증자를 결정했다. "
            "조달금액은 3,000억원이며 기존 주주의 지분이 희석된다."
        ),
    },
    {
        "source_type": "news",
        "symbol": "005930",
        "title": "삼성전자 2분기 영업이익 10조원 돌파 전망",
        "text": (
            "삼성전자는 2026년 2분기 영업이익이 10조원을 돌파할 전망이다. "
            "HBM 수요 증가와 메모리 반도체 가격 상승이 실적 개선의 주요 원인으로 꼽힌다. "
            "다만 일부 모바일 수요 둔화는 단기 리스크로 제기된다."
        ),
    },
]


def split_sentences(text: str) -> list[str]:
    normalized = re.sub(r"\s+", " ", text).strip()
    if not normalized:
        return []
    sentences = re.split(r"(?<=[.!?。！？])\s+", normalized)
    return [sentence.strip() for sentence in sentences if len(sentence.strip()) >= 8]


def extractive_payload(title: str, text: str) -> dict[str, Any]:
    raw_sentences = split_sentences(text)
    sentences = [sentence for sentence in raw_sentences if not is_dart_boilerplate(sentence)]
    first_sentence = sentences[0] if sentences else ""
    numeric_sentence = find_numeric_sentence(sentences)
    summary_parts = [title.strip(), first_sentence]
    if numeric_sentence and numeric_sentence != first_sentence:
        summary_parts.append(numeric_sentence)

    key_points = [
        sentence
        for sentence in sentences
        if any(keyword.lower() in sentence.lower() for keyword in SUMMARY_KEYWORDS + POSITIVE_KEYWORDS)
    ][:3]
    risks = [
        sentence
        for sentence in sentences
        if any(keyword.lower() in sentence.lower() for keyword in NEGATIVE_KEYWORDS)
    ][:3]

    return {
        "summary": " ".join(part for part in summary_parts if part)[:700] or title.strip() or text[:160].strip(),
        "key_points": key_points,
        "risks": risks,
    }


def is_dart_boilerplate(sentence: str) -> bool:
    normalized = re.sub(r"\s+", " ", sentence).strip()
    return any(pattern in normalized for pattern in DART_BOILERPLATE_PATTERNS)


def find_numeric_sentence(sentences: list[str]) -> str:
    money_or_ratio_pattern = re.compile(r"\d+(?:,\d{3})*(?:\.\d+)?\s*(?:조|억|만|천)?\s*(?:원|%)")
    for sentence in sentences:
        if money_or_ratio_pattern.search(sentence):
            return sentence
    amount_pattern = re.compile(r"\d+(?:,\d{3})*(?:\.\d+)?\s*(?:조|억|만|천)?\s*(?:주|명|배)")
    for sentence in sentences:
        if amount_pattern.search(sentence):
            return sentence
    return next((sentence for sentence in sentences if any(keyword in sentence for keyword in SUMMARY_KEYWORDS)), "")


def rule_sentiment_and_impact(title: str, text: str) -> tuple[str, int, float]:
    haystack = f"{title}\n{text}".lower()
    if any(keyword.lower() in title.lower() for keyword in NEUTRAL_ADMIN_TITLE_KEYWORDS):
        return "neutral", 0, 0.72
    positive_hits = [keyword for keyword in POSITIVE_KEYWORDS if keyword.lower() in haystack]
    negative_hits = [keyword for keyword in NEGATIVE_KEYWORDS if keyword.lower() in haystack]

    if positive_hits and negative_hits:
        return "mixed", 1 if len(positive_hits) >= len(negative_hits) else -1, 0.72
    if negative_hits:
        return "negative", -2 if len(negative_hits) >= 2 else -1, 0.78
    if positive_hits:
        return "positive", 2 if len(positive_hits) >= 2 else 1, 0.74
    return "neutral", 0, 0.62


def verify_numerical_grounding(original_text: str, summary_text: str) -> bool:
    clean_original = re.sub(r"[\s,]", "", original_text)
    clean_summary = re.sub(r"[\s,]", "", summary_text)
    summary_tokens = re.findall(r"\d+(?:\.\d+)?[조억만천원%년월일주명배]+", clean_summary)
    for token in summary_tokens:
        pattern = r"(?<!\d)" + re.escape(token) + r"(?!\d)"
        if not re.search(pattern, clean_original):
            return False
    return True


def validate_payload(payload: dict[str, Any], original_text: str, provider: str) -> tuple[bool, list[str]]:
    errors: list[str] = []
    if not isinstance(payload.get("summary"), str) or not payload["summary"].strip():
        errors.append("summary_empty")
    if not isinstance(payload.get("key_points"), list):
        errors.append("key_points_not_list")
    if not isinstance(payload.get("risks"), list):
        errors.append("risks_not_list")
    if provider == "qwen" and not verify_numerical_grounding(original_text, payload.get("summary", "")):
        errors.append("numerical_grounding_failed")
    return not errors, errors


def coerce_payload(payload: dict[str, Any]) -> dict[str, Any]:
    summary = payload.get("summary")
    key_points = payload.get("key_points")
    risks = payload.get("risks")
    return {
        "summary": summary.strip() if isinstance(summary, str) else "",
        "key_points": [str(item).strip() for item in key_points if str(item).strip()]
        if isinstance(key_points, list)
        else [],
        "risks": [str(item).strip() for item in risks if str(item).strip()] if isinstance(risks, list) else [],
    }


def extract_json_object(text: str) -> dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text).strip()
        text = re.sub(r"```$", "", text).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end < start:
        raise ValueError("json object not found")
    return json.loads(text[start : end + 1])


def build_qwen_prompt(title: str, text: str) -> str:
    return (
        "너는 한국 상장사 공시/뉴스 요약기다.\n"
        "본문에 없는 내용은 만들지 마라.\n"
        "금액, 비율, 일자는 원문 그대로 유지하라.\n"
        "아래 JSON 형식으로만 출력하라.\n\n"
        "{\n"
        '  "summary": "한 문장 요약",\n'
        '  "key_points": ["핵심 사실 1", "핵심 사실 2"],\n'
        '  "risks": ["주의점 1"]\n'
        "}\n\n"
        f"제목: {title}\n"
        f"본문: {text[:6000]}"
    )


class QwenRunner:
    def __init__(self, model_name: str, *, local_files_only: bool, device: str = "auto") -> None:
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer

        if device == "auto":
            device = "mps" if torch.backends.mps.is_available() else "cpu"
        self.device = device
        self.tokenizer = AutoTokenizer.from_pretrained(model_name, local_files_only=local_files_only)
        dtype = torch.float16 if device == "mps" else torch.float32
        self.model = AutoModelForCausalLM.from_pretrained(
            model_name,
            torch_dtype=dtype,
            local_files_only=local_files_only,
        )
        self.model.to(device)
        self.model.eval()

    def generate(self, title: str, text: str, max_new_tokens: int) -> dict[str, Any]:
        import torch

        messages = [
            {"role": "system", "content": "너는 사실 기반 한국 금융 뉴스/공시 요약기다."},
            {"role": "user", "content": build_qwen_prompt(title, text)},
        ]
        prompt = self.tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
        inputs = self.tokenizer([prompt], return_tensors="pt").to(self.device)
        with torch.no_grad():
            generated_ids = self.model.generate(
                **inputs,
                max_new_tokens=max_new_tokens,
                do_sample=False,
                temperature=None,
                top_p=None,
                top_k=None,
            )
        output_ids = generated_ids[0][inputs.input_ids.shape[-1] :]
        raw_text = self.tokenizer.decode(output_ids, skip_special_tokens=True)
        payload = extract_json_object(raw_text)
        payload["_raw_text"] = raw_text
        return payload


def run_qwen(
    title: str,
    text: str,
    *,
    runner: QwenRunner,
    max_new_tokens: int,
) -> dict[str, Any]:
    return runner.generate(title, text, max_new_tokens)


def fetch_dart_case(receipt_no: str, *, title: str | None, symbol: str) -> EvidenceInput:
    from app.external.dart import DartClient

    client = DartClient()
    client.redis_client = None
    filing_text = client.fetch_filing_text(receipt_no)
    return EvidenceInput(
        source_type="filing",
        symbol=symbol,
        title=title or f"DART 공시 {receipt_no}",
        text=filing_text,
        source_id=receipt_no,
    )


def build_input_cases(args: argparse.Namespace) -> list[EvidenceInput]:
    if args.dart_receipt_no:
        return [
            fetch_dart_case(
                args.dart_receipt_no,
                title=args.title,
                symbol=args.symbol,
            )
        ]

    if args.text_file:
        text = Path(args.text_file).read_text(encoding="utf-8")
        return [
            EvidenceInput(
                source_type=args.source_type,
                symbol=args.symbol,
                title=args.title or Path(args.text_file).stem,
                text=text,
                source_id=str(Path(args.text_file)),
            )
        ]

    if args.text:
        if not args.title:
            raise SystemExit("--text를 사용할 때는 --title도 지정해야 합니다.")
        return [
            EvidenceInput(
                source_type=args.source_type,
                symbol=args.symbol,
                title=args.title,
                text=args.text,
                source_id="manual",
            )
        ]

    cases = [EvidenceInput(**case) for case in TEST_CASES]
    if args.case_index is not None:
        try:
            return [cases[args.case_index]]
        except IndexError as exc:
            raise SystemExit(f"--case-index 범위가 잘못됐습니다. 0~{len(cases) - 1} 중 하나를 쓰세요.") from exc
    return cases


def analyze_case(
    case: EvidenceInput,
    provider: str,
    model_name: str,
    max_new_tokens: int,
    qwen_runner: QwenRunner | None,
) -> AnalysisResult:
    started = time.perf_counter()
    title = case.title
    text = case.text
    sentiment, impact_score, confidence = rule_sentiment_and_impact(title, text)
    fallback_payload = extractive_payload(title, text)
    raw_response: dict[str, Any] = {
        "provider": provider,
        "prompt_version": PROMPT_VERSION,
    }

    if provider == "qwen":
        try:
            if qwen_runner is None:
                raise RuntimeError("qwen_runner is required for provider=qwen")
            generated_payload = coerce_payload(
                run_qwen(
                    title,
                    text,
                    runner=qwen_runner,
                    max_new_tokens=max_new_tokens,
                )
            )
            valid, errors = validate_payload(generated_payload, f"{title}\n{text}", provider)
            raw_response["generated"] = generated_payload
            raw_response["validation_errors"] = errors
            if valid:
                payload = generated_payload
                raw_response["status"] = "pass"
            else:
                payload = fallback_payload
                raw_response["status"] = "fallback"
                raw_response["fallback_provider"] = "extractive"
        except Exception as exc:
            payload = fallback_payload
            raw_response["status"] = "fallback"
            raw_response["fallback_provider"] = "extractive"
            raw_response["failure_reason"] = type(exc).__name__
            raw_response["failure_message"] = str(exc)
    else:
        payload = fallback_payload
        valid, errors = validate_payload(payload, f"{title}\n{text}", "extractive")
        raw_response["status"] = "pass" if valid else "fallback"
        raw_response["validation_errors"] = errors

    return AnalysisResult(
        summary=payload["summary"],
        sentiment=sentiment if sentiment in VALID_SENTIMENTS else "neutral",
        impact_score=max(-2, min(2, int(impact_score))),
        confidence=confidence,
        key_points=payload["key_points"],
        risks=payload["risks"],
        model_name=model_name if provider == "qwen" else "rule-only-extractive",
        prompt_version=PROMPT_VERSION,
        raw_response=raw_response,
        elapsed_ms=int((time.perf_counter() - started) * 1000),
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Prototype local evidence analysis with extractive or Qwen summary.")
    parser.add_argument("--provider", choices=["extractive", "qwen"], default="extractive")
    parser.add_argument("--output", choices=["json", "simple"], default="json")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--max-new-tokens", type=int, default=220)
    parser.add_argument("--allow-download", action="store_true", help="Allow HuggingFace download if model is not cached.")
    parser.add_argument("--device", choices=["auto", "mps", "cpu"], default="auto")
    parser.add_argument("--case-index", type=int, default=None, help="Run one built-in sample case by zero-based index.")
    parser.add_argument("--source-type", choices=["news", "filing"], default="filing")
    parser.add_argument("--symbol", default="000000")
    parser.add_argument("--title", default=None)
    parser.add_argument("--text", default=None)
    parser.add_argument("--text-file", default=None)
    parser.add_argument("--dart-receipt-no", default=None)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    cases = build_input_cases(args)
    qwen_runner = (
        QwenRunner(args.model, local_files_only=not args.allow_download, device=args.device)
        if args.provider == "qwen"
        else None
    )
    results = [
        analyze_case(
            case,
            provider=args.provider,
            model_name=args.model,
            max_new_tokens=args.max_new_tokens,
            qwen_runner=qwen_runner,
        )
        for case in cases
    ]
    if args.output == "simple":
        for index, result in enumerate(results, start=1):
            validation_errors = result.raw_response.get("validation_errors") or []
            harness_status = result.raw_response.get("status", "unknown")
            print(f"[분석 {index}]")
            print(f"요약: {result.summary}")
            print(f"긍부정: {result.sentiment}")
            print(f"영향도: {result.impact_score:+d}")
            print(f"하네스: {harness_status}")
            if validation_errors:
                print(f"검증 오류: {', '.join(map(str, validation_errors))}")
            print()
        return

    print(json.dumps([asdict(result) for result in results], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
