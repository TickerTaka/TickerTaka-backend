"""테스트 공통 설정.

테스트가 운영 Langfuse 프로젝트에 trace 를 쏘지 않도록 강제 비활성화한다.
(evidence_analysis 의 계측은 호출 시점에 app.core.tracing.get_langfuse 를
지역 import 하므로, 모듈 속성을 패치하면 전부 no-op 으로 떨어진다.)
"""
import os

import pytest

# settings 캐시가 차기 전에 토글을 꺼 둔다(이중 안전장치).
os.environ["LANGFUSE_TRACING_ENABLED"] = "false"


@pytest.fixture(autouse=True)
def _disable_langfuse(monkeypatch):
    from app.core import tracing

    tracing.get_langfuse.cache_clear()
    monkeypatch.setattr(tracing, "get_langfuse", lambda: None)
    yield
