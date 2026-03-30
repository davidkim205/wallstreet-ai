import os
import json
from typing import Generator
from .prompts import SYSTEM_PROMPTS
from data.news import search_google_news, format_news_list
from pydantic import BaseModel
from typing import Optional

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


def generate_search_keywords(client, user_query, intent):
    """LLM을 통해 구글 뉴스 검색어 리스트 생성"""
    language = intent.get("language", "ko")
    prompt = f"""
    다음 투자 분석을 위해 구글 뉴스에서 검색할 만한 핵심 키워드 5개를 리스트로 생성하세요. 각 키워드는 짧고 명확하게 작성하세요. 반드시 리스트 형태로만 출력하세요. 언어는 {language}로 작성하세요.

[사용자 질의]
{user_query}
"""
    LLM_MODEL_NAME = os.environ.get('LLM_MODEL_NAME')
    resp = client.chat.completions.create(
        model=LLM_MODEL_NAME,
        messages=[{"role": "system", "content": "뉴스 검색 키워드 생성"},
                  {"role": "user", "content": prompt}],
    )
    # 키워드 리스트 추출 (예: ['삼성전자', '반도체 전망', ...])
    content = resp.choices[0].message.content
    try:
        keywords = json.loads(content)
        if isinstance(keywords, list):
            return keywords
    except Exception:
        # 리스트 형태가 아니면 줄바꿈/쉼표로 분리
        return [k.strip() for k in content.replace('\n', ',').split(',') if k.strip()]
    return []


def generate_news_info(client, user_query, intent):
    """LLM 키워드 생성 + 구글 뉴스 검색 + 포맷까지 한 번에 처리"""
    language = intent.get("language", "ko")
    keywords = generate_search_keywords(client, user_query, intent)
    news_list = search_google_news(keywords, language=language)
    news_str = format_news_list(news_list)
    return news_str

class Persona(BaseModel):
    name: str                        # 인물 이름
    full_name: str                   # 인물 이름
    summary: str                     # 인물 요약 (간단한 소개)
    financial_mindset: str           # 금융 사고 방식
    data_analysis_approach: str      # 데이터 분석 방식
    response_style: str              # 질문에 대한 답변 스타일
    key_principles: list[str]        # 핵심 투자/금융 원칙
    famous_quotes: Optional[list[str]] = None  # 대표 어록 (있을 경우)

def generate_persona(client, user_query):
    system_prompt = (
        "아래 금융 인물을 웹 검색으로 확인한 뒤 Persona 스키마에 맞춰 작성하시오.\n"
        "이름 규칙:\n"
        "- full_name: 인물의 원문/정식 전체 이름.\n"
        "- name: 사용자가 이해하기 쉬운 표시 이름(한국어 통용명 우선, 없으면 간결한 영어).\n"
        "- 괄호 별칭/원문 병기는 full_name에만 포함하고 name에는 넣지 말 것.\n"
        "- summary: 인물의 간단한 소개 요약 (2-3문장 정도로 간결하게).\n"
        "나머지 필드는 사실 기반으로 충실히 작성하시오."
    )

    LLM_MODEL_NAME = os.environ.get('LLM_MODEL_NAME')

    full_input = system_prompt + "\n" + user_query

    response = client.responses.parse(
        model=LLM_MODEL_NAME,
        tools=[
            {
                "type": "web_search",
                "user_location": {"type": "approximate", "country": "KR"}
            }
        ],
        input=full_input,
        text_format=Persona,
    )

    # output_parsed가 없는 경우 직접 파싱
    if hasattr(response, 'output_parsed') and response.output_parsed:
        return response.output_parsed
    
    # output에서 텍스트 추출 후 Persona로 파싱
    for item in response.output:
        if hasattr(item, 'content'):
            for content in item.content:
                if hasattr(content, 'text'):
                    return Persona.model_validate_json(content.text)

    return None


def build_full_prompt(user_query, context, intent, persona=None):
    analysis_type = intent.get("analysis_type", "general")
    language      = intent.get("language", "ko")
    system_prompt = SYSTEM_PROMPTS.get(analysis_type, SYSTEM_PROMPTS["general"])
    
    system_prompt += f"\n\n반드시 {language} 언어로 답변하세요. 투자 조언이 아닌 정보 제공임을 명시하세요."

    if persona:
        system_prompt += f"""
[사용자 질의]
{user_query}

[선택된 페르소나]
이름: {persona.name}
요약: {persona.summary}
금융 사고방식: {persona.financial_mindset}
데이터 분석 방식: {persona.data_analysis_approach}
답변 스타일: {persona.response_style}
핵심 원칙: {", ".join(persona.key_principles)}
"""
        if persona.famous_quotes:
            system_prompt += f"\n대표 어록: {' / '.join(persona.famous_quotes)}"

    full_prompt = f"""{system_prompt}

[수집된 시장 데이터]
{context}"""
    return full_prompt

def generate_analysis(client, user_query, context, intent, persona=None):
    full_prompt = build_full_prompt(user_query, context, intent, persona)
    LLM_MODEL_NAME = os.environ.get('LLM_MODEL_NAME')
    resp = client.responses.create(
        model=LLM_MODEL_NAME,
        input=full_prompt,
    )

    result = extract_response_text(resp)
    return result or "(분석 결과를 가져오지 못했습니다)"

def generate_analysis_stream(client, user_query, context, intent, persona=None):
   
    full_prompt = build_full_prompt(user_query, context, intent, persona)
    llm_model_name = os.environ.get("LLM_MODEL_NAME")
    print(f"[⑤] LLM 분석 스트리밍 생성 중 (Responses API, 모델: {llm_model_name})...")

    chunks = []
    final_text = ""

    def _event_get(event, key, default=None):
        if isinstance(event, dict):
            return event.get(key, default)
        return getattr(event, key, default)
    # SDK에 따라 stream API 형태가 다를 수 있어 create(stream=True) 기준으로 처리
    stream = client.responses.create(
        model=llm_model_name,
        input=full_prompt,
        stream=True,
    )

    for event in stream:
        event_type = _event_get(event, "type", "")

        if event_type == "response.output_text.delta":
            delta = _event_get(event, "delta", "")
            if delta:
                chunks.append(delta)
                yield delta
            continue

        # 일부 SDK/이벤트에서는 최종 response를 completed 이벤트에서 전달
        if event_type == "response.completed":
            response_obj = _event_get(event, "response", None)
            if response_obj:
                final_text = extract_response_text(response_obj) or ""

    if not chunks:
        # 델타 이벤트를 못 받은 경우 completed response 텍스트를 폴백으로 사용
        final_text = final_text or "(분석 결과를 가져오지 못했습니다)"
        yield final_text
        return final_text

    return "".join(chunks)
