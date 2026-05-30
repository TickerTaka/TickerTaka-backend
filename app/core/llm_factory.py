from __future__ import annotations
import logging
from typing import Literal

from langchain_openai import ChatOpenAI
from app.config import get_settings
from app.core.debate_runtime_guard import get_tracker
from app.core.llm_cache import CachedChatModel, LLMCachePolicy

logger = logging.getLogger(__name__)

_PROMPT_VERSION_MAP = {
    "bull": "bull-v1",
    "bear": "bear-v1",
    "moderator": "moderator-v1",
    "fallback": "fallback-v1",
}


def get_llm(
    role: Literal["bull", "bear", "moderator", "fallback"] = "fallback",
    temperature: float = 0.7,
    *,
    cached: bool = True,
) -> CachedChatModel | ChatOpenAI:
    settings = get_settings()
    if not settings.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY is required to execute live debates")

    model_map = {
        "bull":      settings.bull_model,
        "bear":      settings.bear_model,
        "moderator": settings.moderator_model,
        "fallback":  settings.fallback_model,
    }
    model_id = model_map.get(role, settings.fallback_model)

    client = ChatOpenAI(
        model=model_id,
        temperature=temperature,
        api_key=settings.openai_api_key,
        max_retries=3,
        timeout=60,
    )
    if not cached:
        return client
    return CachedChatModel(
        client,
        role=role,
        temperature=temperature,
        prompt_version=_PROMPT_VERSION_MAP.get(role, "fallback-v1"),
        cache_policy=LLMCachePolicy(
            enabled=settings.llm_cache_enabled,
            ttl_seconds=settings.llm_cache_ttl_seconds,
        ),
    )


__all__ = ["get_llm", "get_tracker"]
