import os
import json
import textwrap
from datetime import datetime
from typing import Any
from dataclasses import dataclass, field
from openai import OpenAI


from intent.intent_parser import parse_intent
from llm.generator import generate_analysis, generate_news_info

from data.collect_data import collect_data

from context.context_builder import build_context
from intent.intent_parser import route_tools
from utils.data_types import AnalysisResult
from dotenv import load_dotenv

load_dotenv()


api_key = os.environ.get("LLM_MODEL_API_KEY")
if api_key:
    client = OpenAI(api_key=api_key)
else:
    client = OpenAI()

def pipeline(query):
    """
    파이프라인:
        ① 인텐트 파싱  (Chat Completions + Function Calling)
        ② Tool 라우팅
        ③ 데이터 수집  (yfinance + Responses API web_search)
        ④ 컨텍스트 조립
        ⑤ 분석 생성   (Responses API + web_search 상시 활성)
    """
    global client
    print(f"\n{'='*60}")
    print(f"Wallstreet-AI 분석 시작: {query}")
    print('='*60)

    # 1. intent 파싱, 2. tool 라우팅, 3. 데이터 수집
    intent      = parse_intent(client, query)
    tools       = route_tools(intent)
    market_data = collect_data(client, intent, tools)

    # 4. 구글 뉴스 정보 생성 및 컨텍스트에 포함
    news_str = generate_news_info(client, query, intent)
    context = build_context(market_data, intent, news_str=news_str)
    print(f"[④] 컨텍스트 빌드 완료 ({len(context)} 문자)")

    # 5. 분석 생성 (news_str는 이미 context에 포함되었으므로 전달하지 않음)
    response = generate_analysis(client, query, context, intent, news_str=None)

    result = AnalysisResult(
        query=query,
        ticker=intent.get("ticker", ""),
        analysis_type=intent.get("analysis_type", "general"),
        data_context={
            "price":             market_data.price_data,
            "fundamentals":      market_data.fundamentals,
            "technicals":        market_data.technicals,
            "news_count":        len(market_data.news_snippets),
            "earnings":          market_data.earnings_data,
            "web_search_blocks": len(market_data.web_search_results),
            "google_news":       news_str,  
        },
        llm_response=response
    )

    print("\n[완료] 분석 완료 ✓")
    print('\ncontext info')
    print(context)
    return result


# ─────────────────────────────────────────────
# CLI 출력 + 진입점
# ─────────────────────────────────────────────

def print_result(result):
    print(f"\n{'='*60}")
    print(f"분석 결과 | {result.ticker} | {result.analysis_type.upper()}")
    print(f"생성 시각: {result.timestamp}")
    print('='*60)
    for line in result.llm_response.split("\n"):
        print(textwrap.fill(line, width=80) if len(line) > 80 else line)
    print()

    ctx = result.data_context
    print("[수집 데이터 요약]")
    print(f"  펀더멘털 지표 수:    {len(ctx.get('fundamentals', {}))}")
    print(f"  가격 데이터 지표 수: {len(ctx.get('price', {}))}")
    print(f"  기술적 지표 수:      {len(ctx.get('technicals', {}))}")
    print(f"  뉴스 헤드라인 수:    {ctx.get('news_count', 0)}")
    print(f"  분기 실적 수:        {len(ctx.get('earnings', {}).get('quarterly_results', []))}")
    print(f"  연간 실적 수:        {len(ctx.get('earnings', {}).get('annual_results', []))}")
    print(f"  웹 검색 블록 수:     {ctx.get('web_search_blocks', 0)}")
    print(f"  구글 뉴스 요약 포함:   {'예' if ctx.get('google_news') else '아니오'}")

def main():
    while True:
        text = input("\n질문> ").strip()
        if text.lower() in ("exit", "quit", "종료"):
            break
        if not text:
            continue
        result = pipeline(text)
        print_result(result)


if __name__ == "__main__":
    main()

# FastAPI 실행 진입점
# (uvicorn으로 실행: uvicorn api_server:app --reload)

