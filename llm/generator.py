import os
import json
import time
from .prompts import SYSTEM_PROMPTS
from data.news import search_google_news, format_news_list

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


async def generate_search_keywords(client, user_query, intent):
    """LLM을 통해 구글 뉴스 검색어 리스트 생성"""
    language = intent.get("language", "ko")
    prompt = f"""
    다음 투자 분석을 위해 구글 뉴스에서 검색할 만한 핵심 키워드 5개를 리스트로 생성하세요. 각 키워드는 짧고 명확하게 작성하세요. 반드시 리스트 형태로만 출력하세요. 언어는 {language}로 작성하세요.

[사용자 질의]
{user_query}
"""
    LLM_MODEL_NAME = os.environ.get("LLM_MODEL_NAME")
    resp = await client.chat.completions.create(
        model=LLM_MODEL_NAME,
        messages=[
            {"role": "system", "content": "뉴스 검색 키워드 생성"},
            {"role": "user", "content": prompt},
        ],
    )
    content = resp.choices[0].message.content
    try:
        keywords = json.loads(content)
        if isinstance(keywords, list):
            return keywords
    except Exception:
        return [k.strip() for k in content.replace("\n", ",").split(",") if k.strip()]
    return []


async def generate_news_info(client, user_query, intent):
    """LLM 키워드 생성 + 구글 뉴스 검색 + 포맷까지 한 번에 처리 (Async LLM)"""
    language = intent.get("language", "ko")
    keywords = await generate_search_keywords(client, user_query, intent)
    news_list = search_google_news(keywords, language=language)
    news_str = format_news_list(news_list)
    return news_str


def _extract_delta_text(event):
    # OpenAI SDK 이벤트 객체(dict/typed)에서 delta 텍스트를 안전하게 추출
    delta = getattr(event, "delta", None)
    if delta:
        return delta
    if isinstance(event, dict):
        return event.get("delta", "")
    return ""


async def generate_analysis(
    client,
    user_query,
    context,
    intent,
    news_str,
    stream_output=False,
    stream_callback=None,
    log_callback=None,
):
    # Async Responses API로 최종 투자 분석 리포트 생성
    analysis_type = intent.get("analysis_type", "general")
    language = intent.get("language", "ko")
    system_prompt = SYSTEM_PROMPTS.get(analysis_type, SYSTEM_PROMPTS["general"])
    system_prompt += f"\n\n반드시 {language} 언어로 답변하세요. 투자 조언이 아닌 정보 제공임을 명시하세요."

    full_input = f"""{system_prompt}

[수집된 시장 데이터]
{context}

[사용자 질의]
{user_query}

[최신 구글 뉴스]
{news_str} """

    LLM_MODEL_NAME = os.environ.get("LLM_MODEL_NAME")
    delta_log_every = int(os.environ.get("DELTA_LOG_EVERY", "20"))
    delta_log_preview_chars = int(os.environ.get("DELTA_LOG_PREVIEW_CHARS", "80"))
    start_msg = f"[⑤] LLM 분석 생성 중 (Responses API, 모델: {LLM_MODEL_NAME})..."
    if log_callback:
        log_callback(start_msg)
    else:
        print(start_msg)

    request_kwargs = dict(
        model=LLM_MODEL_NAME,
        tools=[
            {
                "type": "web_search",
                "user_location": {"type": "approximate", "country": "KR"},
            }
        ],
        input=full_input,
    )

    if stream_output:
        try:
            all_deltas = []
            first_event_logged = False
            delta_count = 0
            delta_total_chars = 0
            response_created_at = None
            first_delta_latency_logged = False

            async with client.responses.stream(**request_kwargs) as stream:
                if log_callback:
                    log_callback("[⑤][stream] stream context 진입 완료")
                else:
                    print("[⑤][stream] stream context 진입 완료")

                async for event in stream:
                    if not first_event_logged:
                        event_type = getattr(event, "type", None)
                        msg = f"[⑤][stream] 첫 이벤트 수신 type={event_type}"
                        if log_callback:
                            log_callback(msg)
                        else:
                            print(msg)
                        first_event_logged = True

                    if getattr(event, "type", None) == "response.created" and response_created_at is None:
                        response_created_at = time.perf_counter()

                    if getattr(event, "type", None) == "response.output_text.delta":
                        delta = _extract_delta_text(event)
                        if delta:
                            all_deltas.append(delta)
                            delta_count += 1
                            delta_total_chars += len(delta)

                            if response_created_at is not None and not first_delta_latency_logged:
                                latency_s = time.perf_counter() - response_created_at
                                latency_msg = f"[⑤][stream] response.created -> 첫 delta 지연: {latency_s:.3f}초"
                                if log_callback:
                                    log_callback(latency_msg)
                                else:
                                    print(latency_msg)
                                first_delta_latency_logged = True

                            should_log_delta = delta_count <= 3 or (delta_log_every > 0 and delta_count % delta_log_every == 0)
                            if should_log_delta:
                                preview = delta.replace("\n", "\\n")[:delta_log_preview_chars]
                                msg = f"[⑤][stream] delta#{delta_count} len={len(delta)} total_chars={delta_total_chars} preview='{preview}'"
                                if log_callback:
                                    log_callback(msg)
                                else:
                                    print(msg)

                            if stream_callback:
                                stream_callback(delta)
                            else:
                                print(delta, end="", flush=True)

                if not stream_callback:
                    print()

                if response_created_at is not None and not first_delta_latency_logged:
                    no_delta_msg = "[⑤][stream] response.created 이후 delta 미수신"
                    if log_callback:
                        log_callback(no_delta_msg)
                    else:
                        print(no_delta_msg)

                summary_msg = f"[⑤][stream] delta 수집 요약: chunks={delta_count}, total_chars={delta_total_chars}"
                if log_callback:
                    log_callback(summary_msg)
                else:
                    print(summary_msg)

                if log_callback:
                    log_callback("[⑤][stream] 최종 응답 수집 시작")
                else:
                    print("[⑤][stream] 최종 응답 수집 시작")
                final_resp = await stream.get_final_response()
                if log_callback:
                    log_callback("[⑤][stream] 최종 응답 수집 완료")
                else:
                    print("[⑤][stream] 최종 응답 수집 완료")

                result = extract_response_text(final_resp)
                if result:
                    return result
                joined = "".join(all_deltas).strip()
                return joined or "(분석 결과를 가져오지 못했습니다)"
        except Exception as e:
            err_msg = f"[⑤] 스트리밍 출력 실패, 일반 모드로 재시도: {e}"
            if log_callback:
                log_callback(err_msg)
            else:
                print(err_msg)

    resp = await client.responses.create(**request_kwargs)
    result = extract_response_text(resp)
    return result or "(분석 결과를 가져오지 못했습니다)"
