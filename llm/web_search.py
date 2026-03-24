from datetime import datetime
import os

def build_web_search_query(analysis_type, base, target_year, target_quarter, today):
    if analysis_type == "news_summary":
        return f"{today} 기준 {base} 최신 뉴스 및 주요 이슈"
    if analysis_type == "earnings":
        if target_year and target_quarter:
            return f"{base} {target_year}년 {target_quarter}분기 실적 발표 매출 영업이익 컨센서스"
        if target_year:
            return f"{base} {target_year}년 연간 실적 매출 영업이익"
        return f"{base} 최근 분기 실적 발표 {today}"
    if analysis_type == "swot":
        return f"{base} 최신 사업 현황, 경쟁사 동향, 리스크 요인 {today}"
    return f"{base} 최신 투자 정보 및 시장 동향 {today}"

def extract_web_search_blocks(resp):
    results = []
    for item in resp.output:
        if getattr(item, "type", None) != "message":
            continue
        for block in (getattr(item, "content", []) or []):
            if getattr(block, "type", None) != "output_text":
                continue
            text        = getattr(block, "text", "") or ""
            annotations = getattr(block, "annotations", []) or []
            citations   = [
                {
                    "title": getattr(ann, "title", ""),
                    "url":   getattr(ann, "url", ""),
                }
                for ann in annotations
                if getattr(ann, "type", "") == "url_citation"
            ]
            if text.strip():
                results.append({"text": text.strip(), "citations": citations})
    return results

def fetch_web_search(client, ticker, company_name, analysis_type, language="ko", target_year=None, target_quarter=None):
    today  = datetime.now().strftime("%Y년 %m월 %d일")
    base   = f"{company_name}({ticker})" if ticker else company_name
    query  = build_web_search_query(
        analysis_type, base, target_year, target_quarter, today
    )
    print(f"[WEB] 웹 검색 중... 쿼리: '{query}'")
    results = []
    LLM_MODEL_NAME = os.environ.get('LLM_MODEL_NAME')
    try:
        resp = client.responses.create(
            model=LLM_MODEL_NAME,
            tools=[
                {
                    "type": "web_search",
                    "user_location": {"type": "approximate", "country": "KR"}
                }
            ],
            input=(
                f"다음 투자 관련 최신 정보를 웹에서 검색하여 핵심 내용만 간결하게 요약해 주세요 "
                f"({language} 언어로). 검색어: {query}"
            ),
        )
        results = extract_web_search_blocks(resp)
        citation_count = sum(len(r["citations"]) for r in results)
        print(f"    → 웹 검색 결과: {len(results)}개 블록, 인용 수: {citation_count}개")
    except Exception as e:
        print(f"    ⚠ 웹 검색 오류: {e}")
    return results
