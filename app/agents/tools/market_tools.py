# app/agents/tools/market_tools.py
"""yfinance 기반 시장 데이터 도구 (DB 캐시 없을 때 폴백용)"""
from datetime import date
from langchain_core.tools import tool
import yfinance as yf


@tool
def get_stock_price(symbol: str) -> dict:
    """현재 주가, 등락률, 52주 고저, 거래량을 반환합니다."""
    try:
        ticker = yf.Ticker(symbol)
        hist   = ticker.history(period="5d")
        info   = ticker.info
        if hist.empty:
            return {"error": f"{symbol} 데이터 없음"}

        latest = hist.iloc[-1]
        prev   = hist.iloc[-2] if len(hist) > 1 else hist.iloc[-1]
        change = (latest["Close"] - prev["Close"]) / prev["Close"] * 100

        return {
            "close":        round(float(latest["Close"]), 2),
            "change_rate":  round(float(change), 2),
            "volume":       int(latest["Volume"]),
            "week52_high":  info.get("fiftyTwoWeekHigh"),
            "week52_low":   info.get("fiftyTwoWeekLow"),
            "source_label": f"yfinance — {symbol} 일봉 ({date.today()})",
            "source_url":   None,
        }
    except Exception as e:
        return {"error": str(e)}


@tool
def get_financial_metrics(symbol: str) -> dict:
    """PER, PBR, ROE, 부채비율 등 핵심 재무지표를 반환합니다."""
    try:
        info = yf.Ticker(symbol).info
        return {
            "per":            info.get("trailingPE"),
            "forward_per":    info.get("forwardPE"),
            "pbr":            info.get("priceToBook"),
            "roe":            info.get("returnOnEquity"),
            "revenue_growth": info.get("revenueGrowth"),
            "debt_to_equity": info.get("debtToEquity"),
            "operating_margin": info.get("operatingMargins"),
            "source_label":   f"yfinance — {symbol} 재무지표 ({date.today()})",
            "source_url":     None,
        }
    except Exception as e:
        return {"error": str(e)}


@tool
def get_technical_indicators(symbol: str) -> dict:
    """MA20/60/120, RSI14, MACD를 계산하여 반환합니다."""
    try:
        hist = yf.Ticker(symbol).history(period="6mo")
        if len(hist) < 20:
            return {"error": "데이터 부족"}

        close  = hist["Close"]
        ma20   = float(close.rolling(20).mean().iloc[-1])
        ma60   = float(close.rolling(60).mean().iloc[-1])   if len(hist) >= 60  else None
        ma120  = float(close.rolling(120).mean().iloc[-1])  if len(hist) >= 120 else None
        delta  = close.diff()
        gain   = delta.clip(lower=0).rolling(14).mean()
        loss   = (-delta.clip(upper=0)).rolling(14).mean()
        rsi    = float(100 - 100 / (1 + gain.iloc[-1] / loss.iloc[-1]))
        ema12  = close.ewm(span=12).mean()
        ema26  = close.ewm(span=26).mean()
        macd   = float((ema12 - ema26).iloc[-1])
        signal = float((ema12 - ema26).ewm(span=9).mean().iloc[-1])
        cur    = float(close.iloc[-1])

        return {
            "current_price": round(cur, 2),
            "ma20":          round(ma20, 2),
            "ma60":          round(ma60, 2)  if ma60  else None,
            "ma120":         round(ma120, 2) if ma120 else None,
            "above_ma20":    cur > ma20,
            "rsi14":         round(rsi, 2),
            "rsi_signal":    "과매수" if rsi > 70 else ("과매도" if rsi < 30 else "중립"),
            "macd":          round(macd, 4),
            "macd_signal":   round(signal, 4),
            "macd_bullish":  macd > signal,
            "source_label":  f"TA 계산 — {symbol} 기술지표 ({date.today()})",
            "source_url":    None,
        }
    except Exception as e:
        return {"error": str(e)}
