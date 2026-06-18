from __future__ import annotations

import asyncio

import pytest

from app.domain import debate_service


class _Settings:
    """_astream_with_config 가 읽는 필드만 흉내."""

    debate_graph_recursion_limit = 64

    def __init__(self, timeout: int) -> None:
        self.debate_timeout_seconds = timeout


class _SlowStream:
    """청크 하나를 timeout 보다 오래 끄는(=hang) 비동기 스트림."""

    def __aiter__(self):
        return self

    async def __anext__(self):
        await asyncio.sleep(5)
        return {"node": {}}


class _FastStream:
    def __init__(self, chunks):
        self._it = iter(chunks)

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return next(self._it)
        except StopIteration:
            raise StopAsyncIteration


class _Runner:
    def __init__(self, stream):
        self._stream = stream

    def astream(self, state, config=None):
        return self._stream


async def _collect(runner):
    chunks = []
    async for chunk in debate_service._astream_with_config(runner, {}, session_id="s", user_id="u"):
        chunks.append(chunk)
    return chunks


def test_graph_timeout_raises(monkeypatch):
    # 1초 데드라인 < 5초 hang → TimeoutError 로 끊겨야 한다(호출부 fail-soft 진입점).
    monkeypatch.setattr(debate_service, "get_settings", lambda: _Settings(timeout=1))
    with pytest.raises((asyncio.TimeoutError, TimeoutError)):
        asyncio.run(_collect(_Runner(_SlowStream())))


def test_graph_completes_within_deadline(monkeypatch):
    # 데드라인 내 정상 완료 → 모든 청크 그대로 통과(무회귀).
    monkeypatch.setattr(debate_service, "get_settings", lambda: _Settings(timeout=30))
    chunks = asyncio.run(_collect(_Runner(_FastStream([{"a": 1}, {"b": 2}]))))
    assert chunks == [{"a": 1}, {"b": 2}]


def test_timeout_disabled_passthrough(monkeypatch):
    # timeout=0 이면 데드라인 비활성 → 그대로 통과(끊지 않음).
    monkeypatch.setattr(debate_service, "get_settings", lambda: _Settings(timeout=0))
    chunks = asyncio.run(_collect(_Runner(_FastStream([{"x": 1}]))))
    assert chunks == [{"x": 1}]
