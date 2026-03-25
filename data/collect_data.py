from typing import Any
from datetime import datetime
import yfinance as yf

from data.earnings import fetch_earnings_data
from data.finance_fetchers import fetch_fundamentals, fetch_news, fetch_price_data
from data.technicals import fetch_technicals
from llm.web_search import fetch_web_search
from utils.data_types import MarketData


def collect_data(client, intent, tools):
    # 인텐트와 선택된 도구 목록을 기반으로 모든 시장 데이터 수집
    ticker = intent.get("ticker", "")
    period = intent.get("time_range", "1y")

    print(f"[③] 데이터 수집 중 (ticker={ticker}, period={period})...")

    price_data         = {}
    fundamentals       = {}
    technicals         = {}
    news_snippets      = []
    earnings_data      = {}
    web_search_results = []

    if ticker:
        if "price" in tools:
            price_data = fetch_price_data(ticker, period)
            print(f"    → 가격 데이터: {len(price_data)}개 지표")

        if "fundamentals" in tools:
            fundamentals = fetch_fundamentals(ticker)
            print(f"    → 펀더멘털: {len(fundamentals)}개 지표")

        if "technicals" in tools:
            technicals = fetch_technicals(ticker, "6mo")
            print(f"    → 기술지표: {len(technicals)}개 지표")

        if "news" in tools:
            news_snippets = fetch_news(ticker)
            print(f"    → 뉴스: {len(news_snippets)}개 헤드라인")

        if "earnings" in tools:
            earnings_data = fetch_earnings_data(
                ticker,
                target_year=intent.get("target_year"),
                target_quarter=intent.get("target_quarter")
            )
    # fetch_web_search는 너무 오래 걸린다.
    # if "web_search" in tools:
    #     company_name = fundamentals.get("company_name", ticker) if fundamentals else ticker
    #     web_search_results = fetch_web_search(
    #         client=client,
    #         ticker=ticker,
    #         company_name=company_name,
    #         analysis_type=intent.get("analysis_type", "general"),
    #         language=intent.get("language", "ko"),
    #         target_year=intent.get("target_year"),
    #         target_quarter=intent.get("target_quarter"),
    #     )

    return MarketData(
        ticker=ticker,
        price_data=price_data,
        fundamentals=fundamentals,
        technicals=technicals,
        news_snippets=news_snippets,
        earnings_data=earnings_data,
        web_search_results=web_search_results,
    )
