from __future__ import annotations

from app.agents.debate_checkpoint import clear_checkpoint, load_checkpoint, merge_state, save_checkpoint


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.ttls: dict[str, int] = {}

    def setex(self, key: str, ttl: int, value: str) -> None:
        self.values[key] = value
        self.ttls[key] = ttl

    def get(self, key: str):
        return self.values.get(key)

    def delete(self, key: str) -> None:
        self.values.pop(key, None)


def main() -> None:
    redis_client = FakeRedis()
    base_state = {
        "session_id": "session-123",
        "symbol": "000660",
        "symbol_name": "SK하이닉스",
        "category": "financial",
        "current_round": "opening",
        "round_order": 0,
        "max_rounds": 3,
        "agenda": [],
        "price_context": "",
        "financial_context": "",
        "evidence_context": "",
        "news_chunks": [],
        "statements": [],
        "moderator_flag": "ok",
        "intervention_note": "",
        "hallucination_count": 0,
        "summary_content": "",
        "key_points": [],
    }
    patch = {
        "agenda": ["쟁점1"],
        "statements": [{"agent_role": "bull", "round": "opening", "round_order": 1, "content": "테스트", "model_used": "fake", "evidences": []}],
        "round_order": 1,
    }
    merged = merge_state(base_state, patch)
    save_checkpoint(merged, redis_client=redis_client, ttl_seconds=3600)
    loaded = load_checkpoint("session-123", redis_client=redis_client)
    assert loaded is not None
    assert loaded["round_order"] == 1
    assert len(loaded["statements"]) == 1
    clear_checkpoint("session-123", redis_client=redis_client)
    assert load_checkpoint("session-123", redis_client=redis_client) is None
    print({"session_id": "session-123", "saved": True, "round_order": 1, "cleared": True})


if __name__ == "__main__":
    main()
