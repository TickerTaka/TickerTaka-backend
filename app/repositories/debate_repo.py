# app/repositories/debate_repo.py
"""에이전트 DB 입출력 레이어"""
from __future__ import annotations
import json
import logging
from uuid import UUID
from app.core.database import get_pool

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════
# 읽기
# ══════════════════════════════════════════════════════════

async def fetch_price_context(symbol: str) -> dict:
    pool = await get_pool()
    async with pool.acquire() as conn:
        price = await conn.fetchrow("""
            SELECT close_price, change_rate, volume, open_price, high_price, low_price
            FROM price_cache WHERE symbol=$1 ORDER BY price_date DESC LIMIT 1
        """, symbol)
        tech = await conn.fetchrow("""
            SELECT ma20, ma60, ma120, rsi14, macd, macd_signal, macd_hist, volume_ma20
            FROM technical_indicator_cache WHERE symbol=$1 ORDER BY indicator_date DESC LIMIT 1
        """, symbol)
    return {"price": dict(price) if price else {}, "tech": dict(tech) if tech else {}}


async def fetch_financial_context(symbol: str) -> list[dict]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT fiscal_year, fiscal_quarter, revenue, operating_profit,
                   net_income, per, pbr, roe, debt_ratio
            FROM financial_cache WHERE symbol=$1
            ORDER BY fiscal_year DESC, fiscal_quarter DESC NULLS LAST LIMIT 4
        """, symbol)
    return [dict(r) for r in rows]


async def fetch_news_context(symbol: str, limit: int = 10) -> list[dict]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT id, title, summary, source_url, source_name, published_at
            FROM news_cache WHERE symbol=$1 AND ttl_until > now()
            ORDER BY published_at DESC LIMIT $2
        """, symbol, limit)
    return [dict(r) for r in rows]


async def fetch_filing_context(symbol: str, limit: int = 5) -> list[dict]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT id, filing_title, filing_type, summary, source_url, disclosed_at
            FROM filing_cache WHERE symbol=$1 AND ttl_until > now()
            ORDER BY disclosed_at DESC LIMIT $2
        """, symbol, limit)
    return [dict(r) for r in rows]


async def fetch_event_timeline(symbol: str, limit: int = 5) -> list[dict]:
    """DB팀이 추가한 event_timeline 테이블"""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT event_date, event_title, event_type, event_status, description, source_url
            FROM event_timeline WHERE symbol=$1
            ORDER BY event_date DESC LIMIT $2
        """, symbol, limit)
    return [dict(r) for r in rows]


# ══════════════════════════════════════════════════════════
# 쓰기
# ══════════════════════════════════════════════════════════

async def update_session_status(session_id: str, status: str, error_message: str | None = None):
    pool = await get_pool()
    async with pool.acquire() as conn:
        if status == "completed":
            await conn.execute("""
                UPDATE debate_session
                SET status=$1, completed_at=now(), error_message=$2
                WHERE id=$3
            """, status, error_message, UUID(session_id))
        else:
            await conn.execute("""
                UPDATE debate_session SET status=$1, error_message=$2 WHERE id=$3
            """, status, error_message, UUID(session_id))
    logger.info(f"[db] 세션 {session_id} → {status}")


async def fail_session_if_running(session_id: str, error_message: str | None = None):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("""
            UPDATE debate_session
               SET status='failed', error_message=$2
             WHERE id=$1 AND status='running'
        """, UUID(session_id), error_message)


async def save_statement(
    session_id: str, round_: str, round_order: int, agent_role: str,
    content: str, model_name: str,
    prompt_tokens: int | None = None,
    completion_tokens: int | None = None,
    latency_ms: int | None = None,
) -> str:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("""
            INSERT INTO agent_statement
              (session_id, round, round_order, agent_role, content,
               model_name, prompt_tokens, completion_tokens, latency_ms)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
            RETURNING id
        """, UUID(session_id), round_, round_order, agent_role,
            content, model_name, prompt_tokens, completion_tokens, latency_ms)
    stmt_id = str(row["id"])
    logger.info(f"[db] statement 저장: {agent_role}/{round_} id={stmt_id}")
    return stmt_id


async def save_evidence(
    statement_id: str, source_type: str, excerpt: str,
    source_url: str | None = None, source_label: str | None = None,
    source_title: str | None = None,
    news_cache_id: str | None = None,
    filing_cache_id: str | None = None,
    price_cache_id: str | None = None,
    financial_cache_id: str | None = None,
    technical_indicator_cache_id: str | None = None,
):
    if not source_url and not source_label:
        logger.warning("[db] evidence source 없음 — 저장 스킵")
        return

    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO evidence
              (statement_id, source_type, source_url, source_label,
               source_title, retrieved_at, excerpt,
               news_cache_id, filing_cache_id, price_cache_id,
               financial_cache_id, technical_indicator_cache_id)
            VALUES ($1,$2,$3,$4,$5,now(),$6,$7,$8,$9,$10,$11)
        """, UUID(statement_id), source_type, source_url, source_label,
            source_title, excerpt,
            UUID(news_cache_id)                if news_cache_id else None,
            UUID(filing_cache_id)              if filing_cache_id else None,
            UUID(price_cache_id)               if price_cache_id else None,
            UUID(financial_cache_id)           if financial_cache_id else None,
            UUID(technical_indicator_cache_id) if technical_indicator_cache_id else None,
        )


async def save_moderator_summary(session_id: str, summary_content: str, key_points: list[str]):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO moderator_summary (session_id, summary_content, key_points)
            VALUES ($1,$2,$3)
            ON CONFLICT (session_id) DO UPDATE
              SET summary_content=EXCLUDED.summary_content,
                  key_points=EXCLUDED.key_points
        """, UUID(session_id), summary_content, json.dumps(key_points, ensure_ascii=False))
    logger.info(f"[db] moderator_summary 저장: {session_id}")
