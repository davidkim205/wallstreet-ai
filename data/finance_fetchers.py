import yfinance as yf
from datetime import datetime
from typing import Any

def fetch_price_data(ticker, period="1y"):
    stock = yf.Ticker(ticker)
    hist  = stock.history(period=period)
    if hist.empty:
        return {}
    close     = hist["Close"]
    current   = close.iloc[-1]
    week_ago  = close.iloc[-6]  if len(close) >= 6  else close.iloc[0]
    month_ago = close.iloc[-22] if len(close) >= 22 else close.iloc[0]
    year_start = close.iloc[0]
    return {
        "current_price":  round(float(current), 2),
        "change_1w_pct":  round((current / week_ago   - 1) * 100, 2),
        "change_1m_pct":  round((current / month_ago  - 1) * 100, 2),
        "change_ytd_pct": round((current / year_start - 1) * 100, 2),
        "52w_high":       round(float(close.max()), 2),
        "52w_low":        round(float(close.min()), 2),
        "avg_volume_30d": int(hist["Volume"].tail(30).mean()),
    }

def fetch_fundamentals(ticker):
    info = yf.Ticker(ticker).info or {}
    def safe(key, default=None):
        val = info.get(key, default)
        return default if (val is None or val != val) else val
    return {
        "company_name":   safe("longName", ticker),
        "sector":         safe("sector", "N/A"),
        "industry":       safe("industry", "N/A"),
        "market_cap_b":   round(safe("marketCap", 0) / 1e9, 2),
        "pe_ratio":       safe("trailingPE"),
        "forward_pe":     safe("forwardPE"),
        "pb_ratio":       safe("priceToBook"),
        "ps_ratio":       safe("priceToSalesTrailing12Months"),
        "roe":            safe("returnOnEquity"),
        "roa":            safe("returnOnAssets"),
        "profit_margin":  safe("profitMargins"),
        "revenue_growth": safe("revenueGrowth"),
        "earnings_growth":safe("earningsGrowth"),
        "debt_to_equity": safe("debtToEquity"),
        "current_ratio":  safe("currentRatio"),
        "dividend_yield": safe("dividendYield"),
        "analyst_target": safe("targetMeanPrice"),
        "recommendation": safe("recommendationKey"),
        "beta":           safe("beta"),
        "description":    (safe("longBusinessSummary") or "")[:400],
    }

def fetch_news(ticker, max_items=8):
    try:
        raw_news = yf.Ticker(ticker).news or []
        snippets = []
        for item in raw_news[:max_items]:
            content = item.get("content", {})
            title   = content.get("title") or item.get("title", "")
            summary = content.get("summary") or ""
            pub_raw = content.get("pubDate") or item.get("providerPublishTime", "")
            pub_dt  = _parse_pub_date(pub_raw)
            if title:
                line = f"[{pub_dt}] {title}"
                if summary:
                    line += f" — {summary[:120]}"
                snippets.append(line)
        return snippets
    except Exception as e:
        print(f"    뉴스 수집 오류: {e}")
        return []

def _parse_pub_date(pub_raw):
    if isinstance(pub_raw, int):
        return datetime.fromtimestamp(pub_raw).strftime("%Y-%m-%d")
    if isinstance(pub_raw, str) and pub_raw:
        return pub_raw[:10]
    return "날짜 미상"
