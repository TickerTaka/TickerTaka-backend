"""Langfuse 트레이싱 게이트 클라이언트.

키(LANGFUSE_PUBLIC_KEY/SECRET_KEY) + 토글(LANGFUSE_TRACING_ENABLED=True)이
모두 있을 때만 Langfuse 인스턴스를 만든다. 하나라도 없으면 None 을 반환해
호출부가 전부 no-op 으로 동작한다(운영 영향 0, 임포트 비용/메모리 보호).
"""
from __future__ import annotations

import os
from functools import lru_cache
from typing import TYPE_CHECKING

from app.config import get_settings

if TYPE_CHECKING:
    from langfuse import Langfuse

# OTel service.name 기본값. .env 는 pydantic(extra_forbid) 때문에 못 쓰고, OTel SDK 는
# OS 환경변수만 읽으므로 클라이언트 생성 전에 프로그램적으로 주입한다(이미 있으면 존중).
_SERVICE_NAME = "tickertaka-analysis-worker"


@lru_cache
def get_langfuse() -> "Langfuse | None":
    s = get_settings()
    if not (s.langfuse_enabled and s.langfuse_public_key and s.langfuse_secret_key):
        return None
    os.environ.setdefault("OTEL_SERVICE_NAME", _SERVICE_NAME)
    from langfuse import Langfuse

    return Langfuse(
        public_key=s.langfuse_public_key,
        secret_key=s.langfuse_secret_key,
        host=s.langfuse_host,
    )
