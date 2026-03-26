import json
import re
import xml.etree.ElementTree as ET
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
from curl_cffi import requests as cffi_requests
from trafilatura import extract
from tqdm import tqdm


def decode_google_news_url(url):
    """Google News RSS 링크의 내부 API를 호출하여 최종 원본 기사 URL을 반환합니다."""
    try:
        with requests.Session() as session:
            session.headers.update({
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36",
            })
            
            base64_id = url.split('/articles/')[-1].split('?')[0]
            
            res = session.get(url, timeout=5)
            res.raise_for_status()
            
            sig_match = re.search(r'data-n-a-sg="([^"]+)"', res.text)
            ts_match = re.search(r'data-n-a-ts="([^"]+)"', res.text)
            
            if not sig_match or not ts_match:
                return None
                
            rpc_payload = [
                "Fbv4je",
                f'["garturlreq",[["X","X",["X","X"],null,null,1,1,"US:en",null,1,null,null,null,null,null,0,1],"X","X",1,[1,1,1],1,1,null,0,0,null,0],"{base64_id}",{ts_match.group(1)},"{sig_match.group(1)}"]'
            ]
            req_data = {"f.req": json.dumps([[rpc_payload]])}
            headers = {"Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",}
            
            rpc_url = "https://news.google.com/_/DotsSplashUi/data/batchexecute"
            rpc_res = session.post(rpc_url, headers=headers, data=req_data, timeout=5)
            rpc_res.raise_for_status()

            clean_res = rpc_res.text.split("\n\n")[1] if "\n\n" in rpc_res.text else rpc_res.text
            parsed_data = json.loads(clean_res)
            
            return json.loads(parsed_data[0][2])[1]
    except Exception as e:
        print(f"\n[ERROR] URL 디코딩 실패 ({url[:30]}...): {e}")
        return None


def extract_body(url, language="ko"):
    """원본 URL에 접속하여 기사 본문 텍스트를 추출합니다."""
    try:
        resp = cffi_requests.get(
            url, 
            impersonate="chrome", 
            timeout=10
        )
        body = extract(
            resp.text,
            favor_precision=True,
            target_language=language,
            include_comments=False,
            include_tables=False,
            deduplicate=True,
        )
        return (body or "").strip()
    except Exception as e:
        print(f"\n[ERROR] 본문 추출 실패 ({url[:30]}...): {e}")
        return ""


def rss_to_kst(pubdate: str) -> str:
    """Google RSS의 GMT 시간을 한국 시간(YYYY-MM-DD HH:MM:SS)으로 변환합니다."""
    try:
        dt_utc = datetime.strptime(pubdate, "%a, %d %b %Y %H:%M:%S %Z").replace(tzinfo=ZoneInfo("UTC"))
        return dt_utc.astimezone(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return pubdate


def search_google_news(keywords, language="ko", max_results=3):
    """키워드로 구글 뉴스 RSS를 검색하고 기사 목록과 본문을 수집합니다."""
    news_results = []

    if isinstance(keywords, str):
        keywords = [keywords]

    for keyword in keywords:
        url = f"https://news.google.com/rss/search?q={keyword}&hl={language}"
        
        try:
            resp = requests.get(url, timeout=5)
            resp.raise_for_status()
            root = ET.fromstring(resp.text)
            
            items = root.findall('.//item')[:max_results]
            
            for item in tqdm(items, desc="[INFO] 기사 수집 진행", bar_format='{desc}: {n_fmt}/{total_fmt} [{elapsed}<{remaining}]'):
                title = item.find('title').text
                link = item.find('link').text
                pub_date = rss_to_kst(item.find('pubDate').text)

                src_url = decode_google_news_url(link)
                if not src_url:
                    continue
                
                body = extract_body(src_url, language)

                news_results.append({
                    'title': title,
                    'body': body,
                    'pubDate': pub_date,
                })
        except Exception as e:
            print(f"\n[ERROR] RSS 검색 실패: {e}")

    return news_results


def format_news_list(news_list):
    """수집한 뉴스 리스트를 문자열로 포맷팅합니다."""
    if not news_list:
        return "(관련 뉴스 없음)"
    lines = []
    for idx, news in enumerate(news_list, 1):
        lines.append(f"{idx}. {news['title']} ({news['pubDate']})\n{news['body']}")
    
    return '\n\n'.join(lines)


if __name__ == "__main__":
    while True:
        keyword = input("\n검색할 키워드를 입력하세요 (종료: q): ").strip()
        
        if not keyword or keyword.lower() == 'q':
            print("프로그램을 종료합니다.")
            break
            
        print("=" * 60)
        news_list = search_google_news([keyword], language="ko", max_results=3)
        
        print("\n[수집 결과]")
        print(format_news_list(news_list))
        print("=" * 60)