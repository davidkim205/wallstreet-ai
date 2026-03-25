import os
import json
import textwrap
import time
from datetime import datetime
from typing import Any
from dataclasses import dataclass, field
from openai import OpenAI
from dataclasses import asdict
from dotenv import load_dotenv


from intent.intent_parser import parse_intent
from llm.generator import generate_analysis, generate_news_info

from data.collect_data import collect_data

from context.context_builder import build_context
from intent.intent_parser import route_tools
from utils.data_types import AnalysisResult

load_dotenv()


api_key = os.environ.get("LLM_MODEL_API_KEY")
if api_key:
    client = OpenAI(api_key=api_key)
else:
    client = OpenAI()


def timed(timings, key):
    """컨텍스트 매니저: 블록 실행 시간을 timings[key]에 기록."""
    class _Timer:
        def __enter__(self):
            self._t = time.time()
        def __exit__(self, *_):
            timings[key] = time.time() - self._t
    return _Timer()


def print_timings(timings):
    print("\n[소요시간(sec)]:")
    for k, v in timings.items():
        print(f"  {k:20s}: {v:.2f}s")


def save_result_jsonl(result):
    file_name = os.environ.get("LOG_FILE", "log_file.jsonl")
    data = asdict(result)

    ordered_data = {
        "timestamp": data.get("timestamp"),
        **{k: v for k, v in data.items() if k != "timestamp"}
    }

    with open(file_name, "a", encoding="utf-8") as f:
        f.write(json.dumps(ordered_data, ensure_ascii=False) + "\n")

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
    timings = {}
    start_total = time.time()
    print(f"\n{'='*60}")
    print(f"Wallstreet-AI 분석 시작: {query}")
    print('='*60)

    with timed(timings, 'intent_parse'):
        intent = parse_intent(client, query)

    with timed(timings, 'tool_route'):
        tools = route_tools(intent)

    with timed(timings, 'data_collect'):
        market_data = collect_data(client, intent, tools)

    with timed(timings, 'context_build'):
        news_str = generate_news_info(client, query, intent)
        context = build_context(market_data, intent, news_str=news_str)
    print(f"[④] 컨텍스트 빌드 완료 ({len(context)} 문자)")

    with timed(timings, 'analysis_generate'):
        response = generate_analysis(client, query, context, intent, news_str=None)

    timings['total'] = time.time() - start_total

    result = AnalysisResult(
        query=query,
        ticker=intent.get("ticker", ""),
        analysis_type=intent.get("analysis_type", "general"),
        data_context=market_data,
        llm_response=response
    )

    save_result_jsonl(result)

    print("\n[완료] 분석 완료 ✓")
    print_timings(timings)
        
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
    earnings = getattr(ctx, 'earnings_data', None) or {}
    stats = {
        "펀더멘털 지표 수":    len(getattr(ctx, 'fundamentals', {}) or {}),
        "가격 데이터 지표 수": len(getattr(ctx, 'price_data', {}) or {}),
        "기술적 지표 수":      len(getattr(ctx, 'technicals', {}) or {}),
        "뉴스 헤드라인 수":    len(getattr(ctx, 'news_snippets', []) or []),
        "분기 실적 수":        len(earnings.get('quarterly_results', [])),
        "연간 실적 수":        len(earnings.get('annual_results', [])),
        "웹 검색 블록 수":     len(getattr(ctx, 'web_search_results', []) or []),
    }
    print("[수집 데이터 요약]")
    for label, val in stats.items():
        print(f"  {label:20s}: {val}")
    print(f"  {'구글 뉴스 요약 포함':20s}: {'예' if getattr(ctx, 'google_news', None) else '아니오'}")

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