from __future__ import annotations
import logging
from typing import Literal

from langchain_openai import ChatOpenAI
from langchain_core.messages import BaseMessage
from langchain_core.language_models.chat_models import BaseChatModel
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from openai import APIError, RateLimitError, APITimeoutError
from app.config import get_settings
from app.core.debate_runtime_guard import get_tracker
from app.core.llm_cache import CachedChatModel, LLMCachePolicy

logger = logging.getLogger(__name__)


def _fallback_model_id(settings) -> str:
    if settings.debate_llm_provider == "anthropic":
        return settings.fallback_model_anthropic
    if settings.debate_llm_provider == "openrouter":
        return settings.fallback_model_openrouter
    return settings.fallback_model

_PROMPT_VERSION_MAP = {
    "bull": "bull-v1",
    "bear": "bear-v1",
    "judge": "judge-v1",
    "moderator": "moderator-v1",
    "fallback": "fallback-v1",
}


def _build_client(model_id: str, temperature: float, settings) -> BaseChatModel:
    """프로바이더(openai | openrouter | anthropic)에 따라 LLM 클라이언트 생성."""
    if settings.debate_llm_provider == "anthropic":
        if not settings.anthropic_api_key:
            raise RuntimeError("ANTHROPIC_API_KEY is required when DEBATE_LLM_PROVIDER=anthropic")
        from langchain_anthropic import ChatAnthropic
        return ChatAnthropic(
            model=model_id,
            temperature=temperature,
            api_key=settings.anthropic_api_key,
            max_retries=3,
            timeout=60,
            max_tokens=4096,  # 기본값(1024)이 너무 작아 긴 요약 JSON이 잘림
        )
    if settings.debate_llm_provider == "openrouter":
        if not settings.openrouter_api_key:
            raise RuntimeError("OPENROUTER_API_KEY is required when DEBATE_LLM_PROVIDER=openrouter")
        return ChatOpenAI(
            model=model_id,
            temperature=temperature,
            api_key=settings.openrouter_api_key,
            base_url=settings.openrouter_base_url,
            default_headers={
                "HTTP-Referer": "https://tickertaka.app",
                "X-Title": "TickerTaka",
            },
            max_retries=3,
            timeout=60,
        )
    # 기본: OpenAI 직접 호출
    if not settings.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY is required when DEBATE_LLM_PROVIDER=openai")
    return ChatOpenAI(
        model=model_id,
        temperature=temperature,
        api_key=settings.openai_api_key,
        max_retries=3,
        timeout=60,
    )


def get_llm(
    role: Literal["bull", "bear", "judge", "moderator", "fallback"] = "fallback",
    temperature: float = 0.7,
    *,
    cached: bool = True,
    session_id: str | None = None,
) -> CachedChatModel | ChatOpenAI:
    settings = get_settings()

    if settings.debate_llm_provider == "anthropic":
        model_map = {
            "bull":      settings.bull_model_anthropic,
            "bear":      settings.bear_model_anthropic,
            "judge":     settings.judge_llm_model_anthropic,
            "moderator": settings.moderator_model_anthropic,
            "fallback":  settings.fallback_model_anthropic,
        }
    elif settings.debate_llm_provider == "openrouter":
        model_map = {
            "bull":      settings.bull_model_openrouter,
            "bear":      settings.bear_model_openrouter,
            "judge":     settings.judge_llm_model_openrouter,
            "moderator": settings.moderator_model_openrouter,
            "fallback":  settings.fallback_model_openrouter,
        }
    else:
        model_map = {
            "bull":      settings.bull_model,
            "bear":      settings.bear_model,
            "judge":     settings.judge_llm_model,
            "moderator": settings.moderator_model,
            "fallback":  settings.fallback_model,
        }
    model_id = model_map.get(role, settings.fallback_model)

    client = _build_client(model_id, temperature, settings)
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


@retry(
    retry=retry_if_exception_type((RateLimitError, APITimeoutError, APIError)),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    reraise=True,
)
def _invoke_primary(
    messages: list[BaseMessage],
    role: Literal["bull", "bear", "judge", "moderator", "fallback"],
    temperature: float,
) -> str:
    """primary 모델 호출 — 최대 3회 retry, 소진 시 raise."""
    llm = get_llm(role, temperature=temperature)
    return llm.invoke(messages).content


def invoke_with_fallback(
    messages: list[BaseMessage],
    role: Literal["bull", "bear", "judge", "moderator", "fallback"] = "fallback",
    temperature: float = 0.3,
) -> str:
    """primary 3회 retry → 실패 시 fallback 모델로 강등. fallback도 실패하면 raise."""
    settings = get_settings()
    try:
        return _invoke_primary(messages, role, temperature)
    except (RateLimitError, APITimeoutError, APIError) as e:
        fallback_model_id = _fallback_model_id(settings)
        logger.warning("[llm] %s 모델 3회 소진, fallback(%s)으로 강등: %s", role, fallback_model_id, e)

    fallback_model_id = _fallback_model_id(settings)
    fallback_llm = _build_client(fallback_model_id, temperature, settings)
    return fallback_llm.invoke(messages).content


__all__ = ["get_llm", "invoke_with_fallback", "get_tracker"]
