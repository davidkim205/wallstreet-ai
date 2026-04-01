import os
import json


# ─────────────────────────────────────────────
# ② Tool Router
# ─────────────────────────────────────────────

# 분석 유형별 필요 도구 매핑
TOOLS_BY_ANALYSIS = {
    "swot":         ["fundamentals", "news", "price", "web_search"],
    "technical":    ["price", "technicals"],
    "fundamental":  ["fundamentals", "price"],
    "news_summary": ["news", "web_search"],
    "screener":     ["fundamentals"],
    "comparison":   ["fundamentals", "price"],
    "watchlist":    ["price", "technicals"],
    "earnings":     ["earnings", "fundamentals", "news", "web_search"],
    "general":      ["fundamentals", "price", "news", "web_search"],
}


def route_tools(intent):
    # 분석 유형에 따라 데이터 수집 도구 목록 결정
    analysis_type = intent.get("analysis_type", "general")
    selected = TOOLS_BY_ANALYSIS.get(
        analysis_type,
        ["fundamentals", "price", "news", "web_search"]
    )
    print(f"[②] Tool Router → 선택된 도구: {selected}")
    return selected


def _format_history_for_intent(conversation_history, max_messages=4):
    if not conversation_history:
        return ""

    lines = []
    for message in conversation_history[-max_messages:]:
        role = message.get("role", "user")
        label = "사용자" if role == "user" else "어시스턴트"
        content = (message.get("content") or "").strip()
        if content:
            lines.append(f"{label}: {content}")
    return "\n".join(lines)


def parse_intent(client, user_query, conversation_history=None):
    LLM_MODEL_NAME = os.environ.get('LLM_MODEL_NAME')
    history_text = _format_history_for_intent(conversation_history or [])
    user_content = user_query
    if history_text:
        user_content = (
            "[최근 대화]\n"
            f"{history_text}\n\n"
            "[현재 사용자 질의]\n"
            f"{user_query}"
        )
    INTENT_TOOL = {
        "type": "function",
        "function": {
            "name": "parse_investment_intent",
            "description": "사용자의 투자 관련 질의에서 인텐트와 엔티티를 추출합니다.",
            "parameters": {
                "type": "object",
                "properties": {
                    "ticker": {"type": "string", "description": "주식 티커 심볼 (예: AAPL, TSLA, 005930.KS). 없으면 null."},
                    "analysis_type": {"type": "string", "enum": ["swot", "technical", "fundamental", "news_summary", "screener", "comparison", "watchlist", "earnings", "general"], "description": "요청된 분석 유형. 실적/어닝스/분기실적/연간실적/매출/영업이익 관련 질의는 반드시 'earnings'로 분류."},
                    "time_range": {"type": "string", "enum": ["1d", "5d", "1mo", "3mo", "6mo", "1y", "2y", "5y"], "description": "분석 기간. 기본값: 1y"},
                    "language": {"type": "string", "description": "응답 언어 (ko, en, ja 등)"},
                    "extra_tickers": {"type": "array", "items": {"type": "string"}, "description": "비교 분석 시 추가 티커 목록"},
                    "target_year": {"type": "integer", "description": "실적 조회 대상 연도 (예: 2025). '25년' → 2025 변환."},
                    "target_quarter": {"type": "integer", "enum": [1, 2, 3, 4], "description": "실적 조회 대상 분기 (1~4). 연간 실적 요청이면 null."}
                },
                "required": ["analysis_type"]
            }
        }
    }
    response = client.chat.completions.create(
        model=LLM_MODEL_NAME,
        messages=[
            {
                "role": "system",
                "content": (
                    "당신은 투자 분석 시스템의 인텐트 파서입니다. "
                    "사용자의 질의에서 분석에 필요한 정보를 정확히 추출하세요. "
                    "최근 대화가 주어지면 현재 질의 해석에 필요한 범위에서만 참고하고, "
                    "현재 질의에 명시된 조건이 이전 대화보다 우선합니다. "
                    "한국 주식은 '.KS'(코스피) 또는 '.KQ'(코스닥) 접미사를 붙이세요. "
                    "삼성전자 → 005930.KS, SK하이닉스 → 000660.KS, LG에너지솔루션 → 373220.KS, "
                    "현대차 → 005380.KS, 카카오 → 035720.KS, 네이버 → 035420.KS. "
                    "실적/어닝스/분기실적/연간실적/매출/영업이익/EPS 관련 질의는 "
                    "'earnings'로 분류하고 언급된 연도·분기를 채우세요."
                )
            },
            {"role": "user", "content": user_content}
        ],
        tools=[INTENT_TOOL],
        tool_choice={"type": "function", "function": {"name": "parse_investment_intent"}}
    )
    args = json.loads(
        response.choices[0].message.tool_calls[0].function.arguments
    )
    args.setdefault("time_range",      "1y")
    args.setdefault("language",        "ko")
    args.setdefault("extra_tickers",   [])
    args.setdefault("target_year",     None)
    args.setdefault("target_quarter",  None)
    return args
