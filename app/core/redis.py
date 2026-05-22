from __future__ import annotations

from functools import lru_cache
import logging

try:
    import redis
except Exception:  # pragma: no cover - runtime dependency guard
    redis = None

from app.config import get_settings

logger = logging.getLogger(__name__)


def make_key(domain: str, purpose: str, *parts: object) -> str:
    """Build a Redis key with the shared `<domain>:<purpose>:<parts...>` convention."""
    key_parts = [domain.strip(), purpose.strip()]
    key_parts.extend(str(part).strip() for part in parts if str(part).strip())
    return ":".join(key_parts)


def build_redis_client(
    redis_url: str,
    *,
    decode_responses: bool = True,
) -> redis.Redis | None:
    if redis is None:
        return None
    try:
        return redis.Redis.from_url(redis_url, decode_responses=decode_responses)
    except Exception:
        logger.exception("failed to create redis client")
        return None


@lru_cache
def get_redis() -> redis.Redis | None:
    return build_redis_client(get_settings().redis_url)
