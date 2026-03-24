# context/context_builder.py
"""
LLM 입력용 컨텍스트 빌더 함수 모음
"""

def _fmt_krw(val):
    return "N/A" if val is None else f"{val:.2f}"


def _pct(val):
    if val is None or val != val:
        return "N/A"
    return f"{float(val) * 100:.1f}%"


def _upside(current, target):
    if not current or not target:
        return "N/A"
    return f"{(float(target) / float(current) - 1) * 100:.1f}%"


def build_company_section(fd, ticker):
    return f"""[기업 정보]\n회사명: {fd.get('company_name', ticker)}\n섹터: {fd.get('sector', 'N/A')} / {fd.get('industry', 'N/A')}\n시가총액: ${fd.get('market_cap_b', 'N/A')}B\n사업 설명: {fd.get('description', 'N/A')}"""


def build_price_section(pd):
    return f"""[주가 퍼포먼스]\n현재가: ${pd.get('current_price', 'N/A')}\n1주 수익률: {pd.get('change_1w_pct', 'N/A')}%  /  1개월: {pd.get('change_1m_pct', 'N/A')}%  /  YTD: {pd.get('change_ytd_pct', 'N/A')}%\n52주 고가: ${pd.get('52w_high', 'N/A')} / 저가: ${pd.get('52w_low', 'N/A')}\n30일 평균 거래량: {pd.get('avg_volume_30d', 'N/A'):,}"""


def build_valuation_section(fd):
    return f"""[밸류에이션 & 재무]\nPER(TTM): {fd.get('pe_ratio', 'N/A')} / 선행 PER: {fd.get('forward_pe', 'N/A')}\nPBR: {fd.get('pb_ratio', 'N/A')} / PSR: {fd.get('ps_ratio', 'N/A')}\nROE: {_pct(fd.get('roe'))} / ROA: {_pct(fd.get('roa'))}\n순이익률: {_pct(fd.get('profit_margin'))}\n매출 성장률: {_pct(fd.get('revenue_growth'))} / 이익 성장률: {_pct(fd.get('earnings_growth'))}\n부채비율: {fd.get('debt_to_equity', 'N/A')} / 유동비율: {fd.get('current_ratio', 'N/A')}\n배당수익률: {_pct(fd.get('dividend_yield'))} / 베타: {fd.get('beta', 'N/A')}"""


def build_analyst_section(fd, pd):
    rec = fd.get("recommendation", "")
    return f"""[애널리스트 컨센서스]\n평균 목표주가: ${fd.get('analyst_target', 'N/A')}\n투자의견: {rec.upper() if rec else 'N/A'}\n현재가 대비 상승여력: {_upside(pd.get('current_price'), fd.get('analyst_target'))}"""


def build_technicals_section(td):
    return f"""[기술적 지표]\nMA20: ${td.get('ma20', 'N/A')} (현재가 MA20 {td.get('price_vs_ma20', 'N/A')})\nMA50: ${td.get('ma50', 'N/A')} / MA200: ${td.get('ma200', 'N/A')}\nRSI(14): {td.get('rsi_14', 'N/A')} → {td.get('rsi_signal', 'N/A')}\nMACD 히스토그램: {td.get('macd_histogram', 'N/A')} → {td.get('macd_signal', 'N/A')}\n볼린저 밴드 위치: {td.get('bb_position', 'N/A')}\n거래량 비율(vs 20일 평균): {td.get('volume_ratio', 'N/A')}x"""


def build_earnings_section(ed, intent):
    lines = ["[실적 데이터]"]
    if ed.get("next_earnings_date"):
        lines.append(f"다음 실적 발표 예정일: {ed['next_earnings_date']}")
    filtered = ed.get("filtered_quarter")
    if filtered and filtered.get("found") and filtered.get("data"):
        lines.extend(_format_filtered_earnings(filtered))
    elif filtered and not filtered.get("found"):
        lines.append(f"\n※ {filtered['period']} 실적 데이터를 찾을 수 없습니다.")
    lines.extend(_format_quarterly_table(ed.get("quarterly_results", [])))
    lines.extend(_format_annual_table(ed.get("annual_results", [])))
    lines.extend(_format_eps_surprise(ed.get("earnings_surprise", [])))
    return "\n".join(lines)


