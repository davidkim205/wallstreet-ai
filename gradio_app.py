import argparse
import base64
import hashlib
import html as html_lib
import json
import os
import re
import time
from pathlib import Path
from queue import Empty, Queue
from threading import Thread

import gradio as gr
import requests
from openai import OpenAI
from pydantic import BaseModel, ValidationError

# ─────────────────────────────────────────────────────────────
# 설정
# ─────────────────────────────────────────────────────────────
PERSONA_FILE = Path(os.environ.get("PERSONA_FILE", "persona.jsonl"))
DEFAULT_ENDPOINT = os.environ.get("API_ENDPOINT", "http://127.0.0.1:8000/analyze/")
IMAGE_CACHE_DIR = Path(".persona_images")
IMAGE_CACHE_DIR.mkdir(exist_ok=True)

_openai_client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

EXAMPLES_BY_TYPE = {
    "screener":     "PER 낮은 대형주 추천해주세요",
    "technical":    "Apple(AAPL) 차트 분석해주세요",
    "fundamental":  "Microsoft 재무상태 어때요?",
    "news_summary": "Tesla 최근 뉴스 요약해 주세요",
    "comparison":   "Apple vs Microsoft 비교 분석해 주세요",
    "earnings":     "2025년 4분기 삼성전자 실적은 어땠나요?",
    "swot":         "OpenAI 경쟁력 분석해 주세요",
    "general":      "Tesla(TSLA) 어떻게 보시나요?",
    "watchlist":    "내 관심종목(삼성전자, SK하이닉스, Apple, Microsoft, Tesla) 현황 봐주세요",
}

ANALYSIS_TYPE_LABELS = {
    "screener":     "스크리너",
    "technical":    "기술적 분석",
    "fundamental":  "기본적 분석",
    "news_summary": "뉴스 요약",
    "comparison":   "비교 분석",
    "earnings":     "실적 분석",
    "swot":         "SWOT 분석",
    "general":      "일반 질문",
    "watchlist":    "관심종목",
}

EXAMPLE_QUERIES = list(EXAMPLES_BY_TYPE.values())

AUTO_SCROLL_JS = """
<script>
(function(){
  function scroll(){
    var el = document.getElementById('log-scroll') || document.getElementById('answer-scroll');
    if(el) el.scrollTop = el.scrollHeight;
  }
  var mo = new MutationObserver(scroll);
  function attach(){
    var root = document.getElementById('output-col') || document.body;
    mo.observe(root, {childList:true, subtree:true, characterData:true});
    scroll();
  }
  attach();
  setInterval(scroll, 400);
})();
</script>
"""


# ─────────────────────────────────────────────────────────────
# 헬퍼
# ─────────────────────────────────────────────────────────────
def to_md(text):
    return text or ""

def timer_text(elapsed):
    return f"⏱ {elapsed}"

def _make_elapsed():
    t0 = time.time()
    return lambda: f"{time.time()-t0:.1f}초"

# 마크다운 링크·URL 제거
_PAREN_MD = re.compile(r'\s*\(\s*\[[^\]]*\]\([^)]*\)\s*\)')
_MD_LINK  = re.compile(r'\[([^\]]*)\]\([^)]*\)')
_PAREN_URL= re.compile(r'\s*\(https?://[^\)]*\)')
_BARE_URL = re.compile(r'https?://\S+')
_PAREN_DOM= re.compile(r'\s*\([a-zA-Z0-9._-]+\.[a-zA-Z]{2,6}\)')

def _safe(text):
    t = text or ""
    t = _PAREN_MD.sub('', t)
    t = _MD_LINK.sub(r'\1', t)
    t = _PAREN_URL.sub('', t)
    t = _BARE_URL.sub('', t)
    t = _PAREN_DOM.sub('', t)
    t = re.sub(r'[ \t]{2,}', ' ', t).strip()
    t = re.sub(r'\.\s*\.', '.', t)
    return html_lib.escape(t).replace("\n", "<br>")


# ─────────────────────────────────────────────────────────────
# 진행 로그 HTML 빌더
# ─────────────────────────────────────────────────────────────
STATUS_ICONS = {
    "요청 수신": "📡", "인텐트": "🧠", "도구": "🔧", "시장": "📊",
    "뉴스": "📰", "컨텍스트": "🗂", "LLM": "✨", "완료": "✅",
}

def _status_icon(msg):
    for k, v in STATUS_ICONS.items():
        if k in msg:
            return v
    return "⏳"




# ─────────────────────────────────────────────────────────────
# 페르소나 모델 & 파일 IO
# ─────────────────────────────────────────────────────────────
class PersonaLine(BaseModel):
    name: str
    full_name: str
    background: str
    financial_mindset: str
    data_analysis_approach: str
    response_style: str
    key_principles: list[str]
    famous_quotes: list[str] | None = None
    birth_year: str | None = None
    nationality: str | None = None
    net_worth: str | None = None
    company: str | None = None
    title: str | None = None
    investment_style: str | None = None
    notable_trades: list[str] | None = None


_persona_cache: list = []
_persona_cache_mtime: float = 0.0


def _parse_personas():
    global _persona_cache, _persona_cache_mtime
    try:
        mtime = PERSONA_FILE.stat().st_mtime if PERSONA_FILE.exists() else 0.0
    except OSError:
        mtime = 0.0
    if mtime == _persona_cache_mtime and _persona_cache:
        return _persona_cache
    personas = []
    if not PERSONA_FILE.exists():
        _persona_cache, _persona_cache_mtime = personas, mtime
        return personas
    try:
        with PERSONA_FILE.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    if not isinstance(data, dict):
                        continue
                    if not data.get("full_name"):
                        data["full_name"] = data.get("name", "")
                    personas.append(PersonaLine(**data))
                except (json.JSONDecodeError, TypeError, ValidationError):
                    continue
    except OSError:
        pass
    _persona_cache, _persona_cache_mtime = personas, mtime
    return personas


def load_persona_names():
    choices = ["없음"]
    for p in _parse_personas():
        n = p.name.strip()
        if n and n not in choices:
            choices.append(n)
    return choices


def load_persona_summary(name):
    if not name or name == "없음":
        return ""
    for p in _parse_personas():
        if p.name.strip() == name:
            title_str = f" · {p.title}" if p.title else ""
            company_str = f" ({p.company})" if p.company else ""
            summary = (p.financial_mindset[:80] + "…") if len(p.financial_mindset) > 80 else p.financial_mindset
            return f"**{p.full_name}**{title_str}{company_str}\n\n{summary}"
    return ""


