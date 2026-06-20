from __future__ import annotations

import app.core.tracing as tracing_mod
from app.domain.evidence_analysis import RemoteQwenEvidenceAnalyzer


# --- OpenAI 호환 클라이언트 mock (외부 서버/네트워크 불필요) -----------------
class _Msg:
    def __init__(self, content: str) -> None:
        self.content = content


class _Choice:
    def __init__(self, content: str) -> None:
        self.message = _Msg(content)


class _Resp:
    def __init__(self, content: str) -> None:
        self.choices = [_Choice(content)]


class _FakeCompletions:
    def __init__(self, handler) -> None:
        self.handler = handler
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.handler(kwargs, len(self.calls))


class _FakeClient:
    def __init__(self, handler) -> None:
        self.chat = type("_Chat", (), {})()
        self.chat.completions = _FakeCompletions(handler)


def _analyzer_with(handler) -> RemoteQwenEvidenceAnalyzer:
    a = RemoteQwenEvidenceAnalyzer("qwen2.5:3b", base_url="http://x/v1", api_key="EMPTY")
    a._client = _FakeClient(handler)  # _get_client() 우회(임포트/네트워크 없음)
    return a


# --- 1) 정상 응답: 파싱된 dict 반환 + 프롬프트에 제목/본문 포함 ---------------
def test_returns_parsed_dict_and_builds_prompt() -> None:
    a = _analyzer_with(lambda kw, n: _Resp('{"summary": "요약", "key_points": ["a"]}'))

    out = a.analyze("삼성전자 잠정실적", "영업이익 증가", kind="filing")

    assert out == {"summary": "요약", "key_points": ["a"]}
    user_msg = a._client.chat.completions.calls[0]["messages"][1]["content"]
    assert "삼성전자 잠정실적" in user_msg and "영업이익 증가" in user_msg


# --- 2) response_format 거부 → 무옵션 1회 재시도 후 성공 ----------------------
def test_retries_without_response_format_on_rejection() -> None:
    def handler(kw, n):
        if "response_format" in kw:
            raise ValueError("json_object not supported")
        return _Resp('{"summary": "ok"}')

    a = _analyzer_with(handler)
    out = a.analyze("t", "b", kind="news")

    assert out == {"summary": "ok"}
    calls = a._client.chat.completions.calls
    assert len(calls) == 2
    assert "response_format" in calls[0] and "response_format" not in calls[1]


# --- 3) 두 시도 모두 실패 → None (FinBERT baseline 폴백 보장) ----------------
def test_returns_none_when_all_attempts_fail() -> None:
    def handler(kw, n):
        raise RuntimeError("connection refused")

    a = _analyzer_with(handler)

    assert a.analyze("t", "b", kind="filing") is None


# --- 4) langfuse 게이트: 비활성이면 표준 openai 클라이언트 선택 ----------------
def test_get_client_uses_plain_openai_when_langfuse_disabled(monkeypatch) -> None:
    monkeypatch.setattr(tracing_mod, "get_langfuse", lambda: None)
    a = RemoteQwenEvidenceAnalyzer("qwen2.5:3b", base_url="http://x/v1")

    client = a._get_client()

    assert type(client).__module__.startswith("openai")
