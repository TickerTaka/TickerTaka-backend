# app/core/database.py
"""asyncpg 커넥션 풀 — DB팀 PostgreSQL 서버 연결"""
from __future__ import annotations
import asyncpg

_pool: asyncpg.Pool | None = None


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        from app.config import get_settings
        url = get_settings().database_url.replace("postgresql+asyncpg://", "postgresql://")
        _pool = await asyncpg.create_pool(url, min_size=2, max_size=10, command_timeout=30)
    return _pool


async def close_pool():
    global _pool
    if _pool:
        await _pool.close()
        _pool = None
