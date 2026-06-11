# app/agents/nodes/data_node.py
"""Data Agent — DB 캐시 우선 조회, 없으면 yfinance 폴백"""
from __future__ import annotations
import asyncio
import logging
from app.agents.state import DebateState
from app.domain.evidence_retrieval import (
    build_category_query,
    format_evidence_context,
    search_evidence_for_symbol,
)
from app.domain.intraday_quote import IntradayQuoteService
from app.external.yfinance_symbol import resolve_yfinance_symbol
from app.repositories.debate_repo import (
    fetch_price_context, fetch_financial_context,
    fetch_news_context, fetch_filing_context, fetch_event_timeline,
)

logger = logging.getLogger(__name__)


async def data_agent_node(state: DebateState) -> dict:
    symbol = state["symbol"]
    logger.info(f"[data_agent] {symbol} 수집 시작")

    raw, finance, news, filings, events = await asyncio.gather(
        fetch_price_context(symbol),
        fetch_financial_context(symbol),
        fetch_news_context(symbol),
        fetch_filing_context(symbol),
        fetch_event_timeline(symbol),
    )
    evidence_query = build_category_query(
        symbol=symbol,
        symbol_name=state["symbol_name"],
        category=state["category"],
    )
    evidences = await asyncio.to_thread(
        search_evidence_for_symbol,
        query=evidence_query,
        symbol=symbol,
        top_k=4,
    )

    price = raw.get("price", {})
    tech  = raw.get("tech",  {})

    if not price:
        logger.warning(f"[data_agent] {symbol} DB 데이터 없음 → yfinance 폴백")
        price, tech = _yfinance_fallback(symbol)

    return {
        "price_context":     _fmt_price(symbol, price, tech, events),
        "financial_context": _fmt_finance(finance),
        "evidence_context":  format_evidence_context(evidences),
        # ChromaDB 없으면 PostgreSQL news/filings로 폴백 (RAGAS 평가에도 동일 적용)
        "initial_evidences": evidences or [
            {"excerpt": n.get("summary") or n.get("title", ""), "source_title": n.get("title", "")}
            for n in news if n.get("summary") or n.get("title")
        ] + [
            {"excerpt": f.get("summary") or f.get("filing_title", ""), "source_title": f.get("filing_title", "")}
            for f in filings if f.get("summary") or f.get("filing_title")
        ],
        "news_chunks":       [ev.get("source_title", "") + " " + (ev.get("excerpt") or "") for ev in evidences]
                           or [n.get("title","") + " " + (n.get("summary") or "") for n in news]
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

    def val(r, k):
        v = r.get(k)
        return float(v) if v is not None else None

    def fmt_val(v):
        return f"{v/1e8:.0f}억" if v is not None else "N/A"

    def chg(curr, prev):
        if curr is None or prev is None or prev == 0:
            return ""
        rate = (curr - prev) / abs(prev) * 100
        sign = "▲" if rate > 0 else "▼"
        return f" ({sign}{abs(rate):.1f}% QoQ)"

    lines = ["[재무지표] ※ QoQ는 직전 분기 대비 증감률"]
    for i, r in enumerate(rows):
        p = f"{r['fiscal_year']}" + (f"Q{r['fiscal_quarter']}" if r.get("fiscal_quarter") else "")
        prev = rows[i + 1] if i + 1 < len(rows) else None

        rev  = val(r, "revenue");          prev_rev  = val(prev, "revenue")  if prev else None
        opi  = val(r, "operating_profit"); prev_opi  = val(prev, "operating_profit") if prev else None
        ni   = val(r, "net_income");       prev_ni   = val(prev, "net_income") if prev else None

        lines.append(
            f"  {p}: "
            f"매출 {fmt_val(rev)}{chg(rev, prev_rev)} / "
            f"영업이익(영익) {fmt_val(opi)}{chg(opi, prev_opi)} / "
            f"당기순이익(순익) {fmt_val(ni)}{chg(ni, prev_ni)} "
            f"| PER {r.get('per','N/A')} PBR {r.get('pbr','N/A')} "
            f"ROE {str(round(float(r['roe'])*100, 2))+'%' if r.get('roe') else 'N/A'}"
        )
    return "\n".join(lines)


def _yfinance_fallback(symbol):
    try:
        import time
        import yfinance as yf

        time.sleep(1)  # rate limit 회피
        quote = IntradayQuoteService().get_latest_quote(symbol)
        t = yf.Ticker(resolve_yfinance_symbol(symbol))
        hist = t.history(period="6mo")
        if hist.empty:
            return {}, {}
        cl = hist["Close"]
        price = {
            "close_price": quote.price,
            "change_rate": quote.change_rate,
            "volume": quote.volume,
        }
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
