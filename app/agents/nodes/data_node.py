# app/agents/nodes/data_node.py
"""Data Agent — DB 캐시 우선 조회, 없으면 yfinance 폴백"""
from __future__ import annotations
import logging
from app.agents.state import DebateState
from app.repositories.debate_repo import (
    fetch_price_context, fetch_financial_context,
    fetch_news_context, fetch_filing_context, fetch_event_timeline,
)

logger = logging.getLogger(__name__)


async def data_agent_node(state: DebateState) -> dict:
    symbol = state["symbol"]
    logger.info(f"[data_agent] {symbol} 수집 시작")

    raw      = await fetch_price_context(symbol)
    finance  = await fetch_financial_context(symbol)
    news     = await fetch_news_context(symbol)
    filings  = await fetch_filing_context(symbol)
    events   = await fetch_event_timeline(symbol)

    price = raw.get("price", {})
    tech  = raw.get("tech",  {})

    if not price:
        logger.warning(f"[data_agent] {symbol} DB 데이터 없음 → yfinance 폴백")
        price, tech = _yfinance_fallback(symbol)

    return {
        "price_context":     _fmt_price(symbol, price, tech, events),
        "financial_context": _fmt_finance(finance),
        "news_chunks":       [n.get("title","") + " " + (n.get("summary") or "") for n in news]
                           + [f.get("filing_title","") + " " + (f.get("summary") or "") for f in filings],
    }


def _fmt_price(symbol, price, tech, events) -> str:
    close  = price.get("close_price", "N/A")
    change = price.get("change_rate", "N/A")
    vol    = price.get("volume", "N/A")
    ma20   = tech.get("ma20", "N/A")
    ma60   = tech.get("ma60", "N/A")
    rsi    = tech.get("rsi14", "N/A")
    macd   = tech.get("macd", "N/A")
    msig   = tech.get("macd_signal", "N/A")

    try:
        rsi_s = "과매수" if float(rsi) > 70 else ("과매도" if float(rsi) < 30 else "중립")
    except Exception:
        rsi_s = "N/A"

    evts = ""
    if events:
        evts = "\n[이벤트]\n" + "\n".join(
            f"  {e['event_date']} {e['event_title']} ({e['event_type']})" for e in events
        )

    return (
        f"[가격/기술지표] {symbol}\n"
        f"- 현재가: {close} / 등락: {change}% / 거래량: {vol}\n"
        f"- MA20: {ma20} / MA60: {ma60}\n"
        f"- RSI14: {rsi} ({rsi_s})\n"
        f"- MACD: {macd} / 시그널: {msig}{evts}"
    ).strip()


def _fmt_finance(rows) -> str:
    if not rows:
        return "[재무] 데이터 없음"
    lines = ["[재무지표]"]
    for r in rows:
        p = f"{r['fiscal_year']}" + (f"Q{r['fiscal_quarter']}" if r.get("fiscal_quarter") else "")
        def f(k): return f"{float(r[k])/1e8:.0f}억" if r.get(k) else "N/A"
        lines.append(
            f"  {p}: 매출 {f('revenue')} 영익 {f('operating_profit')} 순익 {f('net_income')} "
            f"| PER {r.get('per','N/A')} PBR {r.get('pbr','N/A')} ROE {r.get('roe','N/A')}"
        )
    return "\n".join(lines)


def _yfinance_fallback(symbol):
    try:
        import yfinance as yf
        t    = yf.Ticker(symbol)
        hist = t.history(period="6mo")
        if hist.empty: return {}, {}
        cl   = hist["Close"]
        l    = hist.iloc[-1]; p = hist.iloc[-2]
        price = {"close_price": float(l["Close"]), "change_rate": round((l["Close"]-p["Close"])/p["Close"]*100,2), "volume": int(l["Volume"])}
        tech  = {}
        if len(cl) >= 20: tech["ma20"] = round(float(cl.rolling(20).mean().iloc[-1]),2)
        if len(cl) >= 60: tech["ma60"] = round(float(cl.rolling(60).mean().iloc[-1]),2)
        if len(cl) >= 14:
            d = cl.diff(); g = d.clip(lower=0).rolling(14).mean(); ls = (-d.clip(upper=0)).rolling(14).mean()
            tech["rsi14"] = round(float(100-100/(1+g.iloc[-1]/ls.iloc[-1])),2)
        e12 = cl.ewm(span=12).mean(); e26 = cl.ewm(span=26).mean()
        tech["macd"] = round(float((e12-e26).iloc[-1]),4)
        tech["macd_signal"] = round(float((e12-e26).ewm(span=9).mean().iloc[-1]),4)
        return price, tech
    except Exception as e:
        logger.error(f"[yfinance] {e}")
        return {}, {}
