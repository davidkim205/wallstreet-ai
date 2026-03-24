from datetime import datetime
import yfinance as yf
from typing import Any

def _safe_float(val):
    try:
        v = float(val)
        return None if v != v else v
    except (TypeError, ValueError):
        return None

def _extract_income_row(df, col, row_names):
    for row in row_names:
        if row in df.index:
            v = df.loc[row, col]
            if v is not None and v == v:
                return float(v)
    return None

def _calc_yoy(current_val, prev_val):
    if current_val and prev_val and prev_val != 0:
        return round((current_val / prev_val - 1) * 100, 1)
    return None

def _operating_margin(revenue, op_income):
    if op_income and revenue and revenue != 0:
        return round(op_income / revenue * 100, 1)
    return None

def parse_quarterly_financials(qf):
    records = []
    for col in qf.columns[:8]:
        dt      = col if isinstance(col, datetime) else col.to_pydatetime()
        m2q     = {3: 1, 6: 2, 9: 3, 12: 4}
        quarter = m2q.get(dt.month, (dt.month - 1) // 3 + 1)
        revenue = _extract_income_row(qf, col, ["Total Revenue", "Revenue", "totalRevenue"])
        op_inc  = _extract_income_row(qf, col, ["Operating Income", "EBIT", "operatingIncome"])
        net_inc = _extract_income_row(qf, col, ["Net Income", "netIncome", "Net Income Common Stockholders"])
        gross_p = _extract_income_row(qf, col, ["Gross Profit", "grossProfit"])
        records.append({
            "period":             f"{dt.year}Q{quarter}",
            "date":               dt.strftime("%Y-%m-%d"),
            "year":               dt.year,
            "quarter":            quarter,
            "revenue":            revenue,
            "revenue_b":          round(revenue  / 1e12, 2) if revenue  else None,
            "operating_income":   op_inc,
            "operating_income_b": round(op_inc   / 1e12, 2) if op_inc   else None,
            "net_income":         net_inc,
            "net_income_b":       round(net_inc  / 1e12, 2) if net_inc  else None,
            "gross_profit":       gross_p,
            "operating_margin":   _operating_margin(revenue, op_inc),
            "net_margin":         _operating_margin(revenue, net_inc),
        })
    return records

def attach_yoy_growth(quarterly_records):
    q_map = {e["period"]: e for e in quarterly_records}
    for entry in quarterly_records:
        prev = q_map.get(f"{entry['year']-1}Q{entry['quarter']}")
        if prev:
            entry["revenue_yoy_pct"]   = _calc_yoy(entry["revenue"],          prev["revenue"])
            entry["op_income_yoy_pct"] = _calc_yoy(entry["operating_income"],  prev["operating_income"])

def parse_annual_financials(af):
    records = []
    for col in af.columns[:4]:
        dt      = col if isinstance(col, datetime) else col.to_pydatetime()
        revenue = _extract_income_row(af, col, ["Total Revenue", "Revenue"])
        op_inc  = _extract_income_row(af, col, ["Operating Income", "EBIT"])
        net_inc = _extract_income_row(af, col, ["Net Income", "Net Income Common Stockholders"])
        records.append({
            "year":               dt.year,
            "revenue":            revenue,
            "revenue_t":          round(revenue / 1e12, 2) if revenue else None,
            "operating_income":   op_inc,
            "operating_income_t": round(op_inc  / 1e12, 2) if op_inc  else None,
            "net_income":         net_inc,
            "net_income_t":       round(net_inc / 1e12, 2) if net_inc else None,
            "operating_margin":   _operating_margin(revenue, op_inc),
        })
    return records

def fetch_earnings_surprise(stock):
    surprises = []
    qe = stock.quarterly_earnings
    if qe is None or qe.empty:
        return surprises
    for idx, row in qe.head(6).iterrows():
        actual   = _safe_float(row.get("Actual"))
        estimate = _safe_float(row.get("Estimate"))
        surp     = None
        if estimate and actual and estimate != 0:
            surp = round((actual / estimate - 1) * 100, 1)
        surprises.append({
            "period":       str(idx),
            "eps_actual":   actual,
            "eps_estimate": estimate,
            "surprise_pct": surp,
        })
    return surprises

def fetch_next_earnings_date(stock):
    try:
        cal = stock.calendar
        if cal is None:
            return None
        if isinstance(cal, dict):
            ed = cal.get("Earnings Date")
            return str(ed[0] if isinstance(ed, list) else ed)[:10] if ed else None
        if hasattr(cal, "loc"):
            return str(cal.loc["Earnings Date"].iloc[0])[:10]
    except Exception:
        pass
    return None

def filter_target_earnings(quarterly, annual, target_year, target_quarter):
    if target_quarter:
        target  = f"{target_year}Q{target_quarter}"
        matched = [e for e in quarterly if e["period"] == target]
        return {"type": "quarter", "period": target,
                "data": matched[0] if matched else None, "found": bool(matched)}
    matched = [e for e in annual if e["year"] == target_year]
    return {"type": "annual", "period": str(target_year),
            "data": matched[0] if matched else None, "found": bool(matched)}

def fetch_earnings_data(ticker, target_year=None, target_quarter=None):
    result: dict[str, Any] = {
        "ticker":            ticker,
        "target_year":       target_year,
        "target_quarter":    target_quarter,
        "quarterly_results": [],
        "annual_results":    [],
        "earnings_surprise": [],
        "next_earnings_date": None,
        "errors":            []
    }
    try:
        stock = yf.Ticker(ticker)
        try:
            qf = stock.quarterly_financials
            if qf is not None and not qf.empty:
                result["quarterly_results"] = parse_quarterly_financials(qf)
                attach_yoy_growth(result["quarterly_results"])
        except Exception as e:
            result["errors"].append(f"분기 재무제표 오류: {e}")
        try:
            af = stock.financials
            if af is not None and not af.empty:
                result["annual_results"] = parse_annual_financials(af)
        except Exception as e:
            result["errors"].append(f"연간 재무제표 오류: {e}")
        try:
            result["earnings_surprise"] = fetch_earnings_surprise(stock)
        except Exception as e:
            result["errors"].append(f"EPS 서프라이즈 오류: {e}")
        try:
            result["next_earnings_date"] = fetch_next_earnings_date(stock)
        except Exception as e:
            result["errors"].append(f"실적 발표일 오류: {e}")
    except Exception as e:
        result["errors"].append(f"전체 실적 수집 오류: {e}")
    if target_year:
        result["filtered_quarter"] = filter_target_earnings(
            result["quarterly_results"],
            result["annual_results"],
            target_year,
            target_quarter
        )
    print(
        f"    → 분기 실적: {len(result['quarterly_results'])}개 / "
        f"연간 실적: {len(result['annual_results'])}개 / "
        f"EPS 서프라이즈: {len(result['earnings_surprise'])}개"
    )
    return result
