from __future__ import annotations

from datetime import UTC, datetime
import json
import logging
from typing import Any

from app.agents.state import DebateState
from app.core.redis import get_redis, make_key

logger = logging.getLogger(__name__)

_CHECKPOINT_TTL_SECONDS = 24 * 60 * 60


def checkpoint_key(session_id: str) -> str:
    return make_key("debate", "checkpoint", session_id)


def save_checkpoint(state: DebateState, *, redis_client=None, ttl_seconds: int = _CHECKPOINT_TTL_SECONDS) -> None:
    client = redis_client if redis_client is not None else get_redis()
    if client is None:
        return
    payload = dict(state)
    payload["_checkpointed_at"] = datetime.now(UTC).isoformat()
    try:
        client.setex(
            checkpoint_key(state["session_id"]),
            ttl_seconds,
            json.dumps(payload, ensure_ascii=False, default=str),
        )
    except Exception:
        logger.exception("failed to save debate checkpoint for %s", state["session_id"])


def load_checkpoint(session_id: str, *, redis_client=None) -> DebateState | None:
    client = redis_client if redis_client is not None else get_redis()
    if client is None:
        return None
    try:
        raw = client.get(checkpoint_key(session_id))
    except Exception:
        logger.exception("failed to load debate checkpoint for %s", session_id)
        return None
    if not raw:
        return None
    try:
        payload = json.loads(raw)
        payload.pop("_checkpointed_at", None)
        return payload
    except Exception:
        logger.exception("failed to decode debate checkpoint for %s", session_id)
        return None


def clear_checkpoint(session_id: str, *, redis_client=None) -> None:
    client = redis_client if redis_client is not None else get_redis()
    if client is None:
        return
    try:
        client.delete(checkpoint_key(session_id))
    except Exception:
        logger.exception("failed to clear debate checkpoint for %s", session_id)


def merge_state(base: DebateState, patch: dict[str, Any]) -> DebateState:
    merged = dict(base)
    for key, value in patch.items():
        if key == "statements":
            merged[key] = [*merged.get(key, []), *value]
            continue
        merged[key] = value
    return merged
