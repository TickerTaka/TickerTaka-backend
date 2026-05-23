from __future__ import annotations

from app.core.debate_runtime_guard import DebateRuntimeGuard


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.hashes: dict[str, dict[str, int]] = {}
        self.ttls: dict[str, int] = {}

    def set(self, key: str, value: str, nx: bool = False, ex: int | None = None):
        if nx and key in self.values:
            return False
        self.values[key] = value
        if ex is not None:
            self.ttls[key] = ex
        return True

    def get(self, key: str):
        return self.values.get(key)

    def delete(self, key: str) -> None:
        self.values.pop(key, None)
        self.hashes.pop(key, None)

    def incr(self, key: str) -> int:
        value = int(self.values.get(key, "0")) + 1
        self.values[key] = str(value)
        return value

    def incrby(self, key: str, amount: int) -> int:
        value = int(self.values.get(key, "0")) + amount
        self.values[key] = str(value)
        return value

    def expire(self, key: str, ttl: int) -> None:
        self.ttls[key] = ttl

    def hincrby(self, key: str, field: str, amount: int) -> None:
        current = self.hashes.setdefault(key, {})
        current[field] = int(current.get(field, 0)) + amount

    def hgetall(self, key: str):
        return self.hashes.get(key, {})


def main() -> None:
    redis_client = FakeRedis()
    guard = DebateRuntimeGuard(
        redis_client=redis_client,
        max_tokens_per_user_per_day=100,
        max_debates_per_user_per_day=2,
        active_ttl_seconds=1800,
    )

    first = guard.try_start_session(user_id="user-1", symbol="000660", session_id="s1")
    second = guard.try_start_session(user_id="user-1", symbol="000660", session_id="s2")
    assert first.allowed is True
    assert second.allowed is False
    assert second.reason == "active_session"

    guard.record_token_usage(user_id="user-1", session_id="s1", prompt_tokens=30, completion_tokens=20)
    usage = guard.get_usage(user_id="user-1", session_id="s1")
    assert usage["daily_tokens"] == 50
    assert usage["daily_debates"] == 1
    assert usage["session_input_tokens"] == 30
    assert usage["session_output_tokens"] == 20

    guard.end_session(user_id="user-1", symbol="000660", session_id="s1")
    third = guard.try_start_session(user_id="user-1", symbol="000660", session_id="s3")
    assert third.allowed is True

    fourth = guard.try_start_session(user_id="user-1", symbol="005930", session_id="s4")
    assert fourth.allowed is False
    assert fourth.reason == "daily_debate_limit"

    print(
        {
            "active_guard": second.reason,
            "daily_tokens": usage["daily_tokens"],
            "daily_debates": usage["daily_debates"],
            "rate_limit_reason": fourth.reason,
        }
    )


if __name__ == "__main__":
    main()