# ─────────────────────────────────────────────────────────────
# 프로필 카드
# ─────────────────────────────────────────────────────────────
_AVATAR_COLORS = [
    ("#0f766e","#ccfbf1"),("#0e7490","#cffafe"),("#1d4ed8","#dbeafe"),
    ("#7c3aed","#ede9fe"),("#b45309","#fef3c7"),("#be185d","#fce7f3"),
]

def _initials(name):
    parts = name.strip().split()
    if not parts: return "?"
    if len(parts) == 1: return parts[0][:2].upper()
    return (parts[0][0]+parts[-1][0]).upper()

def _avatar_color(name):
    return _AVATAR_COLORS[sum(ord(c) for c in name) % len(_AVATAR_COLORS)]

def build_profile_html(p: PersonaLine):
    bg, fg = _avatar_color(p.full_name)
    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="120" height="120" viewBox="0 0 120 120">
  <defs><linearGradient id="ag" x1="0" y1="0" x2="1" y2="1">
    <stop offset="0%" stop-color="{bg}"/><stop offset="100%" stop-color="{bg}cc"/>
  </linearGradient></defs>
  <circle cx="60" cy="60" r="60" fill="#dbe5e2"/>
  <circle cx="60" cy="60" r="58" fill="url(#ag)"/>
  <ellipse cx="60" cy="48" rx="18" ry="20" fill="{fg}55"/>
  <ellipse cx="60" cy="90" rx="30" ry="22" fill="{fg}44"/>
  <circle cx="60" cy="60" r="58" fill="none" stroke="{fg}66" stroke-width="2"/>
</svg>"""
    meta_rows = []
    for label, val in [
        ("출생", p.birth_year), ("국적", p.nationality), ("소속", p.company),
        ("직책", p.title), ("자산 규모", p.net_worth), ("투자 스타일", p.investment_style),
    ]:
        if val:
            meta_rows.append(f'<div class="pf-meta-row"><span class="pf-meta-label">{_safe(label)}</span>'
                             f'<span class="pf-meta-val">{_safe(val)}</span></div>')
    principles = "".join(f"<li>{_safe(x)}</li>" for x in (p.key_principles or []))
    quotes = "".join(f'<blockquote class="pf-quote">&#8220;{_safe(q)}&#8221;</blockquote>' for q in (p.famous_quotes or []))
    trades = "".join(f'<div class="pf-trade-item">▸ {_safe(t)}</div>' for t in (p.notable_trades or []))

    return f"""<div class="pf-card">
  <div class="pf-header">
    <div class="pf-avatar">{svg}</div>
    <div class="pf-header-info">
      <h2 class="pf-name">{_safe(p.full_name)}</h2>
      <p class="pf-subtitle">{_safe(p.title or "")}{("&nbsp;·&nbsp;" + _safe(p.company)) if p.company else ""}</p>
      <p class="pf-bg">{_safe(p.background)}</p>
    </div>
  </div>
  {('<div class="pf-meta-grid">' + "".join(meta_rows) + '</div>') if meta_rows else ''}
  <div class="pf-section"><h3 class="pf-section-title">💡 투자 철학</h3><p class="pf-text">{_safe(p.financial_mindset)}</p></div>
  <div class="pf-section"><h3 class="pf-section-title">📊 데이터 분석 방식</h3><p class="pf-text">{_safe(p.data_analysis_approach)}</p></div>
  <div class="pf-section"><h3 class="pf-section-title">🗣 답변 스타일</h3><p class="pf-text">{_safe(p.response_style)}</p></div>
  {('<div class="pf-section"><h3 class="pf-section-title">📌 핵심 원칙</h3><ul class="pf-list">' + principles + '</ul></div>') if principles else ''}
  {('<div class="pf-section"><h3 class="pf-section-title">📁 주요 투자 사례</h3><div class="pf-trades">' + trades + '</div></div>') if trades else ''}
  {('<div class="pf-section">' + quotes + '</div>') if quotes else ''}
