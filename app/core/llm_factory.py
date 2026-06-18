from __future__ import annotations
import logging
from typing import Literal

from langchain_openai import ChatOpenAI
from langchain_core.messages import BaseMessage
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from openai import APIError, RateLimitError, APITimeoutError
from app.config import get_settings
from app.core.debate_runtime_guard import get_tracker
from app.core.llm_cache import CachedChatModel, LLMCachePolicy

logger = logging.getLogger(__name__)

_PROMPT_VERSION_MAP = {
    "bull": "bull-v1",
    "bear": "bear-v1",
    "judge": "judge-v1",
    "moderator": "moderator-v1",
    "fallback": "fallback-v1",
}


def _get_langfuse_callback(session_id: str | None = None, tags: list[str] | None = None):
    """Langfuse CallbackHandler 반환. 비활성화 시 None."""
    try:
        from app.core.tracing import get_langfuse
        lf = get_langfuse()
        if lf is None:
            return None
        from langfuse.callback import CallbackHandler
        return CallbackHandler(
            public_key=lf.client.public_key,
            secret_key=lf.client.secret_key,
            host=lf.client.host,
            session_id=session_id,
            tags=tags or [],
        )
    except Exception:
        return None


def get_llm(
    role: Literal["bull", "bear", "judge", "moderator", "fallback"] = "fallback",
    temperature: float = 0.7,
    *,
    cached: bool = True,
    session_id: str | None = None,
) -> CachedChatModel | ChatOpenAI:
    settings = get_settings()
    if not settings.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY is required to execute live debates")

    model_map = {
        "bull":      settings.bull_model,
        "bear":      settings.bear_model,
        "judge":     settings.judge_llm_model,
        "moderator": settings.moderator_model,
        "fallback":  settings.fallback_model,
    }
    model_id = model_map.get(role, settings.fallback_model)

    # Langfuse 콜백 — 활성화 시 모든 LLM 호출 자동 추적
    callbacks = []
    cb = _get_langfuse_callback(session_id=session_id, tags=[role])
    if cb:
        callbacks.append(cb)

    client = ChatOpenAI(
        model=model_id,
        temperature=temperature,
        api_key=settings.openai_api_key,
        max_retries=3,
        timeout=60,
        callbacks=callbacks or None,
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
        logger.warning("[llm] %s 모델 3회 소진, fallback(%s)으로 강등: %s", role, settings.fallback_model, e)

    fallback_llm = ChatOpenAI(
        model=settings.fallback_model,
        temperature=temperature,
        api_key=settings.openai_api_key,
        max_retries=2,
        timeout=30,
    )
    return fallback_llm.invoke(messages).content


__all__ = ["get_llm", "invoke_with_fallback", "get_tracker"]