def _format_filtered_earnings(filtered):
    lines = [f"\n■ {filtered['period']} 실적 (조회 대상)"]
    d = filtered["data"]
    if filtered["type"] == "quarter":
        lines += [
            f"  매출:     {_fmt_krw(d.get('revenue_b'))} 조원",
            f"  영업이익: {_fmt_krw(d.get('operating_income_b'))} 조원  (OPM: {d.get('operating_margin', 'N/A')}%)",
            f"  순이익:   {_fmt_krw(d.get('net_income_b'))} 조원",
        ]
        if d.get("revenue_yoy_pct") is not None:
            lines.append(f"  매출 YoY: {d['revenue_yoy_pct']:+.1f}%")
        if d.get("op_income_yoy_pct") is not None:
            lines.append(f"  영업이익 YoY: {d['op_income_yoy_pct']:+.1f}%")
    else:
        lines += [
            f"  매출:     {_fmt_krw(d.get('revenue_t'))} 조원",
            f"  영업이익: {_fmt_krw(d.get('operating_income_t'))} 조원  (OPM: {d.get('operating_margin', 'N/A')}%)",
            f"  순이익:   {_fmt_krw(d.get('net_income_t'))} 조원",
        ]
    return lines


def _format_quarterly_table(quarters):
    if not quarters:
        return []
    lines = [
        "\n■ 최근 분기별 실적 추이",
        f"  {'분기':<8} {'매출(조원)':>10} {'영업이익(조원)':>14} {'OPM':>6} {'매출YoY':>8}",
        "  " + "-" * 54,
    ]
    for q in quarters[:6]:
        yoy = f"{q['revenue_yoy_pct']:+.1f}%" if q.get("revenue_yoy_pct") is not None else "N/A"
        lines.append(
            f"  {q['period']:<8} "
            f"{_fmt_krw(q.get('revenue_b')):>10} "
            f"{_fmt_krw(q.get('operating_income_b')):>14} "
            f"{str(q.get('operating_margin', 'N/A')) + '%':>6} "
            f"{yoy:>8}"
        )
    return lines


def _format_annual_table(annual):
    if not annual:
        return []
    lines = [
        "\n■ 연간 실적 추이",
        f"  {'연도':<6} {'매출(조원)':>10} {'영업이익(조원)':>14} {'OPM':>6}",
        "  " + "-" * 40,
    ]
    for a in annual[:4]:
        lines.append(
            f"  {a['year']:<6} "
            f"{_fmt_krw(a.get('revenue_t')):>10} "
            f"{_fmt_krw(a.get('operating_income_t')):>14} "
            f"{str(a.get('operating_margin', 'N/A')) + '%':>6}"
        )
    return lines


def _format_eps_surprise(surprises):
    if not surprises:
        return []
    lines = ["\n■ EPS 서프라이즈 (최근)"]
    for s in surprises[:4]:
        surp = f"{s['surprise_pct']:+.1f}%" if s.get("surprise_pct") is not None else "N/A"
        lines.append(
            f"  {s['period']:<10} "
            f"실제: {s.get('eps_actual', 'N/A')}  "
            f"예상: {s.get('eps_estimate', 'N/A')}  "
            f"서프라이즈: {surp}"
        )
    return lines


def build_web_search_section(ws_results):
    lines = ["[웹 검색 최신 정보 (실시간)]"]
    for i, block in enumerate(ws_results, 1):
        text      = block.get("text", "").strip()
        citations = block.get("citations", [])
        if text:
            snippet = text[:800] + ("..." if len(text) > 800 else "")
            lines.append(f"\n■ 검색 결과 {i}")
            lines.append(snippet)
        if citations:
            lines.append("  [출처]")
            for c in citations[:5]:
                title = c.get("title", "")
                url   = c.get("url", "")
                if title or url:
                    lines.append(f"    • {title}  {url}")
    return "\n".join(lines)


def build_context(market_data, intent):
    sections = []
    fd = market_data.fundamentals
    pd = market_data.price_data
    td = market_data.technicals
    ed = market_data.earnings_data
    ws = market_data.web_search_results

    if fd:
        sections.append(build_company_section(fd, market_data.ticker))
    if pd:
        sections.append(build_price_section(pd))
    if fd and any(fd.get(k) for k in ["pe_ratio", "pb_ratio", "roe"]):
        sections.append(build_valuation_section(fd))
    if fd.get("analyst_target"):
        sections.append(build_analyst_section(fd, pd))
    if td:
        sections.append(build_technicals_section(td))
    if ed:
        sections.append(build_earnings_section(ed, intent))
    if market_data.news_snippets:
        news_text = "\n".join(f"  • {n}" for n in market_data.news_snippets[:6])
        sections.append(f"[최근 뉴스 헤드라인 (yfinance)]\n{news_text}")
    if ws:
        sections.append(build_web_search_section(ws))

    return "\n\n".join(sections)
