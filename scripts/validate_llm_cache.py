from __future__ import annotations

import json

from langchain_core.messages import AIMessage, HumanMessage

from app.core.llm_cache import CachedChatModel, LLMCachePolicy


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.ttls: dict[str, int] = {}

    def get(self, key: str):
        return self.values.get(key)

    def setex(self, key: str, ttl: int, value: str) -> None:
        self.values[key] = value
        self.ttls[key] = ttl


class FakeLLM:
    model_name = "fake-llm"

    def __init__(self) -> None:
        self.calls = 0

    def invoke(self, input, config=None, *, stop=None, **kwargs):
        self.calls += 1
        return AIMessage(content=f"response-{self.calls}")


def main() -> None:
    redis_client = FakeRedis()
    inner = FakeLLM()
    cached = CachedChatModel(
        inner,
        role="bull",
        temperature=0.3,
        prompt_version="bull-v1",
        cache_policy=LLMCachePolicy(enabled=True, ttl_seconds=3600),
        redis_client=redis_client,
    )

    messages = [HumanMessage(content="삼성전자 투자 판단을 말해줘")]
    first = cached.invoke(messages)
    second = cached.invoke(messages)
    assert first.content == "response-1"
    assert second.content == "response-1"
    assert inner.calls == 1
    assert len(redis_client.values) == 1

    only_key = next(iter(redis_client.values))
    payload = json.loads(redis_client.values[only_key])
    assert payload["content"] == "response-1"
    assert payload["prompt_version"] == "bull-v1"

    print(
        {
            "cache_hit": True,
            "inner_calls": inner.calls,
            "stored_keys": len(redis_client.values),
            "ttl": redis_client.ttls[only_key],
        }
    )


if __name__ == "__main__":
    main()
