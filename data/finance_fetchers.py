import yfinance as yf
from datetime import datetime
from typing import Any


def _safe_value(data, key, default=None):
    val = data.get(key, default)
    return default if (val is None or val != val) else val


def _to_billions(value):
    if value is None:
        return None
    try:
        return round(float(value) / 1e9, 2)
    except (TypeError, ValueError):
        return None


def _format_date_yyyymmdd(value):
    if value is None:
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value).strftime("%Y-%m-%d")
        except (OverflowError, OSError, ValueError):
            return None
    text = str(value).strip()
    return text[:10] if text else None


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
    return {
        "company_name":             _safe_value(info, "longName", ticker),
        "symbol":                   _safe_value(info, "symbol", ticker),
        "exchange":                 _safe_value(info, "exchange", "N/A"),
        "quote_type":               _safe_value(info, "quoteType"),
        "currency":                 _safe_value(info, "currency"),
        "sector":                   _safe_value(info, "sector", "N/A"),
        "industry":                 _safe_value(info, "industry", "N/A"),
        "country":                  _safe_value(info, "country"),
        "city":                     _safe_value(info, "city"),
        "website":                  _safe_value(info, "website"),
        "full_time_employees":      _safe_value(info, "fullTimeEmployees"),
        "market_cap_b":             _to_billions(_safe_value(info, "marketCap")),
        "enterprise_value_b":       _to_billions(_safe_value(info, "enterpriseValue")),
        "shares_outstanding_b":     _to_billions(_safe_value(info, "sharesOutstanding")),
        "float_shares_b":           _to_billions(_safe_value(info, "floatShares")),
        "pe_ratio":                 _safe_value(info, "trailingPE"),
        "forward_pe":               _safe_value(info, "forwardPE"),
        "pb_ratio":                 _safe_value(info, "priceToBook"),
        "ps_ratio":                 _safe_value(info, "priceToSalesTrailing12Months"),
        "ev_to_ebitda":             _safe_value(info, "enterpriseToEbitda"),
        "trailing_eps":             _safe_value(info, "trailingEps"),
        "forward_eps":              _safe_value(info, "forwardEps"),
        "roe":                      _safe_value(info, "returnOnEquity"),
        "roa":                      _safe_value(info, "returnOnAssets"),
        "gross_margin":             _safe_value(info, "grossMargins"),
        "ebitda_margin":            _safe_value(info, "ebitdaMargins"),
        "operating_margin":         _safe_value(info, "operatingMargins"),
        "profit_margin":            _safe_value(info, "profitMargins"),
        "revenue_growth":           _safe_value(info, "revenueGrowth"),
        "earnings_growth":          _safe_value(info, "earningsGrowth"),
        "debt_to_equity":           _safe_value(info, "debtToEquity"),
        "current_ratio":            _safe_value(info, "currentRatio"),
        "quick_ratio":              _safe_value(info, "quickRatio"),
        "total_cash_b":             _to_billions(_safe_value(info, "totalCash")),
        "total_debt_b":             _to_billions(_safe_value(info, "totalDebt")),
        "operating_cashflow_b":     _to_billions(_safe_value(info, "operatingCashflow")),
        "free_cashflow_b":          _to_billions(_safe_value(info, "freeCashflow")),
        "dividend_rate":            _safe_value(info, "dividendRate"),
        "dividend_yield":           _safe_value(info, "dividendYield"),
        "payout_ratio":             _safe_value(info, "payoutRatio"),
        "ex_dividend_date":         _format_date_yyyymmdd(_safe_value(info, "exDividendDate")),
        "held_percent_insiders":    _safe_value(info, "heldPercentInsiders"),
        "held_percent_institutions": _safe_value(info, "heldPercentInstitutions"),
        "short_ratio":              _safe_value(info, "shortRatio"),
        "short_percent_float":      _safe_value(info, "shortPercentOfFloat"),
        "analyst_target":           _safe_value(info, "targetMeanPrice"),
        "target_high_price":        _safe_value(info, "targetHighPrice"),
        "target_low_price":         _safe_value(info, "targetLowPrice"),
        "analyst_opinion_count":    _safe_value(info, "numberOfAnalystOpinions"),
        "recommendation":           _safe_value(info, "recommendationKey"),
        "beta":                     _safe_value(info, "beta"),
        "description":              (_safe_value(info, "longBusinessSummary") or "")[:400],
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
