from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import json
import logging
from typing import Any, Protocol

from langchain_core.messages import AIMessage, BaseMessage

from app.core.redis import get_redis, make_key

logger = logging.getLogger(__name__)


class InvokableLLM(Protocol):
    model_name: str | None

    def invoke(self, input: Any, config: Any = None, *, stop: Any = None, **kwargs: Any) -> Any: ...


@dataclass(slots=True)
class LLMCachePolicy:
    enabled: bool
    ttl_seconds: int = 24 * 60 * 60


class CachedChatModel:
    def __init__(
        self,
        inner: InvokableLLM,
        *,
        role: str,
        temperature: float,
        prompt_version: str,
        cache_policy: LLMCachePolicy,
        redis_client=None,
    ) -> None:
        self._inner = inner
        self._role = role
        self._temperature = temperature
        self._prompt_version = prompt_version
        self._cache_policy = cache_policy
        self._redis = redis_client if redis_client is not None else get_redis()
        self.model_name = getattr(inner, "model_name", None)

    def invoke(self, input: Any, config: Any = None, *, stop: Any = None, **kwargs: Any) -> Any:
        if not self._cache_policy.enabled or self._redis is None:
            return self._inner.invoke(input, config=config, stop=stop, **kwargs)

        cache_key = self._build_key(input)
        cached = self._read(cache_key)
        if cached is not None:
            return cached

        response = self._inner.invoke(input, config=config, stop=stop, **kwargs)
        self._record_usage(response)
        self._write(cache_key, response)
        return response

    def bind_tools(self, tools: Any, **kwargs: Any) -> "CachedChatModel":
        bound = self._inner.bind_tools(tools, **kwargs)
        return CachedChatModel(
            bound,
            role=self._role,
            temperature=self._temperature,
            prompt_version=self._prompt_version,
            cache_policy=self._cache_policy,
            redis_client=self._redis,
        )

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def _build_key(self, input: Any) -> str:
        serialized = _serialize_messages(input)
        digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
        model_name = self.model_name or "unknown"
        temperature = f"{self._temperature:.2f}"
        return make_key("llm-cache", model_name, self._prompt_version, digest, temperature)

    def _read(self, key: str) -> AIMessage | None:
        try:
            raw = self._redis.get(key)
        except Exception:
            logger.exception("llm cache read failed")
            return None
        if not raw:
            return None
        try:
            payload = json.loads(raw)
            return AIMessage(
                content=payload["content"],
                response_metadata=payload.get("response_metadata") or {},
                usage_metadata=payload.get("usage_metadata") or None,
            )
        except Exception:
            logger.exception("llm cache decode failed")
            return None

    def _write(self, key: str, response: Any) -> None:
        content = getattr(response, "content", None)
        if content is None:
            return
        payload = {
            "content": content,
            "response_metadata": getattr(response, "response_metadata", {}) or {},
            "usage_metadata": getattr(response, "usage_metadata", None),
            "cached_at": datetime.now(UTC).isoformat(),
            "prompt_version": self._prompt_version,
            "role": self._role,
        }
        try:
            self._redis.setex(
                key,
                self._cache_policy.ttl_seconds,
                json.dumps(payload, ensure_ascii=False),
            )
        except Exception:
            logger.exception("llm cache write failed")

    def _record_usage(self, response: Any) -> None:
        usage_metadata = getattr(response, "usage_metadata", None) or {}
        response_metadata = getattr(response, "response_metadata", None) or {}
        prompt_tokens = _coerce_usage_value(
            usage_metadata.get("input_tokens"),
            response_metadata.get("token_usage", {}).get("prompt_tokens"),
            response_metadata.get("usage", {}).get("prompt_tokens"),
        )
        completion_tokens = _coerce_usage_value(
            usage_metadata.get("output_tokens"),
            response_metadata.get("token_usage", {}).get("completion_tokens"),
            response_metadata.get("usage", {}).get("completion_tokens"),
        )
        if prompt_tokens == 0 and completion_tokens == 0:
            return
        try:
            from app.core.debate_runtime_guard import get_tracker

            get_tracker().record_usage_from_current_context(
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
            )
        except Exception:
            logger.exception("llm usage tracking failed")


def _serialize_messages(input: Any) -> str:
    if isinstance(input, str):
        return input
    if isinstance(input, BaseMessage):
        return json.dumps(_serialize_message(input), ensure_ascii=False, sort_keys=True)
    if isinstance(input, list):
        return json.dumps([_serialize_messages(item) if not isinstance(item, BaseMessage) else _serialize_message(item) for item in input], ensure_ascii=False, sort_keys=True)
    if isinstance(input, tuple):
        return json.dumps([_serialize_messages(item) for item in input], ensure_ascii=False, sort_keys=True)
    return json.dumps(input, ensure_ascii=False, sort_keys=True, default=str)


def _serialize_message(message: BaseMessage) -> dict[str, Any]:
    return {
        "type": message.type,
        "content": message.content,
        "name": getattr(message, "name", None),
        "additional_kwargs": getattr(message, "additional_kwargs", {}),
    }


def _coerce_usage_value(*values: Any) -> int:
    for value in values:
        try:
            if value is not None:
                return int(value)
        except Exception:
            continue
    return 0
