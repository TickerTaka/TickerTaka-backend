from __future__ import annotations
import logging
import time
from functools import lru_cache
from typing import Literal

from langchain_openai import ChatOpenAI
from app.config import get_settings

logger = logging.getLogger(__name__)


def get_llm(
    role: Literal["bull", "bear", "moderator", "fallback"] = "fallback",
    temperature: float = 0.7,
) -> ChatOpenAI:
    settings = get_settings()

    # 현재 사용 가능한 무료 모델 — rate limit 시 순서대로 시도
    model_map = {
        "bull":      settings.bull_model,
        "bear":      settings.bear_model,
        "moderator": settings.moderator_model,
        "fallback":  settings.fallback_model,
    }
    model_id = model_map.get(role, settings.fallback_model)

    return ChatOpenAI(
        model=model_id,
        temperature=temperature,
        api_key=settings.openrouter_api_key,
        base_url=settings.openrouter_base_url,
        max_retries=3,          # openai 클라이언트 내장 재시도
        timeout=60,
        default_headers={
            "HTTP-Referer": "https://tickertaka.app",
            "X-Title": "TickerTaka Debate",
        },
    )


def get_tracker():
    """rate limit 추적 (Redis 없으면 더미 반환)"""
    class DummyTracker:
        async def check_and_increment(self, _): return True
        async def get_usage(self): return {}
    return DummyTracker()
