import os
import json
import asyncio
import textwrap
from contextlib import suppress
from datetime import datetime
from pathlib import Path
from functools import partial
from uuid import uuid4

from openai import AsyncOpenAI
from dotenv import load_dotenv

from intent.intent_parser import parse_intent, route_tools
from llm.generator import generate_analysis, generate_news_info
from data.collect_data import collect_data
from context.context_builder import build_context
from utils.data_types import AnalysisResult

load_dotenv()


api_key = os.environ.get("LLM_MODEL_API_KEY")
if api_key:
    async_client = AsyncOpenAI(api_key=api_key)
else:
    async_client = AsyncOpenAI()

PIPELINE_LOG_PATH = Path("results/pipeline.jsonl")
WAITING_STATUS_MESSAGES = [
    "답변 구조를 정리하고 있어요...",
    "핵심 포인트를 우선순위로 정리 중이에요...",
    "시장 데이터와 맥락을 교차 검증하고 있어요...",
    "근거를 확인하면서 답변을 다듬고 있어요...",
    "요약과 리스크 포인트를 함께 정리하고 있어요...",
    "답변 완성도를 높이고 있어요...",
]
WAITING_STATUS_INTERVAL_RANGE_SECONDS = 12


def append_pipeline_jsonl_log(result, executed_at, request_id, process_timings=None):
    PIPELINE_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

    log_record = {
        "request_id": request_id,
        "executed_at": executed_at,
        "query": result.query,
        "ticker": result.ticker,
        "analysis_type": result.analysis_type,
        "timestamp": result.timestamp,
        "data_context": result.data_context,
        "llm_response": result.llm_response,
        "process_timings": process_timings or {},
    }

    with PIPELINE_LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(log_record, ensure_ascii=False) + "\n")


def _emit_status(message, status_callback=None):
    if status_callback:
        status_callback(message)


def _now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _req_prefix(request_id):
    return f"[req:{request_id}]"


def _req_log_and_status_callback(request_id, on_latency_marker=None):
    def _log(message):
        print(f"{_req_prefix(request_id)} {message}")
        if on_latency_marker and "response.created -> 첫 delta 지연:" in message:
            on_latency_marker()

    return _log


async def _emit_waiting_statuses_in_order(status_callback, stop_event):
    if not status_callback:
        return

    index = 0
    message_count = len(WAITING_STATUS_MESSAGES)

    while not stop_event.is_set():
        await asyncio.sleep(WAITING_STATUS_INTERVAL_RANGE_SECONDS)
        if stop_event.is_set():
            break
        _emit_status(WAITING_STATUS_MESSAGES[index], status_callback)
        index = (index + 1) % message_count


async def _run_stage(label, coro, stream_output, process_timings, request_id):
    started_at_dt = datetime.now()
    started_at = started_at_dt.strftime("%Y-%m-%d %H:%M:%S")

    if stream_output:
        print(f"{_req_prefix(request_id)} [{label}] 시작: {started_at}")

    value = await coro

    ended_at_dt = datetime.now()
    ended_at = ended_at_dt.strftime("%Y-%m-%d %H:%M:%S")
    elapsed_seconds = round((ended_at_dt - started_at_dt).total_seconds(), 3)

    process_timings[label] = {
        "started_at": started_at,
        "ended_at": ended_at,
        "elapsed_seconds": elapsed_seconds,
    }

    if stream_output:
        print(f"{_req_prefix(request_id)} [{label}] 종료: {ended_at} (소요 {elapsed_seconds}초)")

    return value