</div>"""

def get_profile_html(name):
    if not name or name == "없음":
        return '<p class="pf-empty">왼쪽에서 투자자를 선택하세요.</p>'
    for p in _parse_personas():
        if p.name.strip() == name:
            return build_profile_html(p)
    return '<p class="pf-empty">해당 페르소나 정보를 찾을 수 없습니다.</p>'


# ─────────────────────────────────────────────────────────────
# Wikipedia 인물 사진 (캐시 포함)
# ─────────────────────────────────────────────────────────────
def _image_cache_path(full_name: str) -> Path:
    key = hashlib.md5(full_name.encode()).hexdigest()
    return IMAGE_CACHE_DIR / f"{key}.b64"


def _extract_english_name(full_name: str) -> str:
    """full_name에서 영어 이름을 추출. 괄호 안 영어가 있으면 그것을 우선 사용."""
    paren_match = re.search(r'\(([A-Za-z][^)]+)\)', full_name)
    if paren_match:
        return paren_match.group(1).strip()
    ascii_part = re.sub(r'[^\x00-\x7F]+', '', full_name).strip()
    return ascii_part if ascii_part else full_name


def _extract_english_keywords(text: str) -> str:
    """한국어 텍스트에서 영어 단어/고유명사만 추출."""
    words = re.findall(r'[A-Za-z][A-Za-z\s&.]{2,}', text)
    # 짧거나 일반적인 단어 제거
    stopwords = {"the", "and", "for", "with", "from", "that", "this", "are", "was", "has"}
    result = []
    for w in words:
        w = w.strip()
        if w.lower() not in stopwords and len(w) > 3:
            result.append(w)
        if len(result) >= 3:
            break
    return " ".join(result)

def _translate_to_english_name(name: str) -> str:
    """OpenAI를 사용해 가장 가능성 높은 영어 Wikipedia 이름으로 변환"""
    try:
        resp = _openai_client.chat.completions.create(
            model="gpt-5-mini",
            messages=[
                {"role": "system", "content": "Convert a person's name into the most likely English Wikipedia page title. Only output the name."},
                {"role": "user", "content": name}
            ],
            temperature=0
        )
        return resp.choices[0].message.content.strip()
    except Exception:
        return name
def _wikidata_image(name: str) -> str:
    """Wikidata에서 이미지 가져오기 (fallback)"""
    try:
        url = "https://www.wikidata.org/w/api.php"
        params = {
            "action": "wbsearchentities",
            "search": name,
            "language": "en",
            "format": "json",
            "limit": 1
        }
        r = requests.get(url, params=params, timeout=10).json()
        if not r.get("search"):
            return ""

        entity_id = r["search"][0]["id"]

        entity_url = f"https://www.wikidata.org/wiki/Special:EntityData/{entity_id}.json"
        data = requests.get(entity_url, timeout=10).json()

        claims = data["entities"][entity_id].get("claims", {})
        if "P18" in claims:
            filename = claims["P18"][0]["mainsnak"]["datavalue"]["value"]
            return f"https://commons.wikimedia.org/wiki/Special:FilePath/{filename}"

    except Exception:
        pass

    return ""    
def _wikipedia_search_image(query: str, headers: dict) -> str:
    """Wikipedia search API로 쿼리에 맞는 첫 번째 인물 사진을 반환."""
    import urllib.parse
    search_url = (
        "https://en.wikipedia.org/w/api.php"
        f"?action=query&list=search&srsearch={urllib.parse.quote(query)}"
        "&srnamespace=0&srlimit=1&format=json"
    )
    resp = requests.get(search_url, headers=headers, timeout=10)
    resp.raise_for_status()
    results = resp.json().get("query", {}).get("search", [])
    if not results:
        return ""
    page_title = results[0]["title"]
    img_url = (
        "https://en.wikipedia.org/w/api.php"
        f"?action=query&titles={urllib.parse.quote(page_title)}"
        "&prop=pageimages&format=json&pithumbsize=500"
    )
    resp2 = requests.get(img_url, headers=headers, timeout=10)
    resp2.raise_for_status()
    pages = resp2.json().get("query", {}).get("pages", {})
    for page in pages.values():
        thumb = page.get("thumbnail", {}).get("source")
        if thumb:
            return thumb
    return ""

def _fetch_from_multi_wiki(name):
    langs = ["en", "ko", "ja"]

    for lang in langs:
        try:
            url = (
                f"https://{lang}.wikipedia.org/w/api.php"
                f"?action=query&titles={name}"
                "&prop=pageimages&format=json&pithumbsize=500"
            )
            r = requests.get(url, timeout=8).json()
            pages = r.get("query", {}).get("pages", {})
            for page in pages.values():
                if page.get("thumbnail"):
                    return page["thumbnail"]["source"]
        except:
            continue
    return ""

def _fetch_wikipedia_image(full_name, background=None):

    # 1. Wikidata (가장 강력)
    img = _wikidata_image(full_name)
    if img:
        return img

    # 2. 다국어 wiki
    img = _fetch_from_multi_wiki(full_name)
    if img:
        return img

    # 3. 영어 이름 variants
    queries = [
    full_name,
    _extract_english_name(full_name),
    _translate_to_english_name(full_name),
    ]

    if background:
        queries.append(_extract_english_keywords(background))
        
    for q in queries:
        headers = {"User-Agent": "Mozilla/5.0"}

        img = _wikipedia_search_image(q, headers)
        if img:
            return img

    return ""

def generate_persona_image(name: str) -> str:
    """투자자 이름으로 Wikipedia 실제 사진을 가져와 base64 data-URL을 반환."""
    if not name or name == "없음":
        return ""

    persona = None
    for p in _parse_personas():
        if p.name.strip() == name:
            persona = p
            break
    if persona is None:
        return ""

    cache_path = _image_cache_path(persona.full_name)
    if cache_path.exists():
        return cache_path.read_text()

    try:
        data_url = _fetch_wikipedia_image(persona.full_name, persona.background)
        if data_url:
            cache_path.write_text(data_url)
            return data_url
        return ""
    except Exception as e:
        return f"__error__{e}"


def build_profile_html_with_image(name: str) -> str:
    """이미지 생성 후 프로필 HTML을 반환 (버튼 클릭용)."""
    if not name or name == "없음":
        return '<p class="pf-empty">왼쪽에서 투자자를 선택하세요.</p>'

    persona = None
    for p in _parse_personas():
        if p.name.strip() == name:
            persona = p
            break
    if persona is None:
        return '<p class="pf-empty">해당 페르소나 정보를 찾을 수 없습니다.</p>'

    data_url = generate_persona_image(name)
    if data_url and not data_url.startswith("__error__"):
        img_html = f'<img src="{data_url}" style="width:120px;height:120px;border-radius:50%;object-fit:cover;border:2px solid #ccc">'
    else:
        img_html = None  # 실패 시 기본 SVG 아바타 유지

    html = build_profile_html(persona)
    if img_html:
        html = re.sub(
            r'<div class="pf-avatar">.*?</div>',
            f'<div class="pf-avatar">{img_html}</div>',
            html,
            flags=re.DOTALL,
        )
    return html


# ─────────────────────────────────────────────────────────────
# 스트림 분석 (핵심 로직)
# ─────────────────────────────────────────────────────────────
def _make_log_html(log_lines):
    """log_lines: list of (type, text)"""
    if not log_lines:
        return ''
    rows = []
    for t, text in log_lines:
        safe = html_lib.escape(text)
        if t == "status":
            icon = next((v for k,v in STATUS_ICONS.items() if k in text), "⏳")
            rows.append(f'<div class="log-status">{icon} <span>{safe}</span></div>')
        elif t == "stdout":
            rows.append(f'<div class="log-stdout"><pre>{safe}</pre></div>')
        elif t == "error":
            rows.append(f'<div class="log-error">❌ {safe}</div>')
        elif t == "done":
            rows.append('<div class="log-done">✅ 분석 완료</div>')
    return "\n".join(rows)


def _wrap_log(inner):
    return (
        '<div id="output-panel" class="phase-log">'
        '<div class="panel-header"><span class="panel-title">진행 과정</span></div>'
        '<div id="log-scroll">' + inner + '</div>'
        '</div>'
    )


def _wrap_answer(md_html, timer_str):
    return (
        '<div id="output-panel" class="phase-answer">'
        '<div class="panel-header"><span class="panel-title">분석 결과</span>'
        f'<span class="panel-timer">{html_lib.escape(timer_str)}</span></div>'
        '<div id="answer-scroll" class="md-body">' + md_html + '</div>'
        '</div>'
    )


def _md_to_html(text):
    """마크다운 텍스트를 간단한 HTML로 변환 (Gradio Markdown 렌더러 대신)."""
    import re as _re
    t = html_lib.escape(text)
    # 헤더
    t = _re.sub(r'(?m)^#### (.+)$', r'<h4>\1</h4>', t)
    t = _re.sub(r'(?m)^### (.+)$',  r'<h3>\1</h3>', t)
    t = _re.sub(r'(?m)^## (.+)$',   r'<h2>\1</h2>', t)
    t = _re.sub(r'(?m)^# (.+)$',    r'<h1>\1</h1>', t)
    # bold / italic
    t = _re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', t)
    t = _re.sub(r'\*(.+?)\*',      r'<em>\1</em>', t)
    # 인라인 코드
    t = _re.sub(r'`(.+?)`', r'<code>\1</code>', t)
    # 리스트
    t = _re.sub(r'(?m)^- (.+)$',    r'<li>\1</li>', t)
    t = _re.sub(r'(?m)^\d+\. (.+)$',r'<li>\1</li>', t)
    # 줄바꿈
    t = t.replace('\n\n', '</p><p>')
    t = t.replace('\n',   '<br>')
    return '<p>' + t + '</p>'


IDLE_PANEL = (
    '<div id="output-panel" class="phase-idle">'
    '<div class="idle-msg">🔍 왼쪽에서 질문을 입력하고 질문하기를 누르세요.</div>'
    '</div>'
)


def stream_analyze(query, persona_name, endpoint):
    """
    Yields: (panel_html, timer_md, result_json)
    - delta 전: panel_html = 진행 과정 로그 HTML
    - delta 후: panel_html = 분석 결과 HTML (누적)
    """
    query = (query or "").strip()
    endpoint = (endpoint or "").strip()
    persona_name = (persona_name or "").strip()

    if not query:
        yield (_wrap_log('<div class="log-error">❌ 질문을 입력해주세요.</div>'), "", "")
        return
    if not endpoint:
        yield (_wrap_log('<div class="log-error">❌ API 엔드포인트를 확인해주세요.</div>'), "", "")
        return

    text_acc    = ""
    log_lines   = []
    frozen_log  = ""
    result_json = ""
    first_delta = False
    worker_done = False
    terminal    = False
    elapsed     = _make_elapsed()
    eq: Queue   = Queue()

    def reader():
        try:
            body = {"query": query}
            if persona_name and persona_name != "없음":
                body["persona_name"] = persona_name
            with requests.post(endpoint, json=body,
                               headers={"Accept": "text/event-stream"},
                               stream=True, timeout=(10, 300)) as resp:
                resp.raise_for_status()
                for raw in resp.iter_lines(chunk_size=1, decode_unicode=True):
                    if not raw: continue
                    line = raw.strip()
                    if not line.startswith("data:"): continue
                    try:
                        eq.put(("event", json.loads(line[5:].strip())))
                    except json.JSONDecodeError:
                        continue
        except requests.exceptions.ConnectionError:
            eq.put(("exception", f"연결 실패: {endpoint}"))
        except requests.exceptions.Timeout:
            eq.put(("exception", "요청 시간 초과"))
        except requests.RequestException as e:
            eq.put(("exception", f"요청 실패: {e}"))
        finally:
            eq.put(("worker_done", None))

    Thread(target=reader, daemon=True).start()

    while True:
        try:
            kind, payload = eq.get(timeout=0.1)
            buf = [(kind, payload)]
            while True:
                try: buf.append(eq.get_nowait())
                except Empty: break
        except Empty:
            buf = []

        for kind, payload in buf:
            if kind == "event":
                et = payload.get("type")

                if et == "status":
                    if not first_delta:
                        msg = payload.get("message", "")
                        if msg:
                            log_lines.append(("status", msg))

                elif et == "stdout":
                    if not first_delta:
                        msg = payload.get("message", "")
                        if msg:
                            if log_lines and log_lines[-1][0] == "stdout":
                                log_lines[-1] = ("stdout", log_lines[-1][1] + msg)
                            else:
                                log_lines.append(("stdout", msg))

                elif et == "delta":
                    delta = payload.get("delta", "")
                    if delta:
                        if not first_delta:
                            first_delta = True
                            frozen_log = _make_log_html(log_lines)
                        text_acc += delta

                elif et == "result":
                    result = payload.get("result", payload)
                    result_json = json.dumps(result, ensure_ascii=False, indent=2)
                    if not text_acc and result.get("llm_response"):
                        first_delta = True
                        frozen_log  = _make_log_html(log_lines)
                        text_acc    = result["llm_response"]
                    terminal = True

                elif et == "error":
                    msg = payload.get("message", "오류 발생")
                    log_lines.append(("error", msg))
                    terminal = True

                elif et == "done":
                    terminal = True

            elif kind == "exception":
                log_lines.append(("error", str(payload)))
                terminal = True

            elif kind == "worker_done":
                worker_done = True

        t = timer_text(elapsed())
        if first_delta:
            panel = _wrap_answer(_md_to_html(text_acc), t)
        else:
            panel = _wrap_log(_make_log_html(log_lines))
        yield (panel, t, result_json)

        if worker_done:
            break

    # 최종
    t = timer_text(elapsed())
    if first_delta:
        panel = _wrap_answer(_md_to_html(text_acc), t)
    else:
        panel = _wrap_log(_make_log_html(log_lines))
    yield (panel, t, result_json)


# ─────────────────────────────────────────────────────────────
# 페르소나 생성 스트림
# ─────────────────────────────────────────────────────────────
def generate_persona_stream(info, endpoint):
    if not info or not info.strip():
        yield "인물 정보를 입력해주세요.", "{}", timer_text("0.0초")
        return

    persona_ep = endpoint.rstrip("/").rsplit("/", 1)[0] + "/persona/"
    elapsed = _make_elapsed()
    q: Queue = Queue()

    def worker():
        try:
            r = requests.post(persona_ep, json={"info": info.strip()}, timeout=(10, 300))
            r.raise_for_status()
            q.put(("ok", r.json()))
        except requests.exceptions.ConnectionError:
            q.put(("error", f"연결 실패: {persona_ep}"))
        except requests.exceptions.Timeout:
            q.put(("error", "요청 시간 초과"))
        except requests.RequestException as e:
            q.put(("error", f"요청 실패: {e}"))

    Thread(target=worker, daemon=True).start()

    while True:
        try:
            kind, payload = q.get_nowait(); break
        except Empty:
            yield (
                '<div class="ws-loading shimmer"><div class="ws-loading-title">⏳ 페르소나 생성 중...</div>'
                '<div class="ws-loading-msg">AI가 인물 정보를 검색하고 있습니다</div></div>',
                "{}", timer_text(elapsed())
            )
            time.sleep(0.3)

    if kind == "error":
        yield payload, "{}", timer_text(elapsed())
        return

    data = payload
    md = "\n\n".join([
        f"**이름**: {data.get('name','')}",
        f"**배경**: {data.get('background','')}",
        f"**금융 사고 방식**: {data.get('financial_mindset','')}",
        f"**데이터 분석 방식**: {data.get('data_analysis_approach','')}",
        f"**답변 스타일**: {data.get('response_style','')}",
        f"**핵심 원칙**: {', '.join(data.get('key_principles',[]))}",
    ])
    if data.get("famous_quotes"):
        md += f"\n\n**어록**: {' / '.join(data['famous_quotes'])}"
    yield md, json.dumps(data, ensure_ascii=False, indent=2), timer_text(elapsed())


# ─────────────────────────────────────────────────────────────
# CSS
# ─────────────────────────────────────────────────────────────
CSS = """
@import url("https://fonts.googleapis.com/css2?family=IBM+Plex+Sans+KR:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;600&display=swap");

