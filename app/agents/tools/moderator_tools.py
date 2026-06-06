"""
사회자 전용 DB 조회 툴 — 확정 수치만 반환, LLM 추론 없음.
사회자가 발언 정정 시 이 툴로 조회한 값만 사용하도록 강제.
"""
from __future__ import annotations
import asyncio
from langchain_core.tools import tool


@tool
def get_financial_data(symbol: str, fiscal_year: int, fiscal_quarter: int | None = None) -> dict:
    """
    종목의 특정 분기/연도 재무 데이터를 DB에서 직접 조회합니다.
    매출(revenue), 영업이익(operating_profit), 당기순이익(net_income), ROE, PER, PBR을 반환합니다.
    수치 검증 시 반드시 이 툴로 확인하세요.

    Args:
        symbol: 종목 코드 (예: '005930')
        fiscal_year: 회계연도 (예: 2026)
        fiscal_quarter: 분기 (1~4, None이면 연간)
    """
    async def _query():
        from app.core.database import get_pool
        pool = await get_pool()
        async with pool.acquire() as conn:
            if fiscal_quarter is not None:
                row = await conn.fetchrow("""
                    SELECT fiscal_year, fiscal_quarter,
                           revenue, operating_profit, net_income,
                           per, pbr, roe, debt_ratio
                    FROM financial_cache
                    WHERE symbol=$1 AND fiscal_year=$2 AND fiscal_quarter=$3
                """, symbol, fiscal_year, fiscal_quarter)
            else:
                row = await conn.fetchrow("""
                    SELECT fiscal_year, fiscal_quarter,
                           revenue, operating_profit, net_income,
                           per, pbr, roe, debt_ratio
                    FROM financial_cache
                    WHERE symbol=$1 AND fiscal_year=$2 AND fiscal_quarter IS NULL
                """, symbol, fiscal_year)
        return dict(row) if row else {}

    result = asyncio.get_event_loop().run_until_complete(_query())
    if not result:
        return {"error": f"{symbol} {fiscal_year}{'Q'+str(fiscal_quarter) if fiscal_quarter else ''} 데이터 없음"}

    def fmt(v):
        return f"{float(v)/1e8:.0f}억" if v is not None else "N/A"

    return {
        "period":           f"{result['fiscal_year']}{'Q'+str(result['fiscal_quarter']) if result.get('fiscal_quarter') else ''}",
        "revenue":          fmt(result.get("revenue")),
        "operating_profit": fmt(result.get("operating_profit")),
        "net_income":       fmt(result.get("net_income")),
        "roe":              f"{float(result['roe'])*100:.2f}%" if result.get("roe") else "N/A",
        "per":              str(result.get("per", "N/A")),
        "pbr":              str(result.get("pbr", "N/A")),
        "raw": {
            "revenue":          float(result["revenue"]) if result.get("revenue") else None,
            "operating_profit": float(result["operating_profit"]) if result.get("operating_profit") else None,
            "net_income":       float(result["net_income"]) if result.get("net_income") else None,
        }
    }


@tool
def get_price_data(symbol: str) -> dict:
    """
    종목의 최신 주가 및 기술적 지표를 DB에서 직접 조회합니다.
    현재가, 등락률, MA20, MA60, RSI14, MACD를 반환합니다.
    수치 검증 시 반드시 이 툴로 확인하세요.

    Args:
        symbol: 종목 코드 (예: '005930')
    """
    async def _query():
        from app.core.database import get_pool
        pool = await get_pool()
        async with pool.acquire() as conn:
            price = await conn.fetchrow("""
                SELECT close_price, change_rate, volume
                FROM price_cache WHERE symbol=$1
                ORDER BY price_date DESC LIMIT 1
            """, symbol)
            tech = await conn.fetchrow("""
                SELECT ma20, ma60, rsi14, macd, macd_signal
                FROM technical_indicator_cache WHERE symbol=$1
                ORDER BY indicator_date DESC LIMIT 1
            """, symbol)
        return dict(price) if price else {}, dict(tech) if tech else {}

    price, tech = asyncio.get_event_loop().run_until_complete(_query())
    if not price:
        return {"error": f"{symbol} 가격 데이터 없음"}

    def f(d, k): return float(d[k]) if d.get(k) is not None else None

    return {
        "close_price": f(price, "close_price"),
        "change_rate": f(price, "change_rate"),
        "volume":      int(price["volume"]) if price.get("volume") else None,
        "ma20":        f(tech, "ma20"),
        "ma60":        f(tech, "ma60"),
        "rsi14":       f(tech, "rsi14"),
        "macd":        f(tech, "macd"),
        "macd_signal": f(tech, "macd_signal"),
    }
