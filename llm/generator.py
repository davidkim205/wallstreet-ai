import os
import json
import textwrap
from datetime import datetime
from typing import Any
from dataclasses import dataclass, field

import yfinance as yf
from openai import OpenAI
from .prompts import SYSTEM_PROMPTS

# ─────────────────────────────────────────────
# ⑤ LLM 분석 생성 (Responses API + web_search 상시 활성)
# ─────────────────────────────────────────────

def extract_response_text(resp):
    # Responses API 출력에서 텍스트 블록만 추출하여 합산
    texts = []
    for item in resp.output:
        if getattr(item, "type", None) != "message":
            continue
        for block in (getattr(item, "content", []) or []):
            if getattr(block, "type", None) == "output_text":
                t = getattr(block, "text", "")
                if t:
                    texts.append(t)
    return "\n".join(texts)


def generate_analysis(client, user_query, context, intent):
    # Responses API + web_search로 최종 투자 분석 리포트 생성
    analysis_type = intent.get("analysis_type", "general")
    language      = intent.get("language", "ko")
    system_prompt = SYSTEM_PROMPTS.get(analysis_type, SYSTEM_PROMPTS["general"])
    system_prompt += f"\n\n반드시 {language} 언어로 답변하세요. 투자 조언이 아닌 정보 제공임을 명시하세요."

    full_input = f"""{system_prompt}

[수집된 시장 데이터]
{context}

[사용자 질의]
{user_query}"""

    LLM_MODEL_NAME = os.environ.get('LLM_MODEL_NAME')
    print(f"[⑤] LLM 분석 생성 중 (Responses API, 모델: {LLM_MODEL_NAME})...")

    resp = client.responses.create(
        model=LLM_MODEL_NAME,
        tools=[
            {
                "type": "web_search",
                "user_location": {"type": "approximate", "country": "KR"}
            }
        ],
        input=full_input,
    )

    result = extract_response_text(resp)
    return result or "(분석 결과를 가져오지 못했습니다)"