:root {
    --ws-bg:           #f7faf9;
    --ws-surface:      #ffffff;
    --ws-border:       #dbe5e2;
    --ws-text:         #182022;
    --ws-muted:        #5d6b70;
    --ws-accent:       #0f766e;
    --ws-accent2:      #14b8a6;
    --ws-code-bg:      #f1f5f9;
    --ws-green-bg:     #f0fdfa;
    --ws-green-border: #99f6e4;
}

/* ── 전역 폰트 ── */
.gradio-container,
.gradio-container :is(h1,h2,h3,h4,h5,h6,p,span,div,label,button,input,textarea,select) {
    font-family: "IBM Plex Sans KR","Noto Sans KR","Source Sans 3",sans-serif !important;
    letter-spacing: 0.005em;
}
.gradio-container {
    background: radial-gradient(circle at top left,#edf9f6 0%,#f8fbfc 35%,#fdfefe 100%) !important;
}

/* ── 헤더 ── */
#ws-header {
    background: linear-gradient(135deg,#f0fdfa 0%,#e8faf7 55%,#f7faf9 100%);
    border: 1px solid #b2e8e2;
    border-radius: 14px;
    padding: 20px 28px 16px;
    margin-bottom: 8px;
    position: relative; overflow: hidden;
}
#ws-header::before {
    content:""; position:absolute; top:-70px; right:-70px;
    width:260px; height:260px;
    background:radial-gradient(circle,rgba(20,184,166,.13) 0%,transparent 68%);
    pointer-events:none;
}
#ws-header h1 { font-size:22px !important; font-weight:700 !important; color:#0b3b39 !important; margin:0 0 4px !important; }
#ws-header p  { font-size:13px !important; color:var(--ws-muted) !important; margin:0 !important; }
#ws-header .ws-badge {
    display:inline-block; padding:1px 8px; border-radius:20px;
    font-size:10px; font-weight:700; letter-spacing:.07em; text-transform:uppercase;
    background:rgba(15,118,110,.1); border:1px solid rgba(15,118,110,.25);
    color:var(--ws-accent); margin-right:7px; vertical-align:middle;
}