async def pipeline(query, stream_output=False, stream_callback=None, status_callback=None, request_id=None):
    """
    파이프라인:
        ① 인텐트 파싱  (Chat Completions + Function Calling)
        ② Tool 라우팅
        ③ 데이터 수집  (yfinance + Responses API web_search)
        ④ 컨텍스트 조립
        ⑤ 분석 생성   (Responses API + web_search 상시 활성)
    """
    global async_client
    request_id = request_id or uuid4().hex[:8]
    executed_at = _now_str()
    process_timings = {}

    print(f"\n{'='*60}")
    print(f"{_req_prefix(request_id)} Wallstreet-AI 분석 시작: {query}")
    print('='*60)

    _emit_status("질문 의도를 파악하고 있어요...", status_callback)
    intent = await _run_stage(
        "① 인텐트 파싱",
        parse_intent(async_client, query),
        stream_output,
        process_timings,
        request_id,
    )

    _emit_status("분석에 필요한 도구를 고르고 있어요...", status_callback)
    tools = await _run_stage(
        "② Tool 라우팅",
        asyncio.to_thread(route_tools, intent),
        stream_output,
        process_timings,
        request_id,
    )

    _emit_status("시장 데이터를 수집하고 있어요...", status_callback)
    market_data = await _run_stage(
        "③ 데이터 수집",
        collect_data(async_client, intent, tools),
        stream_output,
        process_timings,
        request_id,
    )

    _emit_status("수집 데이터를 정리하고 있어요...", status_callback)
    news_str = await generate_news_info(async_client, query, intent)
    context = await _run_stage(
        "④ 컨텍스트 조립",
        asyncio.to_thread(partial(build_context, market_data, intent, news_str=news_str)),
        stream_output,
        process_timings,
        request_id,
    )
    print(f"{_req_prefix(request_id)} [④] 컨텍스트 빌드 완료 ({len(context)} 문자)")

    _emit_status("AI가 답변을 작성하고 있어요...", status_callback)
    waiting_stop_event = asyncio.Event()
    waiting_task = None
    if status_callback and stream_output:
        waiting_task = asyncio.create_task(
            _emit_waiting_statuses_in_order(status_callback, waiting_stop_event)
        )

    try:
        response = await _run_stage(
            "⑤ 분석 생성",
            generate_analysis(
                async_client,
                query,
                context,
                intent,
                news_str=news_str,
                stream_output=stream_output,
                stream_callback=stream_callback,
                log_callback=_req_log_and_status_callback(
                    request_id,
                    on_latency_marker=waiting_stop_event.set,
                ),
            ),
            stream_output,
            process_timings,
            request_id,
        )
    finally:
        waiting_stop_event.set()
        if waiting_task:
            waiting_task.cancel()
            with suppress(asyncio.CancelledError):
                await waiting_task

    result = AnalysisResult(
        query=query,
        ticker=intent.get("ticker", ""),
        analysis_type=intent.get("analysis_type", "general"),
        data_context={
            "price": market_data.price_data,
            "fundamentals": market_data.fundamentals,
            "technicals": market_data.technicals,
            "news_count": len(market_data.news_snippets),
            "earnings": market_data.earnings_data,
            "web_search_blocks": len(market_data.web_search_results),
            "google_news": news_str,
        },
        llm_response=response,
    )

    await asyncio.to_thread(append_pipeline_jsonl_log, result, executed_at, request_id, process_timings)
    print(f"{_req_prefix(request_id)} [로그] JSONL 저장 완료: {PIPELINE_LOG_PATH}")

    print(f"\n{_req_prefix(request_id)} [완료] 분석 완료 ✓")
    print(f"{_req_prefix(request_id)} context info")
    print(context)

    _emit_status("답변 준비가 끝났어요.", status_callback)
    return result


# ─────────────────────────────────────────────
# CLI 출력 + 진입점
# ─────────────────────────────────────────────

def print_result(result, include_response=True):
    print(f"\n{'='*60}")
    print(f"분석 결과 | {result.ticker} | {result.analysis_type.upper()}")
    print(f"생성 시각: {result.timestamp}")
    print('='*60)

    if include_response:
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


async def main():
    while True:
        text = input("\n질문> ").strip()
        if text.lower() in ("exit", "quit", "종료"):
            break
        if not text:
            continue
        result = await pipeline(text, stream_output=True)
        print_result(result, include_response=False)


if __name__ == "__main__":
    asyncio.run(main())

# FastAPI 실행 진입점
# (uvicorn으로 실행: uvicorn api_server:app --reload)
