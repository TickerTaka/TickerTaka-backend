from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass
from datetime import datetime
import logging
from zoneinfo import ZoneInfo

from app.config import get_settings
from app.core.redis import get_redis, make_key

logger = logging.getLogger(__name__)

KST = ZoneInfo("Asia/Seoul")
_runtime_context: ContextVar["DebateRuntimeContext | None"] = ContextVar("debate_runtime_context", default=None)


@dataclass(slots=True)
class DebateRuntimeContext:
    user_id: str
    symbol: str
    session_id: str


@dataclass(slots=True)
class StartSessionResult:
    allowed: bool
    reason: str = "ok"


class DebateRuntimeGuard:
    def __init__(
        self,
        *,
        redis_client=None,
        max_tokens_per_user_per_day: int | None = None,
        max_debates_per_user_per_day: int | None = None,
        active_ttl_seconds: int | None = None,
    ) -> None:
        settings = get_settings()
        self.redis_client = redis_client if redis_client is not None else get_redis()
        self.max_tokens_per_user_per_day = (
            max_tokens_per_user_per_day
            if max_tokens_per_user_per_day is not None
            else settings.max_tokens_per_user_per_day
        )
        self.max_debates_per_user_per_day = (
            max_debates_per_user_per_day
            if max_debates_per_user_per_day is not None
            else settings.max_debates_per_user_per_day
        )
        self.active_ttl_seconds = (
            active_ttl_seconds
            if active_ttl_seconds is not None
            else settings.debate_active_ttl_seconds
        )

    def try_start_session(
        self,
        *,
        user_id: str,
        symbol: str,
        session_id: str,
        estimated_tokens: int = 0,
    ) -> StartSessionResult:
        if self.redis_client is None:
            return StartSessionResult(True)

        usage = self.get_usage(user_id=user_id, session_id=session_id)
        if usage["daily_debates"] >= self.max_debates_per_user_per_day:
            return StartSessionResult(False, "daily_debate_limit")
        if usage["daily_tokens"] + estimated_tokens > self.max_tokens_per_user_per_day:
            return StartSessionResult(False, "daily_token_limit")

        active_key = self._active_key(user_id, symbol)
        try:
            acquired = self.redis_client.set(active_key, session_id, nx=True, ex=self.active_ttl_seconds)
        except Exception:
            logger.exception("failed to acquire debate active guard for %s/%s", user_id, symbol)
            return StartSessionResult(True, "redis_error_fail_open")
        if not acquired:
            return StartSessionResult(False, "active_session")

        debate_key = self._daily_debates_key(user_id)
        try:
            current = self.redis_client.incr(debate_key)
            if current == 1:
                self.redis_client.expire(debate_key, 48 * 60 * 60)
        except Exception:
            logger.exception("failed to increment daily debate counter for %s", user_id)

        return StartSessionResult(True)

    def end_session(self, *, user_id: str, symbol: str, session_id: str) -> None:
        if self.redis_client is None:
            return
        active_key = self._active_key(user_id, symbol)
        try:
            current = self.redis_client.get(active_key)
            if current == session_id:
                self.redis_client.delete(active_key)
        except Exception:
            logger.exception("failed to release debate active guard for %s/%s", user_id, symbol)

    def record_token_usage(self, *, user_id: str, session_id: str, prompt_tokens: int = 0, completion_tokens: int = 0) -> None:
        if self.redis_client is None:
            return
        total = max(0, prompt_tokens) + max(0, completion_tokens)
        if total <= 0:
            return
        day_key = self._daily_tokens_key(user_id)
        cost_key = self._cost_key(session_id)
        try:
            daily_total = self.redis_client.incrby(day_key, total)
            if daily_total == total:
                self.redis_client.expire(day_key, 48 * 60 * 60)
            self.redis_client.hincrby(cost_key, "input_tokens", max(0, prompt_tokens))
            self.redis_client.hincrby(cost_key, "output_tokens", max(0, completion_tokens))
            self.redis_client.expire(cost_key, 24 * 60 * 60)
        except Exception:
            logger.exception("failed to record debate token usage for %s/%s", user_id, session_id)

    def record_usage_from_current_context(self, *, prompt_tokens: int = 0, completion_tokens: int = 0) -> None:
        context = _runtime_context.get()
        if context is None:
            return
        self.record_token_usage(
            user_id=context.user_id,
            session_id=context.session_id,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )

    def get_usage(self, *, user_id: str, session_id: str | None = None) -> dict[str, int]:
        if self.redis_client is None:
            return {
                "daily_tokens": 0,
                "daily_debates": 0,
                "session_input_tokens": 0,
                "session_output_tokens": 0,
            }
        daily_tokens = _safe_int(self.redis_client.get(self._daily_tokens_key(user_id)))
        daily_debates = _safe_int(self.redis_client.get(self._daily_debates_key(user_id)))
        session_input_tokens = 0
        session_output_tokens = 0
        if session_id:
            try:
                usage = self.redis_client.hgetall(self._cost_key(session_id)) or {}
            except Exception:
                logger.exception("failed to read debate cost usage for %s", session_id)
                usage = {}
            session_input_tokens = _safe_int(usage.get("input_tokens"))
            session_output_tokens = _safe_int(usage.get("output_tokens"))
        return {
            "daily_tokens": daily_tokens,
            "daily_debates": daily_debates,
            "session_input_tokens": session_input_tokens,
            "session_output_tokens": session_output_tokens,
        }

    @contextmanager
    def bind_context(self, *, user_id: str, symbol: str, session_id: str):
        token = _runtime_context.set(DebateRuntimeContext(user_id=user_id, symbol=symbol, session_id=session_id))
        try:
            yield
        finally:
            _runtime_context.reset(token)

    @staticmethod
    def _active_key(user_id: str, symbol: str) -> str:
        return make_key("debate", "active", user_id, symbol)

    @staticmethod
    def _daily_tokens_key(user_id: str) -> str:
        return make_key("rate", "user", user_id, "tokens", _kst_date())

    @staticmethod
    def _daily_debates_key(user_id: str) -> str:
        return make_key("rate", "user", user_id, "debates", _kst_date())

    @staticmethod
    def _cost_key(session_id: str) -> str:
        return make_key("cost", "debate", session_id)


def get_tracker() -> DebateRuntimeGuard:
    return DebateRuntimeGuard()


def _kst_date() -> str:
    return datetime.now(KST).date().isoformat()


def _safe_int(value) -> int:
    try:
        return int(value)
    except Exception:
        return 0