/* ── 탭 ── */
.tab-nav button {
    background:transparent !important; color:var(--ws-muted) !important;
    border:none !important; border-bottom:2px solid transparent !important;
    font-size:13px !important; font-weight:600 !important;
    padding:8px 18px !important; border-radius:0 !important;
    transition:color .18s,border-color .18s !important;
}
.tab-nav button.selected,.tab-nav button:hover {
    color:var(--ws-accent) !important; border-bottom-color:var(--ws-accent) !important;
    background:transparent !important;
}

/* ══════════════════════════════════════════════
   질문하기 탭 — 좌우 분할 레이아웃
══════════════════════════════════════════════ */

/* 좌: 입력 패널 */
#input-col {
    background: var(--ws-surface);
    border: 1px solid var(--ws-border) !important;
    border-radius: 14px !important;
    padding: 18px 20px !important;
    box-shadow: 0 2px 12px rgba(16,24,40,.04);
    display: flex; flex-direction: column; gap: 10px;
}

.ws-label {
    font-size: 10px; font-weight: 700; text-transform: uppercase;
    letter-spacing: .08em; color: var(--ws-muted); margin-bottom: 4px;
}
.ws-divider { border:none; border-top:1px solid var(--ws-border); margin:10px 0; }

/* 페르소나 요약 */
#persona-summary {
    background: linear-gradient(180deg,#f9fefd 0%,#f3fbf9 100%) !important;
    border: 1px solid #cde8e3 !important; border-radius: 10px !important;
    padding: 10px 14px !important; font-size: 13px !important;
    color: var(--ws-text) !important;
}
#persona-summary > .wrap,#persona-summary > div.prose { padding:0!important;border:none!important;box-shadow:none!important; }

/* 예시 버튼 그리드 */
#example-grid {
    display: grid;
    grid-template-columns: 1fr 1fr 1fr;
    gap: 5px;
}
#example-grid button {
    width: 100% !important; text-align: left !important;
    background: var(--ws-code-bg) !important; border: 1px solid var(--ws-border) !important;
    border-radius: 8px !important; color: var(--ws-text) !important;
    font-size: 11px !important; padding: 7px 10px !important;
    white-space: normal !important; line-height: 1.4 !important;
    transition: border-color .18s, color .18s, background .18s !important;
    min-height: 44px;
}
#example-grid button:hover {
    background: var(--ws-green-bg) !important; border-color: var(--ws-accent2) !important;
    color: var(--ws-accent) !important;
}

/* 분석 버튼 */
#run-btn {
    background: linear-gradient(135deg,#0f766e 0%,#14b8a6 100%) !important;
    border: none !important; color: #fff !important; font-weight: 700 !important;
    font-size: 13px !important; border-radius: 9px !important;
    transition: opacity .18s, transform .12s !important;
}
#run-btn:hover { opacity:.87!important; transform:translateY(-1px)!important; }
#clear-btn {
    background: var(--ws-code-bg) !important; border: 1px solid var(--ws-border) !important;
    color: var(--ws-muted) !important; border-radius: 9px !important; font-size:12px!important;
    transition: border-color .18s,color .18s !important;
}
#clear-btn:hover { border-color:var(--ws-accent)!important; color:var(--ws-accent)!important; }

