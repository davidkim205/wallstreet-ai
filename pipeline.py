import os
import json
import textwrap
import time
from datetime import datetime
from typing import Any, Callable, Optional
from dataclasses import dataclass, field, asdict, is_dataclass
from openai import OpenAI
from dotenv import load_dotenv


from intent.intent_parser import parse_intent
from llm.generator import generate_analysis, generate_analysis_stream, generate_news_info

from data.collect_data import collect_data

from context.context_builder import build_context
from intent.intent_parser import route_tools
from utils.data_types import AnalysisResult
from persona.persona_loader import get_persona
from persona.persona_loader import load_personas
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

    def _json_safe(value):
        if is_dataclass(value):
            return {k: _json_safe(v) for k, v in asdict(value).items()}
        if isinstance(value, dict):
            return {k: _json_safe(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [_json_safe(v) for v in value]
        if isinstance(value, (datetime, )):
            return value.isoformat()
        return value

    data = _json_safe(result)

    ordered_data = {
        "timestamp": data.get("timestamp"),
        **{k: v for k, v in data.items() if k != "timestamp"}
    }

    with open(file_name, "a", encoding="utf-8") as f:
        f.write(json.dumps(ordered_data, ensure_ascii=False) + "\n")

def pipeline(query, conversation=None, persona_name=None, status_callback=None, stream_callback=None, stream=True):
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

    persona = get_persona(persona_name) if persona_name else None
    def emit_status(message: str):
        if status_callback:
            status_callback(message)

    def emit_delta(delta: str):
        if stream_callback:
            stream_callback(delta)

    print(f"\n{'='*60}")
    print(f"Wallstreet-AI 분석 시작: {query}")
    if persona:
        print(f"[Persona] {persona.name}")
    print('='*60)

    emit_status("인텐트 분석 중...")
    with timed(timings, 'intent_parse'):
        intent = parse_intent(client, query)

    emit_status("도구 라우팅 중...")
    with timed(timings, 'tool_route'):
        tools = route_tools(intent)

    emit_status("시장 데이터 수집 중...")
    with timed(timings, 'data_collect'):
        market_data = collect_data(client, intent, tools)

    emit_status("뉴스 컨텍스트 생성 중...")
    with timed(timings, 'context_build'):
        news_str = generate_news_info(client, query, intent)
        emit_status("컨텍스트 빌드 중...")
        context = build_context(market_data, intent, news_str=news_str)

    emit_status("LLM 분석 생성 중...")
    with timed(timings, 'analysis_generate'):
        if stream:
            chunks = []
            print("[⑤] 스트리밍 응답 수신 중...")
            for delta in generate_analysis_stream(
                client,
                query,
                context,
                intent,
                persona=persona,
                conversation=conversation,
            ):
                if not delta:
                    continue
                chunks.append(delta)
                print(delta, end="", flush=True)
                emit_delta(delta)
            print()
            response = "".join(chunks).strip() or "(분석 결과를 가져오지 못했습니다)"
        else:
            print("[⑤] 단일 응답 생성 중...")
            response = generate_analysis(
                client,
                query,
                context,
                intent,
                persona=persona,
                conversation=conversation,
            )
            if response:
                emit_delta(response)

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
    personas = load_personas()

    print("\n사용 가능한 persona:")
    for i, p in enumerate(personas, 1):
        print(f"{i}. {p.name}")

    persona_name = None
    persona_num = input("\npersona 번호 입력(없으면 Enter): ").strip()
    if persona_num:
        try:
            idx = int(persona_num) - 1
            if 0 <= idx < len(personas):
                persona_name = personas[idx].name
            else:
                print("해당 번호의 persona 없음. 기본 모드로 진행합니다.")
        except ValueError:
            print("잘못된 입력입니다. 기본 모드로 진행합니다.")

    conversation = []
    print("\n멀티턴 대화 모드입니다. 이전 질문/답변이 다음 분석에 함께 반영됩니다.")
    print("대화 초기화: reset 또는 clear | 종료: exit, quit, 종료")

    while True:
        text = input("\n질문 > ").strip()
        if text.lower() in ("exit", "quit", "종료"):
            break
        if text.lower() in ("reset", "clear"):
            conversation = []
            print("대화 히스토리를 초기화했습니다.")
            continue
        if not text:
            continue

        current_conversation = conversation + [{"role": "user", "content": text}]
        result = pipeline(text, conversation=current_conversation, persona_name=persona_name)
        conversation = current_conversation + [{"role": "assistant", "content": result.llm_response}]
        print_result(result)


if __name__ == "__main__":
    main()
