import requests
from datetime import datetime
import xml.etree.ElementTree as ET

def search_google_news(keywords, language="ko", max_results=5):
    """구글 뉴스에서 키워드별로 뉴스 검색 (뉴스 제목, 링크, 시간)"""
    news_results = []
    for kw in keywords:
        url = f"https://news.google.com/rss/search?q={kw}&hl={language}"
        try:
            resp = requests.get(url, timeout=5)
            if resp.status_code == 200:
                root = ET.fromstring(resp.text)
                for item in root.findall('.//item')[:max_results]:
                    title = item.find('title').text
                    link = item.find('link').text
                    pubDate = item.find('pubDate').text
                    news_results.append({
                        'keyword': kw,
                        'title': title,
                        'link': link,
                        'pubDate': pubDate
                    })
        except Exception:
            continue
    return news_results

def format_news_list(news_list):
    """뉴스 리스트를 문자열로 포맷"""
    if not news_list:
        return "(관련 뉴스 없음)"
    lines = []
    for news in news_list:
        lines.append(f"[{news['keyword']}] {news['title']} ({news['pubDate']})\n{news['link']}")
    return '\n'.join(lines)