/* 새로고침 버튼 */
#refresh-btn {
    min-width:34px!important; padding:0 8px!important;
    background:var(--ws-code-bg)!important; border:1px solid var(--ws-border)!important;
    color:var(--ws-muted)!important; border-radius:8px!important; font-size:15px!important;
    transition:color .18s,border-color .18s!important;
}
#refresh-btn:hover { color:var(--ws-accent)!important; border-color:var(--ws-accent)!important; background:var(--ws-green-bg)!important; }

/* 우: 출력 컬럼 */
#output-col {
    border: 1px solid var(--ws-border) !important;
    border-radius: 14px !important;
    overflow: hidden;
    background: var(--ws-surface);
    box-shadow: 0 2px 12px rgba(16,24,40,.04);
}
/* output-col 안의 Gradio 래퍼들 여백 제거 */
#output-col > .wrap, #output-col > div {
    padding: 0 !important; margin: 0 !important;
    border: none !important; box-shadow: none !important;
}

/* 단일 출력 패널 */
#output-panel {
    display: flex;
    flex-direction: column;
    min-height: 520px;
}

/* 패널 헤더 */
.panel-header {
    display: flex; align-items: center; justify-content: space-between;
    padding: 10px 16px;
    background: #f7faf9;
    border-bottom: 1px solid var(--ws-border);
    flex-shrink: 0;
}
.panel-title {
    font-size: 11px; font-weight: 700; text-transform: uppercase;
    letter-spacing: .07em; color: var(--ws-muted);
}
.panel-timer {
    font-size: 11px; font-weight: 600; color: var(--ws-accent);
    background: var(--ws-green-bg); border: 1px solid var(--ws-green-border);
    padding: 2px 10px; border-radius: 999px;
}

/* 대기 상태 */
.idle-msg {
    display: flex; align-items: center; justify-content: center;
    height: 480px;
    color: var(--ws-muted); font-size: 13px;
}

/* 로그 단계 */
#log-scroll {
    flex: 1;
    overflow-y: auto;
    padding: 14px 18px;
    font-size: 12px; line-height: 1.7;
    max-height: calc(100vh - 260px);
}
.log-status {
    display: flex; align-items: flex-start; gap: 7px;
    padding: 3px 0; color: var(--ws-text);
}
.log-status span { color: var(--ws-text); }
.log-stdout pre {
    margin: 3px 0; padding: 4px 10px;
    background: #f1f8f7; border-left: 3px solid var(--ws-accent2);
    border-radius: 0 5px 5px 0;
    font-family: "JetBrains Mono","IBM Plex Mono",monospace !important;
    font-size: 11px !important; color: var(--ws-muted);
    white-space: pre-wrap; word-break: break-all;
}
.log-done  { color: var(--ws-accent); font-weight: 700; padding: 4px 0; }
.log-error { color: #e53e3e; padding: 4px 0; }

/* 답변 단계 */
#answer-scroll {
    flex: 1;
    overflow-y: auto;
    padding: 18px 22px;
    max-height: calc(100vh - 260px);
}
.md-body {
    color: var(--ws-text); line-height: 1.75; font-size: 14.5px;
    font-family: "IBM Plex Sans KR","Noto Sans KR","Source Sans 3",sans-serif;
}
.md-body h1,.md-body h2,.md-body h3,.md-body h4 { color: #0b3b39; margin: .85em 0 .35em; }
.md-body h2 { font-size: 16px; border-bottom: 1px solid var(--ws-border); padding-bottom: 5px; }
.md-body h3 { font-size: 14px; color: var(--ws-accent); }
.md-body h4 { font-size: 13px; }
.md-body p  { margin: .4em 0; }
.md-body strong { font-weight: 700; }
.md-body em     { font-style: italic; }
.md-body ul,.md-body ol { margin: .4em 0; padding-left: 1.5em; }
.md-body li { margin: .2em 0; }
.md-body code {
    background: var(--ws-code-bg); color: #0b3b39;
    border: 1px solid #d9e2ec; border-radius: 5px;
    padding: .1em .35em; font-size: .91em;
    font-family: "JetBrains Mono","IBM Plex Mono",monospace;
}
.md-body pre {
    background: #0f172a; color: #e2e8f0;
    border-radius: 10px; border: 1px solid #1e293b;
    padding: .85em 1em; overflow-x: auto; margin: .7em 0;
}
.md-body pre code { background: transparent; border: none; color: inherit; padding: 0; }
.md-body blockquote {
    margin: .8em 0; padding: .6em .9em;
    border-left: 4px solid var(--ws-accent2);
    background: var(--ws-green-bg); color: #115e59;
    border-radius: 0 8px 8px 0;
}
.md-body table { width:100%; border-collapse:collapse; margin:.7em 0; }
.md-body th {
    background:#eef6f4; color:#0f3f3b; font-weight:600; font-size:12px;
    text-transform:uppercase; letter-spacing:.04em;
    padding:7px 10px; border:1px solid var(--ws-border);
}
.md-body td { border:1px solid var(--ws-border); padding:6px 10px; vertical-align:top; }
.md-body tr:hover td { background:#f9fefd; }

/* 타이머는 패널 헤더 안에 내장됨 */

/* ── 하단 JSON 고정 ── */
#result-json-wrap {
    border-top: 2px solid var(--ws-border);
    background: var(--ws-surface);
}
#result-json-wrap .accordion-header { padding: 10px 16px !important; }
#meta-box {
    max-height: 220px; overflow-y: auto;
    background: var(--ws-surface)!important; border:none!important;
}
#meta-box code,#meta-box pre {
    font-family:"JetBrains Mono",monospace!important;
    font-size:11.5px!important; color:var(--ws-muted)!important; background:transparent!important;
}

