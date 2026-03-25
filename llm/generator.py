import os
import json
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


def generate_analysis(client, user_query, context, intent, news_str, persona=None):
    analysis_type = intent.get("analysis_type", "general")
    language      = intent.get("language", "ko")
    system_prompt = SYSTEM_PROMPTS.get(analysis_type, SYSTEM_PROMPTS["general"])
    
    system_prompt += f"\n\n반드시 {language} 언어로 답변하세요. 투자 조언이 아닌 정보 제공임을 명시하세요."

    if persona:
        system_prompt += f"""
        
[선택된 페르소나]
이름: {persona.name}
배경: {persona.background}
금융 사고방식: {persona.financial_mindset}
데이터 분석 방식: {persona.data_analysis_approach}
답변 스타일: {persona.response_style}
핵심 원칙: {", ".join(persona.key_principles)}
"""
        if persona.famous_quotes:
            system_prompt += f"\n대표 어록: {' / '.join(persona.famous_quotes)}"

    full_input = f"""{system_prompt}

[수집된 시장 데이터]
{context}

[사용자 질의]
{user_query}

[최신 구글 뉴스]
{news_str} """

    LLM_MODEL_NAME = os.environ.get('LLM_MODEL_NAME')
    resp = client.responses.create(
        model=LLM_MODEL_NAME,
        input=full_input,
    )

    result = extract_response_text(resp)
    return result or "(분석 결과를 가져오지 못했습니다)"


class Persona(BaseModel):
    name: str                        # 인물 이름
    background: str                  # 인물 배경 (경력, 주요 업적 등)
    financial_mindset: str           # 금융 사고 방식
    data_analysis_approach: str      # 데이터 분석 방식
    response_style: str              # 질문에 대한 답변 스타일
    key_principles: list[str]        # 핵심 투자/금융 원칙
    famous_quotes: Optional[list[str]] = None  # 대표 어록 (있을 경우)

def generate_persona(client, user_query):
    system_prompt = (
        "아래 금융 인물에 대해 자세히 검색을 한다음 "
        "인물에 대한 금융 사고 방식, 인물이 데이터를 분석하는 방식, "
        "질문에 대한 답변 스타일을 작성하시오."
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