/* ── 로딩 (페르소나 탭용) ── */
.ws-loading {
    position:relative; overflow:hidden; border:1px solid #cde8e3; border-radius:12px;
    background:linear-gradient(180deg,#f9fefd 0%,#f3fbf9 100%); padding:14px 16px;
}
.ws-loading-title { color:var(--ws-accent); font-weight:700; margin-bottom:6px; }
.ws-loading-msg   { color:#365055; font-size:13px; }
.shimmer::after {
    content:""; position:absolute; top:0; left:-140%; width:80%; height:100%;
    background:linear-gradient(100deg,rgba(255,255,255,0) 0%,rgba(255,255,255,.55) 45%,rgba(255,255,255,0) 100%);
    animation:ws-shimmer 1.6s ease-in-out infinite;
}
@keyframes ws-shimmer { 0%{left:-140%} 100%{left:150%} }

/* ── 페르소나 생성 탭 ── */
#persona-result-wrapper {
    min-height:180px; max-height:50vh; overflow-y:auto!important;
    border:1px solid var(--ws-border)!important; border-radius:14px!important;
    background:var(--ws-surface)!important; padding:20px 24px!important;
    color:var(--ws-text)!important; font-size:14px!important; line-height:1.72!important;
}

/* ── 프로필 카드 ── */
#profile-wrapper { max-height:78vh; overflow-y:auto; padding:4px 2px; }
.pf-empty { color:var(--ws-muted); font-size:14px; text-align:center; padding:40px 20px; }
.pf-card { background:var(--ws-surface); border:1px solid var(--ws-border); border-radius:16px; overflow:hidden; box-shadow:0 4px 20px rgba(16,24,40,.07); }
.pf-header { display:flex; gap:24px; align-items:flex-start; padding:28px 28px 20px; background:linear-gradient(135deg,#f0fdfa 0%,#e8faf7 60%,#f7faf9 100%); border-bottom:1px solid var(--ws-border); }
.pf-avatar { flex-shrink:0; width:120px; height:120px; border-radius:50%; overflow:hidden; border:3px solid #b2e8e2; box-shadow:0 4px 16px rgba(15,118,110,.18); }
.pf-avatar svg { display:block; width:100%; height:100%; }
.pf-header-info { flex:1; min-width:0; }
.pf-name { font-size:22px!important; font-weight:700!important; color:#0b3b39!important; margin:0 0 4px!important; }
.pf-subtitle { font-size:13px!important; color:var(--ws-accent)!important; font-weight:600!important; margin:0 0 10px!important; }
.pf-bg { font-size:13px!important; color:var(--ws-muted)!important; line-height:1.6!important; margin:0!important; }
.pf-meta-grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(200px,1fr)); border-bottom:1px solid var(--ws-border); }
.pf-meta-row { display:flex; flex-direction:column; padding:12px 20px; border-right:1px solid var(--ws-border); }
.pf-meta-row:last-child { border-right:none; }
.pf-meta-label { font-size:10px; font-weight:700; text-transform:uppercase; letter-spacing:.07em; color:var(--ws-muted); margin-bottom:3px; }
.pf-meta-val { font-size:14px; font-weight:600; color:var(--ws-text); }
.pf-section { padding:18px 24px; border-bottom:1px solid #eef4f2; }
.pf-section:last-child { border-bottom:none; }
.pf-section-title { font-size:12px!important; font-weight:700!important; text-transform:uppercase!important; letter-spacing:.07em!important; color:var(--ws-accent)!important; margin:0 0 8px!important; }
.pf-text { font-size:14px!important; color:var(--ws-text)!important; line-height:1.7!important; margin:0!important; }
.pf-list { margin:0!important; padding-left:1.2em!important; }
.pf-list li { font-size:14px!important; color:var(--ws-text)!important; line-height:1.65!important; margin:4px 0!important; }
.pf-trades { display:flex; flex-direction:column; gap:6px; }
.pf-trade-item { font-size:13px; color:var(--ws-text); background:var(--ws-code-bg); border-left:3px solid var(--ws-accent2); border-radius:0 6px 6px 0; padding:6px 12px; }
.pf-quote { margin:6px 0!important; padding:.6em 1em!important; border-left:4px solid var(--ws-accent2)!important; background:var(--ws-green-bg)!important; color:#115e59!important; border-radius:0 8px 8px 0; font-size:14px!important; font-style:italic; }

/* ── 스크롤바 ── */
::-webkit-scrollbar { width:5px; height:5px; }
::-webkit-scrollbar-track { background:transparent; }
::-webkit-scrollbar-thumb { background:var(--ws-border); border-radius:3px; }
::-webkit-scrollbar-thumb:hover { background:#aec5c1; }
body:has(.options:not(.hide)) { overflow:hidden!important; }

/* ── 반응형 ── */
@media (max-width: 768px) {
    #example-grid { grid-template-columns: 1fr 1fr !important; }
    .pf-header { flex-direction:column; }
    .pf-avatar { width:80px; height:80px; }
}
"""

HEADER_HTML = """
<div id="ws-header">
  <h1>📈 Wallstreet AI</h1>
  <p><span class="ws-badge">Live</span>실적 · 뉴스 · 시장 트렌드를 한 곳에서 &mdash; AI 금융 분석 플랫폼</p>
</div>
"""


# ─────────────────────────────────────────────────────────────
# Gradio 앱 빌드
# ─────────────────────────────────────────────────────────────
def create_app(default_endpoint):
    theme = gr.themes.Soft(primary_hue="emerald", secondary_hue="teal",
                           neutral_hue="slate", radius_size="lg")

    with gr.Blocks(title="Wallstreet AI", css=CSS, theme=theme,
                   analytics_enabled=False) as demo:

        # 엔드포인트 — 숨김 상태로 보관 (UI에서 안 보임)
        endpoint_state = gr.State(default_endpoint)

        gr.HTML(HEADER_HTML)

        with gr.Tabs():

            # ════════════════════════════════════════════
            # TAB 1 : 질문하기  (좌/우 반반)
            # ════════════════════════════════════════════
            with gr.Tab("💬 질문하기"):
                with gr.Row(equal_height=False):

                    # ── 좌: 입력 패널 ──────────────────
                    with gr.Column(scale=1, min_width=280, elem_id="input-col"):

                        # 페르소나
                        gr.HTML("<p class='ws-label'>페르소나</p>")
                        with gr.Row():
                            persona_dd = gr.Dropdown(
                                label="", choices=load_persona_names(),
                                value="없음", interactive=True,
                                scale=5, show_label=False,
                            )
                            refresh_btn = gr.Button("↺", size="sm", scale=1,
                                                    min_width=34, elem_id="refresh-btn")
                        persona_summary = gr.Markdown(value="",elem_id="persona-summary",visible=True)

                        gr.HTML("<hr class='ws-divider'>")

                        # 예시 질문 (유형별 9개 버튼)
                        gr.HTML("<p class='ws-label'>예시 질문</p>")
                        with gr.Column(elem_id="example-grid"):
                            example_btns = []
                            for key, example_text in EXAMPLES_BY_TYPE.items():
                                label = ANALYSIS_TYPE_LABELS[key]
                                b = gr.Button(f"{label}: {example_text}", size="sm")
                                example_btns.append((b, example_text))

                        gr.HTML("<hr class='ws-divider'>")

                        # 질문 입력
                        gr.HTML("<p class='ws-label'>질문 입력</p>")
                        query_input = gr.Textbox(
                            label="",
                            placeholder="종목명, 티커, 분석 요청을 입력하세요...",
                            lines=3, value=EXAMPLE_QUERIES[0], show_label=False,
                        )
                        with gr.Row():
                            run_btn   = gr.Button("🔍  질문하기", variant="primary",
                                                   scale=3, elem_id="run-btn")
                            clear_btn = gr.Button("초기화", scale=1, elem_id="clear-btn")

                    # ── 우: 단일 출력 패널 ──────────────────
                    with gr.Column(scale=1, min_width=300, elem_id="output-col"):
                        output_panel = gr.HTML(
                            value=IDLE_PANEL,
                            show_label=False,
                        )
                        timer = gr.Markdown(value="", visible=False)  # 내부용 더미

                # 하단 JSON (전체 너비)
                with gr.Row(elem_id="result-json-wrap"):
                    with gr.Column():
                        gr.HTML("<p class='ws-label'>📄 원본 데이터 (JSON)</p>")
                        meta = gr.Code(
                            label="", language="json",
                            elem_id="meta-box", show_label=False,
                        )

                gr.HTML(AUTO_SCROLL_JS, visible=False)

                # ── 이벤트 ──
                def on_run(q, persona, ep):
                    for panel, t, rj in stream_analyze(q, persona, ep):
                        yield panel, t, rj

                run_btn.click(
                    fn=on_run,
                    inputs=[query_input, persona_dd, endpoint_state],
                    outputs=[output_panel, timer, meta],
                )
                query_input.submit(
                    fn=on_run,
                    inputs=[query_input, persona_dd, endpoint_state],
                    outputs=[output_panel, timer, meta],
                )
                clear_btn.click(
                    fn=lambda: (IDLE_PANEL, "", ""),
                    outputs=[output_panel, timer, meta],
                )
                refresh_btn.click(
                    fn=lambda: gr.update(choices=load_persona_names(), value="없음"),
                    outputs=[persona_dd],
                )

                def on_persona_change(name):
                    info = load_persona_summary(name)
                    return gr.update(value=info)

                persona_dd.change(
                    fn=on_persona_change, inputs=[persona_dd], outputs=[persona_summary])

                # 예시 버튼 클릭 → query_input 에 채우기
                for _b, _text in example_btns:
                    _b.click(fn=lambda t=_text: t, outputs=[query_input])

            # ════════════════════════════════════════════
            # TAB 2 : 페르소나 만들기
            # ════════════════════════════════════════════
            with gr.Tab("🧑‍💼 페르소나 만들기"):
                gr.HTML("<p class='ws-label'>금융 인물 이름이나 설명을 입력하면 AI가 투자 철학·분석 스타일을 자동으로 구성합니다.</p>")
                with gr.Row(equal_height=False):
                    with gr.Column(scale=3, min_width=300):
                        persona_input = gr.Textbox(
                            label="인물 정보",
                            placeholder="예: 워렌 버핏, JP모건, 가타야마 아키라 ...", lines=3)
                        persona_gen_btn = gr.Button("✨  페르소나 생성", variant="primary")
                    with gr.Column(scale=1, min_width=140):
                        gr.HTML("<p class='ws-label'>예시 인물</p>")
                        for ep in ["워렌 버핏", "JP모건", "가타야마 아키라"]:
                            gr.Button(ep, size="sm").click(fn=lambda x=ep: x, outputs=[persona_input])

                persona_result = gr.Markdown(value="", label="생성 결과",
                                             elem_id="persona-result-wrapper")
                persona_timer  = gr.Markdown(value="", elem_id="timer-row")
                with gr.Accordion("📄 페르소나 JSON", open=False):
                    persona_json = gr.Code(label="", language="json",
                                          elem_id="meta-box", show_label=False)

                persona_gen_btn.click(
                    fn=generate_persona_stream,
                    inputs=[persona_input, endpoint_state],
                    outputs=[persona_result, persona_json, persona_timer],
                )

            # ════════════════════════════════════════════
            # TAB 3 : 투자자 프로필
            # ════════════════════════════════════════════
            with gr.Tab("👤 투자자 프로필"):
                with gr.Row():
                    with gr.Column(scale=1, min_width=180):
                        gr.HTML("<p class='ws-label'>투자자 선택</p>")
                        with gr.Row():
                            profile_dd = gr.Dropdown(
                                label="", choices=load_persona_names(), value="없음",
                                interactive=True, scale=5, show_label=False)
                            profile_refresh = gr.Button("↺", size="sm", scale=1,
                                                        min_width=34, elem_id="refresh-btn")
                    with gr.Column(scale=3): pass

                profile_card = gr.HTML(
                    value='<p class="pf-empty">왼쪽에서 투자자를 선택하세요.</p>',
                    elem_id="profile-wrapper")

                profile_dd.change(fn=build_profile_html_with_image, inputs=[profile_dd], outputs=[profile_card])
                profile_refresh.click(
                    fn=lambda: gr.update(choices=load_persona_names(), value="없음"),
                    outputs=[profile_dd])

    return demo


# ─────────────────────────────────────────────────────────────
# 진입점
# ─────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Wallstreet-AI Gradio UI")
    parser.add_argument("--api-url",     type=str, default=DEFAULT_ENDPOINT)
    parser.add_argument("--share",       action="store_true")
    parser.add_argument("--server-name", type=str, default="0.0.0.0")
    parser.add_argument("--port",        type=int, default=7860)
    args = parser.parse_args()

    print(f"FastAPI : {args.api_url}")
    print(f"Gradio  : http://{args.server_name}:{args.port}")

    app = create_app(args.api_url)
    app.queue(default_concurrency_limit=8, max_size=64)
    app.launch(share=args.share, server_name=args.server_name,
               server_port=args.port, debug=True)


if __name__ == "__main__":
    main()