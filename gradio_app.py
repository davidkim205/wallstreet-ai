import argparse
import base64
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

LOCAL_IMAGE_DIR = Path(os.environ.get("LOCAL_IMAGE_DIR", Path(__file__).parent / "persona_images"))
DEFAULT_IMAGE_FILENAME = "default.png"

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
    summary: str
    financial_mindset: str
    data_analysis_approach: str
    response_style: str
    key_principles: list[str]
    famous_quotes: list[str] | None = None
    image_path: str | None = None
    


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
                    if "background" in data and "summary" not in data:
                        data["summary"] = data["background"]
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
            summary = (p.summary[:80] + "…") if len(p.summary) > 80 else p.summary
            return f"**{p.full_name}**\n\n{summary}"
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

def build_profile_html(p: PersonaLine, img_html: str = ""):
    if img_html:
        avatar = f'<div class="pf-avatar">{img_html}</div>'
    else:
        bg, fg = _avatar_color(p.full_name)
        svg = (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100" viewBox="0 0 100 100">'
            f'<circle cx="50" cy="50" r="50" fill="{bg}"/>'
            f'<ellipse cx="50" cy="38" rx="16" ry="17" fill="{fg}66"/>'
            f'<ellipse cx="50" cy="76" rx="26" ry="18" fill="{fg}55"/>'
            f'</svg>'
        )
        avatar = f'<div class="pf-avatar">{svg}</div>'

    def section(title, content, extra_class=""):
        cls = f'pf-section {extra_class}'.strip()
        return f'<div class="{cls}"><p class="pf-section-title">{title}</p><div class="pf-section-body">{content}</div></div>'

    principles_html = (
        '<ul class="pf-list">'
        + "".join(f'<li>{_safe(x)}</li>' for x in (p.key_principles or []))
        + '</ul>'
    ) if p.key_principles else ""

    quotes_html = (
        '<div class="pf-quotes">'
        + "".join(f'<blockquote class="pf-quote">&#8220;{_safe(q)}&#8221;</blockquote>' for q in (p.famous_quotes or []))
        + '</div>'
    ) if p.famous_quotes else ""

    sections = f"""
<div class="pf-grid">
    <div class="pf-left">
        {section("💡 Financial Mindset", f'<p class="pf-text">{_safe(p.financial_mindset)}</p>', "pf-left-section")}
        {section("📊 Data Analysis Approach", f'<p class="pf-text">{_safe(p.data_analysis_approach)}</p>', "pf-left-section")}
        {section("🗣 Response Style", f'<p class="pf-text">{_safe(p.response_style)}</p>', "pf-left-section")}
    </div>
    <div class="pf-right">
        {section("📌 Key Principles", principles_html, "pf-right-section") if principles_html else ""}
    </div>
</div>

<div class="pf-bottom">
    {section("💬 Famous Quotes", quotes_html, "full") if quotes_html else ""}
</div>
"""

    return f"""<div class="pf-card">
  <div class="pf-banner"><div class="pf-banner-pattern"></div></div>
  <div class="pf-identity">
    {avatar}
    <div class="pf-identity-info">
      <h2 class="pf-name">{_safe(p.full_name)}</h2>
    </div>
  </div>
  <div class="pf-body">
    {f'<p class="pf-summary">{_safe(p.summary)}</p>' if p.summary else ''}
    <div class="pf-sections">{sections}</div>
  </div>
</div>"""


# ─────────────────────────────────────────────────────────────
# 로컬 인물 이미지 — persona.jsonl의 image_path만 사용
# ─────────────────────────────────────────────────────────────
def _load_local_image_b64(image_path: str) -> str:
    def _to_b64(path: Path) -> str:
        with open(path, "rb") as f:
            data = base64.b64encode(f.read()).decode()
        suffix = path.suffix.lower().lstrip(".")
        mime = "jpeg" if suffix in ("jpg", "jpeg") else suffix
        return f"data:image/{mime};base64,{data}"

    if image_path:
        candidate = Path(image_path)  
        if candidate.exists():
            return _to_b64(candidate)

    # 2순위: default.png 폴백
    default = LOCAL_IMAGE_DIR / DEFAULT_IMAGE_FILENAME
    if default.exists():
        return _to_b64(default)

    return ""


def build_profile_html_with_image(name: str) -> str:
    if not name or name == "없음":
        return '<div class="pf-placeholder"><span>👤</span><p>Select an Investor Persona</p></div>'

    persona = None
    for p in _parse_personas():
        if p.name.strip() == name:
            persona = p
            break
    if persona is None:
        return '<div class="pf-placeholder"><span>🔍</span><p>Unable to find the selected persona</p></div>'

    # persona.jsonl의 image_path만 사용
    data_url = _load_local_image_b64(persona.image_path or "")
    img_html = (
        f'<img src="{data_url}" style="width:100px;height:100px;object-fit:cover;display:block;">'
    ) if data_url else ""

    return build_profile_html(persona, img_html=img_html)


# ─────────────────────────────────────────────────────────────
# 스트림 분석 (핵심 로직)
# ─────────────────────────────────────────────────────────────
def _make_log_html(log_lines):
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
        '<div class="panel-header"><span class="panel-title">Progress</span></div>'
        '<div id="log-scroll">' + inner + '</div>'
        '</div>'
    )


def _wrap_answer(log_html, md_html, timer_str):
    log_section = (
        '<details class="result-log-section">'
        '<summary class="result-log-header">Progress <span class="log-toggle-hint">Click to show/hide</span></summary>'
        '<div class="result-log-body">' + log_html + '</div>'
        '</details>'
    ) if log_html else ''

    answer_section = (
        '<div class="result-answer-section">'
        '<div class="result-answer-header">'
        '<span class="result-answer-badge">&#128203; Final results</span>'
        f'<span class="panel-timer">{html_lib.escape(timer_str)}</span>'
        '</div>'
        '<div id="answer-scroll" class="md-body">' + md_html + '</div>'
        '</div>'
    )

    return (
        '<div id="output-panel" class="phase-answer">'
        '<div class="panel-header">'
        '<span class="panel-title">Analysis results</span>'
        f'<span class="panel-timer">{html_lib.escape(timer_str)}</span>'
        '</div>'
        '<div id="answer-scroll-wrap">'
        + log_section
        + answer_section
        + '</div>'
        '</div>'
    )


def _md_to_html(text):
    import re as _re

    _URL_RE = _re.compile(r'https?://[^\s,，、\)）\]】]+')

    def _url_badge(url: str) -> str:
        clean = url.rstrip('.,;:')
        m = _re.match(r'https?://(?:www\.)?([^/\s]+)', clean)
        label = m.group(1) if m else clean
        esc = html_lib.escape(clean)
        return f'<a href="{esc}" target="_blank" class="md-link">🔗 {html_lib.escape(label)}</a>'

    def _clean_source_line(s: str) -> str:
        s = _re.sub(r'\s*[—–-]{1,2}\s*(https?://)', r' \1', s)
        return s

    def process_inline(s: str) -> str:
        s = _clean_source_line(s)
        parts = []
        last = 0
        for m in _URL_RE.finditer(s):
            before = s[last:m.start()]
            before = html_lib.escape(before)
            before = _re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', before)
            before = _re.sub(r'(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)', r'<em>\1</em>', before)
            before = _re.sub(r'`(.+?)`', r'<code>\1</code>', before)
            parts.append(before)
            parts.append(_url_badge(m.group(0)))
            last = m.end()
        tail = html_lib.escape(s[last:])
        tail = _re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', tail)
        tail = _re.sub(r'(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)', r'<em>\1</em>', tail)
        tail = _re.sub(r'`(.+?)`', r'<code>\1</code>', tail)
        parts.append(tail)
        return ''.join(parts)

    BULLET_RE = _re.compile(
        r'^([ \t]*)'
        r'(\d+[.)]\s+|[-•·○◦▸▹*]\s+)'
        r'(.+)$'
    )
    HEADER_RE = _re.compile(r'^(#{1,4})\s+(.+)$')

    def indent_level(spaces: str) -> int:
        n = spaces.count('\t') * 4 + spaces.count(' ')
        return n // 2

    lines = text.split('\n')
    out: list[str] = []
    list_stack: list[tuple[int, str]] = []
    para_lines: list[str] = []

    def flush_para():
        if para_lines:
            out.append('<p>' + process_inline(' '.join(para_lines)) + '</p>')
            para_lines.clear()

    def open_list(depth: int, tag: str):
        while list_stack and list_stack[-1][0] > depth:
            _, t = list_stack.pop()
            out.append(f'</{t}>')
        if list_stack and list_stack[-1][0] == depth:
            if list_stack[-1][1] != tag:
                _, t = list_stack.pop()
                out.append(f'</{t}>')
                out.append(f'<{tag}>')
                list_stack.append((depth, tag))
        else:
            out.append(f'<{tag}>')
            list_stack.append((depth, tag))

    def close_all_lists():
        while list_stack:
            _, t = list_stack.pop()
            out.append(f'</{t}>')

    for line in lines:
        stripped = line.strip()

        if not stripped:
            flush_para()
            close_all_lists()
            continue

        mh = HEADER_RE.match(stripped)
        if mh:
            flush_para(); close_all_lists()
            level = min(len(mh.group(1)), 4)
            out.append(f'<h{level}>{process_inline(mh.group(2))}</h{level}>')
            continue

        if _URL_RE.fullmatch(stripped):
            flush_para()
            badge = _url_badge(stripped)
            if list_stack:
                out.append(f'<li class="md-link-item">{badge}</li>')
            else:
                out.append(f'<p class="md-link-p">{badge}</p>')
            continue

        mb = BULLET_RE.match(line)
        if mb:
            flush_para()
            spaces  = mb.group(1)
            marker  = mb.group(2)
            content = mb.group(3).strip()
            depth   = indent_level(spaces)
            is_ordered = bool(_re.match(r'\d+', marker.strip()))
            tag = 'ol' if is_ordered else 'ul'
            open_list(depth, tag)

            if is_ordered:
                num = int(_re.match(r'(\d+)', marker.strip()).group(1))
                out.append(f'<li value="{num}">{process_inline(content)}</li>')
            else:
                out.append(f'<li>{process_inline(content)}</li>')
            continue

        close_all_lists()
        para_lines.append(stripped)

    flush_para()
    close_all_lists()
    return '\n'.join(out)


IDLE_PANEL = (
    '<div id="output-panel" class="phase-idle">'
    '<div class="idle-msg">🔍 Enter your question on the left and click Ask a Question..</div>'
    '</div>'
)


def stream_analyze(query, persona_name, endpoint):
    query = (query or "").strip()
    endpoint = (endpoint or "").strip()
    persona_name = (persona_name or "").strip()

    if not query:
        yield (_wrap_log('<div class="log-error">❌ Please enter your question.</div>'), "", "")
        return
    if not endpoint:
        yield (_wrap_log('<div class="log-error">❌ Please check the API endpoint.</div>'), "", "")
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
            panel = _wrap_answer(frozen_log, _md_to_html(text_acc), t)
        else:
            panel = _wrap_log(_make_log_html(log_lines))
        yield (panel, t, result_json)

        if worker_done:
            break

    t = timer_text(elapsed())
    if first_delta:
        panel = _wrap_answer(frozen_log, _md_to_html(text_acc), t)
    else:
        panel = _wrap_log(_make_log_html(log_lines))
    yield (panel, t, result_json)


# ─────────────────────────────────────────────────────────────
# 페르소나 생성 스트림
# ─────────────────────────────────────────────────────────────
def generate_persona_stream(info, endpoint):
    if not info or not info.strip():
        yield "Please enter the person information.", "{}", timer_text("0.0초")
        return

    persona_ep = endpoint.rstrip("/").rsplit("/", 1)[0] + "/persona/"
    elapsed = _make_elapsed()
    eq: Queue = Queue()

    def reader():
        try:
            body = {"info": info.strip(), "stream": True}
            with requests.post(persona_ep, json=body,
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
            eq.put(("exception", f"연결 실패: {persona_ep}"))
        except requests.exceptions.Timeout:
            eq.put(("exception", "요청 시간 초과"))
        except requests.RequestException as e:
            eq.put(("exception", f"요청 실패: {e}"))
        finally:
            eq.put(("worker_done", None))

    Thread(target=reader, daemon=True).start()

    log_lines = []
    result_data = None
    worker_done = False

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
                    msg = payload.get("message", "")
                    if msg:
                        log_lines.append(("status", msg))

                elif et == "result":
                    result_data = payload
                    log_lines.append(("done", "페르소나 생성 완료"))

                elif et == "error":
                    msg = payload.get("message", "오류 발생")
                    log_lines.append(("error", msg))

                elif et == "done":
                    pass

            elif kind == "exception":
                log_lines.append(("error", str(payload)))

            elif kind == "worker_done":
                worker_done = True

        t = timer_text(elapsed())
        if result_data:
            data = result_data
            existing_names = {p.name.strip() for p in _parse_personas()}
            if data.get("name") and data["name"].strip() not in existing_names:
                # API 응답의 image_path를 그대로 신뢰해 저장
                # (없으면 None으로 저장되며, 추후 Edit Profile에서 업로드 가능)
                with PERSONA_FILE.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(data, ensure_ascii=False) + "\n")
                global _persona_cache_mtime
                _persona_cache_mtime = 0.0
            md = "\n\n".join([
                f"**이름**: {data.get('name','')}",
                f"**배경**: {data.get('summary','')}",
                f"**금융 사고 방식**: {data.get('financial_mindset','')}",
                f"**데이터 분석 방식**: {data.get('data_analysis_approach','')}",
                f"**답변 스타일**: {data.get('response_style','')}",
                f"**핵심 원칙**: {', '.join(data.get('key_principles',[]))}",
            ])
            if data.get("famous_quotes"):
                md += f"\n\n**어록**: {' / '.join(data['famous_quotes'])}"
            yield md, json.dumps(data, ensure_ascii=False, indent=2), t
            break
        else:
            panel = _wrap_log(_make_log_html(log_lines))
            yield panel, "{}", t

        if worker_done and not result_data:
            panel = _wrap_log(_make_log_html(log_lines))
            yield panel, "{}", t
            break


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

    --panel-height: 680px;

    --pf-left-h: 130px;
    --pf-right-h: calc(var(--pf-left-h) * 3 + 12px * 2);
}

.gradio-container,
.gradio-container :is(h1,h2,h3,h4,h5,h6,p,span,div,label,button,input,textarea,select) {
    font-family: "IBM Plex Sans KR","Noto Sans KR","Source Sans 3",sans-serif !important;
    letter-spacing: 0.005em;
}
.gradio-container {
    background: radial-gradient(circle at top left,#edf9f6 0%,#f8fbfc 35%,#fdfefe 100%) !important;
}

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

#qa-row { align-items: stretch !important; }

#input-col {
    background: var(--ws-surface);
    border: 1px solid var(--ws-border) !important;
    border-radius: 14px !important;
    padding: 18px 20px !important;
    box-shadow: 0 2px 12px rgba(16,24,40,.04);
    display: flex; flex-direction: column; gap: 10px;
    height: auto !important;
    min-height: var(--panel-height);
    overflow-y: visible !important;
    box-sizing: border-box;
}

.ws-label {
    font-size: 10px; font-weight: 700; text-transform: uppercase;
    letter-spacing: .08em; color: var(--ws-muted); margin-bottom: 4px;
}
.ws-divider { border:none; border-top:1px solid var(--ws-border); margin:10px 0; }

#persona-summary {
    background: linear-gradient(180deg,#f9fefd 0%,#f3fbf9 100%) !important;
    border: 1px solid #cde8e3 !important; border-radius: 10px !important;
    padding: 10px 14px !important; font-size: 13px !important;
    color: var(--ws-text) !important;
}
#persona-summary > .wrap,#persona-summary > div.prose { padding:0!important;border:none!important;box-shadow:none!important; }

#example-grid { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 5px; }
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
}
#clear-btn:hover { border-color:var(--ws-accent)!important; color:var(--ws-accent)!important; }

#refresh-btn {
    min-width:34px!important; padding:0 8px!important;
    background:var(--ws-code-bg)!important; border:1px solid var(--ws-border)!important;
    color:var(--ws-muted)!important; border-radius:8px!important; font-size:15px!important;
}
#refresh-btn:hover { color:var(--ws-accent)!important; border-color:var(--ws-accent)!important; background:var(--ws-green-bg)!important; }

#output-col {
    border: 1px solid var(--ws-border) !important;
    border-radius: 14px !important;
    overflow: hidden !important;
    background: var(--ws-surface);
    box-shadow: 0 2px 12px rgba(16,24,40,.04);
    height: var(--panel-height) !important;
    display: flex !important; flex-direction: column !important;
    box-sizing: border-box;
    min-height: var(--panel-height) !important;
    height: auto !important;
}
#output-col > .wrap, #output-col > div {
    padding: 0 !important; margin: 0 !important;
    border: none !important; box-shadow: none !important;
    height: 100% !important;
    display: flex !important; flex-direction: column !important;
    overflow: hidden !important; min-height: 0 !important;
}

#output-panel { display:flex; flex-direction:column; height:100%; overflow:hidden; min-height:0; }

.panel-header {
    display:flex; align-items:center; justify-content:space-between;
    padding:10px 16px; background:#f7faf9;
    border-bottom:1px solid var(--ws-border); flex-shrink:0;
}
.panel-title { font-size:11px; font-weight:700; text-transform:uppercase; letter-spacing:.07em; color:var(--ws-muted); }
.panel-timer {
    font-size:11px; font-weight:600; color:var(--ws-accent);
    background:var(--ws-green-bg); border:1px solid var(--ws-green-border);
    padding:2px 10px; border-radius:999px;
}
.idle-msg { display:flex; align-items:center; justify-content:center; flex:1; color:var(--ws-muted); font-size:13px; }

#log-scroll { flex:1; overflow-y:auto; padding:14px 18px; font-size:12px; line-height:1.7; }
.log-status { display:flex; align-items:flex-start; gap:7px; padding:3px 0; color:var(--ws-text); }
.log-status span { color:var(--ws-text); }
.log-stdout pre {
    margin:3px 0; padding:4px 10px;
    background:#f1f8f7; border-left:3px solid var(--ws-accent2); border-radius:0 5px 5px 0;
    font-family:"JetBrains Mono","IBM Plex Mono",monospace !important;
    font-size:11px !important; color:var(--ws-muted); white-space:pre-wrap; word-break:break-all;
}
.log-done  { color:var(--ws-accent); font-weight:700; padding:4px 0; }
.log-error { color:#e53e3e; padding:4px 0; }

#answer-scroll-wrap { display:flex; flex-direction:column; height:calc(var(--panel-height) - 42px); overflow:hidden; }
.result-log-section { border-bottom:2px solid var(--ws-border); background:#f7faf9; flex-shrink:0; max-height:140px; overflow:hidden; }
.result-log-section[open] { overflow-y:auto; }
.result-log-header {
    font-size:10px; font-weight:700; text-transform:uppercase; letter-spacing:.07em; color:var(--ws-muted);
    padding:7px 18px; cursor:pointer; display:flex; align-items:center; justify-content:space-between;
    list-style:none; user-select:none;
}
.result-log-header::-webkit-details-marker { display:none; }
.result-log-header::after { content:"▲"; font-size:9px; color:var(--ws-muted); transition:transform 0.2s; }
.result-log-section:not([open]) .result-log-header::after { transform:rotate(180deg); }
.result-log-section:not([open]) ~ .result-answer-section #answer-scroll { height:calc(var(--panel-height) - 78px) !important; }
.log-toggle-hint { font-size:9px; font-weight:400; color:#9bb0ac; margin-left:6px; text-transform:none; letter-spacing:0; }
.result-log-body { padding:0 18px 10px; font-size:12px; line-height:1.7; }
.result-answer-section { padding:0; flex:1; display:flex; flex-direction:column; min-height:0; overflow:hidden; }
.result-answer-header {
    display:flex; align-items:center; justify-content:space-between;
    padding:12px 18px 8px; border-bottom:1px solid var(--ws-border);
    background:linear-gradient(135deg,#f0fdfa 0%,#e8faf7 100%); flex-shrink:0;
}
.result-answer-badge { font-size:13px; font-weight:700; color:var(--ws-accent); letter-spacing:.01em; }
#answer-scroll { padding:18px 22px; overflow-y:auto !important; height:calc(var(--panel-height) - 118px) !important; box-sizing:border-box; }

.md-body { color:var(--ws-text); line-height:1.75; font-size:14.5px; }
.md-body h1,.md-body h2,.md-body h3,.md-body h4 { color:#0b3b39; margin:.85em 0 .35em; }
.md-body h2 { font-size:16px; border-bottom:1px solid var(--ws-border); padding-bottom:5px; }
.md-body h3 { font-size:14px; color:var(--ws-accent); }
.md-body h4 { font-size:13px; }
.md-body p  { margin:.4em 0; }
.md-body strong { font-weight:700; }
.md-body em     { font-style:italic; }
.md-body a.md-link {
    display:inline-flex; align-items:center; gap:4px;
    color:var(--ws-accent); font-size:12.5px; font-weight:500; text-decoration:none;
    background:var(--ws-green-bg); border:1px solid var(--ws-green-border);
    border-radius:6px; padding:2px 9px; transition:background .15s,border-color .15s;
}
.md-body a.md-link:hover { background:#ccfbf1; border-color:var(--ws-accent); }
.md-body li.md-link-item { list-style:none; margin:3px 0; }
.md-body p.md-link-p { margin:3px 0; }
.md-body ul,.md-body ol { margin:.4em 0; padding-left:1.5em; }
.md-body ul ul,.md-body ol ol,.md-body ul ol,.md-body ol ul { margin:.2em 0; padding-left:1.4em; }
.md-body li { margin:.2em 0; }
.md-body li > ul,.md-body li > ol { margin-top:.15em; }
.md-body code { background:var(--ws-code-bg); color:#0b3b39; border:1px solid #d9e2ec; border-radius:5px; padding:.1em .35em; font-size:.91em; font-family:"JetBrains Mono","IBM Plex Mono",monospace; }
.md-body pre { background:#0f172a; color:#e2e8f0; border-radius:10px; border:1px solid #1e293b; padding:.85em 1em; overflow-x:auto; margin:.7em 0; }
.md-body pre code { background:transparent; border:none; color:inherit; padding:0; }
.md-body blockquote { margin:.8em 0; padding:.6em .9em; border-left:4px solid var(--ws-accent2); background:var(--ws-green-bg); color:#115e59; border-radius:0 8px 8px 0; }
.md-body table { width:100%; border-collapse:collapse; margin:.7em 0; }
.md-body th { background:#eef6f4; color:#0f3f3b; font-weight:600; font-size:12px; text-transform:uppercase; letter-spacing:.04em; padding:7px 10px; border:1px solid var(--ws-border); }
.md-body td { border:1px solid var(--ws-border); padding:6px 10px; vertical-align:top; }
.md-body tr:hover td { background:#f9fefd; }

#timer-row { display:none !important; }

#result-json-wrap { border-top:2px solid var(--ws-border); background:var(--ws-surface); }
#result-json-wrap .accordion-header { padding:10px 16px !important; }
#meta-box { max-height:220px; overflow-y:auto; background:var(--ws-surface)!important; border:none!important; }
#meta-box code,#meta-box pre { font-family:"JetBrains Mono",monospace!important; font-size:11.5px!important; color:var(--ws-muted)!important; background:transparent!important; }

.ws-loading { position:relative; overflow:hidden; border:1px solid #cde8e3; border-radius:12px; background:linear-gradient(180deg,#f9fefd 0%,#f3fbf9 100%); padding:14px 16px; }
.ws-loading-title { color:var(--ws-accent); font-weight:700; margin-bottom:6px; }
.ws-loading-msg   { color:#365055; font-size:13px; }
.shimmer::after { content:""; position:absolute; top:0; left:-140%; width:80%; height:100%; background:linear-gradient(100deg,rgba(255,255,255,0) 0%,rgba(255,255,255,.55) 45%,rgba(255,255,255,0) 100%); animation:ws-shimmer 1.6s ease-in-out infinite; }
@keyframes ws-shimmer { 0%{left:-140%} 100%{left:150%} }

#persona-input-col {
    background: var(--ws-surface);
    border: 1px solid var(--ws-border) !important;
    border-radius: 14px !important;
    padding: 18px 20px !important;
    box-shadow: 0 2px 12px rgba(16,24,40,.04);
    display: flex;
    flex-direction: column;
    min-height: 260px;
}
#persona-example-col {
    background: var(--ws-surface);
    border: 1px solid var(--ws-border) !important;
    border-radius: 14px !important;
    padding: 14px 16px !important;
    box-shadow: 0 2px 12px rgba(16,24,40,.04);
    display: flex;
    flex-direction: column;
    min-height: 260px;
}
.persona-example-btn { width:100%!important; background:var(--ws-code-bg)!important; border:1px solid var(--ws-border)!important; border-radius:8px!important; color:var(--ws-text)!important; font-size:12px!important; margin-bottom:4px!important; transition:all .18s!important; }
.persona-example-btn:hover { background:var(--ws-green-bg)!important; border-color:var(--ws-accent2)!important; color:var(--ws-accent)!important; }
#persona-gen-btn { background:linear-gradient(135deg,#0f766e 0%,#14b8a6 100%)!important; border:none!important; color:#fff!important; font-weight:700!important; font-size:13px!important; border-radius:9px!important; min-height:42px!important; width:100%!important; margin-top:10px!important; }
#persona-result-wrapper { min-height:180px; max-height:50vh; overflow-y:auto!important; border:1px solid var(--ws-border)!important; border-radius:14px!important; background:var(--ws-surface)!important; padding:20px 24px!important; color:var(--ws-text)!important; font-size:14px!important; line-height:1.72!important; margin-top:12px!important; }

#profile-select-bar {
    background:var(--ws-surface); border:1px solid var(--ws-border); border-radius:12px;
    padding:10px 14px!important; margin-bottom:14px!important; align-items:center!important;
    box-shadow:0 1px 6px rgba(16,24,40,.04);
}
#profile-dd { border-radius:8px!important; }
#profile-wrapper { margin-bottom:14px; }

.pf-placeholder {
    display:flex; flex-direction:column; align-items:center; justify-content:center;
    height:220px; gap:12px; border:2px dashed var(--ws-border); border-radius:16px; color:var(--ws-muted);
}
.pf-placeholder span { font-size:48px; opacity:.35; }
.pf-placeholder p { font-size:14px; margin:0; }

.pf-card { background:var(--ws-surface); border:1px solid var(--ws-border); border-radius:16px; overflow:visible; box-shadow:0 4px 24px rgba(16,24,40,.07); position:relative; }
.pf-banner { height:100px; background:linear-gradient(135deg,#0f766e 0%,#14b8a6 55%,#5eead4 100%); position:relative; border-radius:16px 16px 0 0; overflow:hidden; }
.pf-banner-pattern { position:absolute; inset:0; opacity:.15; background-image:radial-gradient(circle,#fff 1px,transparent 1px); background-size:18px 18px; }
.pf-identity { display:flex; align-items:flex-start; gap:20px; padding:0 28px; margin-top:-54px; margin-bottom:12px; position:relative; z-index:2; }
.pf-avatar { width:100px; height:100px; border-radius:50%; overflow:hidden; border:4px solid var(--ws-surface); box-shadow:0 4px 16px rgba(15,118,110,.25); flex-shrink:0; background:#e2f0ee; }
.pf-avatar img,.pf-avatar svg { width:100%; height:100%; object-fit:cover; display:block; }
.pf-identity-info { display: flex; flex-direction: column; justify-content: flex-end; min-height: 100px; padding-bottom:6px; flex:1; min-width:0; }
.pf-name { font-size:24px!important; font-weight:800!important; color:#0b3b39!important; margin:0 0 2px!important; line-height:1.2!important; }
.pf-subtitle { font-size:13px!important; color:var(--ws-accent)!important; font-weight:600!important; margin:0!important; }
.pf-body { padding:0 28px 24px; }
.pf-summary { font-size:14px!important; color:var(--ws-muted)!important; line-height:1.65!important; margin:0 0 20px!important; padding-bottom:18px!important; border-bottom:1px solid #eef4f2!important; }
.pf-tags { display:flex; flex-wrap:wrap; gap:8px; margin-bottom:20px; }
.pf-tag { display:inline-flex; align-items:center; gap:5px; padding:4px 12px; border-radius:999px; background:var(--ws-green-bg); border:1px solid var(--ws-green-border); font-size:12px; color:#0d5c54; font-weight:600; }
.pf-tag-icon { font-size:13px; }

.pf-grid {
    display: grid;
    grid-template-columns: 2fr 1fr;
    gap: 16px;
    align-items: start;
}
.pf-left {
    display: flex;
    flex-direction: column;
    gap: 12px;
}
.pf-left-section {
    height: var(--pf-left-h) !important;
    min-height: var(--pf-left-h) !important;
    max-height: var(--pf-left-h) !important;
    overflow-y: auto;
    box-sizing: border-box;
}
.pf-right {
    display: flex;
    flex-direction: column;
}
.pf-right-section {
    height: var(--pf-right-h) !important;
    min-height: var(--pf-right-h) !important;
    max-height: var(--pf-right-h) !important;
    overflow-y: auto;
    box-sizing: border-box;
}
.pf-left-section::-webkit-scrollbar,
.pf-right-section::-webkit-scrollbar { width: 3px; }
.pf-left-section::-webkit-scrollbar-track,
.pf-right-section::-webkit-scrollbar-track { background: transparent; }
.pf-left-section::-webkit-scrollbar-thumb,
.pf-right-section::-webkit-scrollbar-thumb { background: var(--ws-green-border); border-radius: 3px; }
.pf-left-section::-webkit-scrollbar-thumb:hover,
.pf-right-section::-webkit-scrollbar-thumb:hover { background: var(--ws-accent2); }

.pf-bottom { display:flex; flex-direction:column; gap:14px; margin-top:16px; }
.pf-sections { display:grid; grid-template-columns:1fr; gap:14px; }

.pf-section {
    background: var(--ws-code-bg);
    border: 1px solid var(--ws-border);
    border-radius: 12px;
    padding: 14px 16px;
    box-sizing: border-box;
}
.pf-section.full { width: 100%; }

.pf-section-title { font-size:10px!important; font-weight:700!important; text-transform:uppercase!important; letter-spacing:.08em!important; color:var(--ws-accent)!important; margin:0 0 8px!important; }
.pf-text { font-size:13.5px!important; color:var(--ws-text)!important; line-height:1.7!important; margin:0!important; }
.pf-list { margin:0!important; padding-left:1.1em!important; }
.pf-list li { font-size:13px!important; color:var(--ws-text)!important; line-height:1.6!important; margin:6px 0!important; }
.pf-trades { display:flex; flex-direction:column; gap:5px; }
.pf-trade-item { font-size:12.5px; color:var(--ws-text); background:var(--ws-surface); border-left:3px solid var(--ws-accent2); border-radius:0 6px 6px 0; padding:5px 10px; }
.pf-quotes { display:flex; flex-direction:column; gap:8px; }
.pf-quote { margin:0!important; padding:.55em .9em!important; border-left:3px solid var(--ws-accent2)!important; background:var(--ws-surface)!important; color:#115e59!important; border-radius:0 8px 8px 0; font-size:13px!important; font-style:italic; }

#edit-accordion { border:1px solid var(--ws-border)!important; border-radius:12px!important; background:var(--ws-surface)!important; overflow:hidden; }
#edit-accordion .label-wrap { padding:12px 16px!important; }
#edit-accordion label span { font-size:13px!important; font-weight:700!important; color:var(--ws-text)!important; }
#edit-image-upload .wrap { padding:6px 10px!important; min-height:40px!important; border-radius:8px!important; border:1px dashed var(--ws-border)!important; background:var(--ws-code-bg)!important; }
#edit-image-upload label { font-size:11px!important; color:var(--ws-muted)!important; }

::-webkit-scrollbar { width:5px; height:5px; }
::-webkit-scrollbar-track { background:transparent; }
::-webkit-scrollbar-thumb { background:var(--ws-border); border-radius:3px; }
::-webkit-scrollbar-thumb:hover { background:#aec5c1; }
body:has(.options:not(.hide)) { overflow:hidden!important; }

@media (max-width: 768px) {
    #example-grid { grid-template-columns:1fr 1fr!important; }
    .pf-identity { flex-direction:column; align-items:flex-start; margin-top:-30px; }
    .pf-grid { grid-template-columns:1fr!important; }
    .pf-left-section { height:auto!important; min-height:100px!important; max-height:none!important; }
    .pf-right-section { height:auto!important; min-height:160px!important; max-height:none!important; }
    :root { --panel-height:500px; }
    #output-col { max-height:none!important; }
}

.edit-panel { background:var(--ws-surface); border:1px solid var(--ws-border); border-radius:14px; padding:18px 20px; box-shadow:0 2px 12px rgba(16,24,40,.04); }
.edit-panel label { font-size:12px!important; font-weight:600!important; color:var(--ws-muted)!important; }
"""

HEADER_HTML = """
<div id="ws-header">
  <h1>📈 Wallstreet AI</h1>
  <p>
    Financial analysis assistant combining legendary investor personas with
    prices, fundamentals, earnings, news, and technical indicators
  </p>
</div>
"""


def _persona_to_dict(p: PersonaLine) -> dict:
    return {k: v for k, v in p.model_dump().items() if v is not None}


def save_persona_edits(
    original_name,
    new_name,
    full_name,
    summary,
    financial_mindset,
    data_analysis_approach,
    response_style,
    key_principles_text,
    famous_quotes_text,
    image_path=""
) -> str:

    if not original_name or original_name == "없음":
        return "Select a persona to save."

    try:
        # raw dict로 읽어서 saved_at, query 등 extra 필드 보존
        raw_lines = []
        if PERSONA_FILE.exists():
            with PERSONA_FILE.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        raw_lines.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue

        updated = False
        new_lines = []

        for d in raw_lines:
            if d.get("name", "").strip() == original_name:
                # extra 필드(saved_at, query 등)는 그대로 두고 편집 필드만 덮어쓰기
                d["name"]                   = new_name.strip() or d.get("name", "")
                d["full_name"]              = full_name.strip() or d.get("full_name", "")
                d["summary"]                = summary.strip()
                d["financial_mindset"]      = financial_mindset.strip()
                d["data_analysis_approach"] = data_analysis_approach.strip()
                d["response_style"]         = response_style.strip()
                d["key_principles"]         = [x.strip() for x in key_principles_text.split("\n") if x.strip()]
                d["famous_quotes"]          = [x.strip() for x in famous_quotes_text.split("\n") if x.strip()] or None
                if image_path.strip():
                    d["image_path"]         = image_path.strip()
                new_lines.append(json.dumps(d, ensure_ascii=False))
                updated = True
            else:
                new_lines.append(json.dumps(d, ensure_ascii=False))

        if not updated:
            return "No corresponding persona found."

        PERSONA_FILE.write_text("\n".join(new_lines) + "\n", encoding="utf-8")

        global _persona_cache_mtime
        _persona_cache_mtime = 0.0

        return "✅ Save complete!"

    except Exception as e:
        return f"❌ Save fail: {e}"


def load_persona_for_edit(name: str):
    # UI의 edit_outputs 리스트(8개 항목)에 순서와 개수를 맞춥니다:
    # [edit_name, edit_full_name, edit_summary, edit_mindset, edit_approach, edit_style, edit_principles, edit_quotes]
    empty = ("", "", "", "", "", "", "", "","")
    if not name or name == "없음":
        return empty
    for p in _parse_personas():
        if p.name.strip() == name:
            return (
                p.name or "",
                p.full_name or "",
                p.summary or "",
                p.financial_mindset or "",
                p.data_analysis_approach or "",
                p.response_style or "",
                "\n".join(p.key_principles or []),
                "\n".join(p.famous_quotes or []),
                p.image_path or "",
            )
    return empty


# ─────────────────────────────────────────────────────────────
def create_app(default_endpoint):
    theme = gr.themes.Soft(primary_hue="emerald", secondary_hue="teal",
                           neutral_hue="slate", radius_size="lg")

    with gr.Blocks(title="Wallstreet AI", analytics_enabled=False) as demo:

        endpoint_state = gr.State(default_endpoint)

        gr.HTML(HEADER_HTML)

        with gr.Tabs():

            # ════════════════════════════════════════════
            # TAB 1 : 질문하기
            # ════════════════════════════════════════════
            with gr.Tab("💬 Ask a Question"):
                with gr.Row(equal_height=True, elem_id="qa-row"):

                    with gr.Column(scale=1, min_width=280, elem_id="input-col"):

                        gr.HTML("<p class='ws-label'>Persona</p>")
                        with gr.Row():
                            persona_dd = gr.Dropdown(
                                label="", choices=load_persona_names(),
                                value="없음", interactive=True,
                                scale=5, show_label=False,
                            )
                            refresh_btn = gr.Button("↺", size="sm", scale=1,
                                                    min_width=34, elem_id="refresh-btn")
                        persona_summary = gr.Markdown(value="", elem_id="persona-summary", visible=True)

                        gr.HTML("<hr class='ws-divider'>")

                        gr.HTML("<p class='ws-label'>Example Questions</p>")
                        with gr.Column(elem_id="example-grid"):
                            example_btns = []
                            for example_text in EXAMPLES_BY_TYPE.values():
                                b = gr.Button(example_text, size="sm")
                                example_btns.append((b, example_text))

                        gr.HTML("<hr class='ws-divider'>")

                        gr.HTML("<p class='ws-label'>Ask Question</p>")
                        query_input = gr.Textbox(
                            label="",
                            placeholder="종목명, 티커, 분석 요청을 입력하세요...",
                            lines=3, value=EXAMPLE_QUERIES[0], show_label=False,
                        )
                        with gr.Row():
                            run_btn   = gr.Button("🔍  Ask Question", variant="primary",
                                                   scale=3, elem_id="run-btn")
                            clear_btn = gr.Button("Reset", scale=1, elem_id="clear-btn")

                    with gr.Column(scale=1, min_width=300, elem_id="output-col"):
                        output_panel = gr.HTML(value=IDLE_PANEL, show_label=False)
                        timer = gr.Markdown(value="", visible=False)

                with gr.Row(elem_id="result-json-wrap"):
                    with gr.Column():
                        gr.HTML("<p class='ws-label'>📄 Original Data(JSON)</p>")
                        meta = gr.Code(label="", language="json", elem_id="meta-box", show_label=False)

                gr.HTML(AUTO_SCROLL_JS, visible=False)

                def on_run(q, persona, ep):
                    for panel, t, rj in stream_analyze(q, persona, ep):
                        yield panel, t, rj

                run_btn.click(fn=on_run, inputs=[query_input, persona_dd, endpoint_state], outputs=[output_panel, timer, meta])
                query_input.submit(fn=on_run, inputs=[query_input, persona_dd, endpoint_state], outputs=[output_panel, timer, meta])
                clear_btn.click(fn=lambda: (IDLE_PANEL, "", ""), outputs=[output_panel, timer, meta])
                refresh_btn.click(fn=lambda: gr.update(choices=load_persona_names(), value="없음"), outputs=[persona_dd])

                def on_persona_change(name):
                    return gr.update(value=load_persona_summary(name))

                persona_dd.change(fn=on_persona_change, inputs=[persona_dd], outputs=[persona_summary])

                for _b, _text in example_btns:
                    _b.click(fn=lambda t=_text: t, outputs=[query_input])

            # ════════════════════════════════════════════
            # TAB 2 : 페르소나 만들기
            # ════════════════════════════════════════════
            with gr.Tab("🧑‍💼 Create a Persona"):
                with gr.Row(equal_height=True):
                    with gr.Column(scale=3, min_width=320, elem_id="persona-input-col"):
                        gr.HTML("<p class='ws-label'>Enter Person Info</p>")
                        persona_input = gr.Textbox(
                            label="",
                            placeholder="ex: Warren Buffett, JP Morgan, Ray Dalio ...",
                            lines=4, show_label=False)
                        persona_gen_btn = gr.Button(
                            "✨  Build Persona", variant="primary",
                            elem_id="persona-gen-btn",
                        )

                    with gr.Column(scale=1, min_width=150, elem_id="persona-example-col"):
                        gr.HTML("<p class='ws-label'>Example Figures</p>")
                        PERSONA_EXAMPLES = [
                                ("Warren Buffett", "워렌 버핏"),
                                ("JP Morgan", "JP모건"),
                                ("Ray Dalio", "레이 달리오"),
                                ("Ken Griffin", "켄 그리핀"),
                                ("Jim Rogers", "짐 로저스"),
                        ]
                        for label, query in PERSONA_EXAMPLES:
                            gr.Button(label, size="sm", elem_classes=["persona-example-btn"]).click(
                                fn=lambda q=query: q,
                                outputs=[persona_input]
                            )
                persona_result = gr.Markdown(value="", label="생성 결과", elem_id="persona-result-wrapper")
                persona_timer  = gr.Markdown(value="", visible=False, elem_id="timer-row")
                with gr.Row(elem_id="result-json-wrap"):
                    with gr.Column():
                        gr.HTML("<p class='ws-label'>📄 Persona Data (JSON)</p>")
                        persona_json = gr.Code(label="", language="json", elem_id="meta-box", show_label=False)

                persona_gen_btn.click(
                    fn=generate_persona_stream,
                    inputs=[persona_input, endpoint_state],
                    outputs=[persona_result, persona_json, persona_timer],
                )

            # ════════════════════════════════════════════
            # TAB 3 : 투자자 프로필
            # ════════════════════════════════════════════
            with gr.Tab("👤 Investor Profile"):

                with gr.Row(elem_id="profile-select-bar"):
                    profile_dd = gr.Dropdown(
                        label="", choices=load_persona_names(), value="없음",
                        interactive=True, scale=6, show_label=False, elem_id="profile-dd",
                    )
                    profile_refresh = gr.Button("↺", size="sm", scale=0, min_width=40, elem_id="refresh-btn")

                profile_card = gr.HTML(
                    value='<div class="pf-placeholder"><span>👤</span><p>Select an Investor Persona</p></div>',
                    elem_id="profile-wrapper",
                )

                with gr.Accordion("✏️ Edit Profile", open=False, elem_id="edit-accordion"):
                    with gr.Column():
                        edit_name = gr.Textbox(label="Name", lines=1)
                        edit_full_name = gr.Textbox(label="Full Name", lines=1)
                        edit_summary = gr.Textbox(label="Summary", lines=3)
                        edit_mindset = gr.Textbox(label="Financial Mindset", lines=3)
                        edit_approach = gr.Textbox(label="Data Analysis Approach", lines=3)
                        edit_style = gr.Textbox(label="Response Style", lines=2)

                        edit_principles = gr.Textbox(label="Key Principles (one per line)", lines=3)
                        edit_quotes = gr.Textbox(label="Famous Quotes (one per line)", lines=3)

                        edit_image_path = gr.Textbox(
                        label="Image Path (absolute)",
                        placeholder="/home/ai/wallstreet-ai/persona/images/J.P._Morgan.png")

                save_btn = gr.Button("💾  Save", variant="primary")
                save_status = gr.Markdown(value="")

                edit_outputs = [
                    edit_name,edit_full_name, edit_summary, edit_mindset, edit_approach,
                    edit_style, edit_principles, edit_quotes,edit_image_path,
                ]
                edit_inputs = [
                    profile_dd, edit_name,edit_full_name, edit_summary, edit_mindset,
                    edit_approach, edit_style, edit_principles, edit_quotes,
                    edit_image_path,
                ]

                def on_profile_change(name):
                    card = build_profile_html_with_image(name)
                    vals = load_persona_for_edit(name)
                    return (card,) + tuple(vals)

                profile_dd.change(fn=on_profile_change, inputs=[profile_dd], outputs=[profile_card] + edit_outputs)
                profile_refresh.click(fn=lambda: gr.update(choices=load_persona_names(), value="없음"), outputs=[profile_dd])
                save_btn.click(fn=save_persona_edits, inputs=edit_inputs, outputs=[save_status]).then(
                    fn=build_profile_html_with_image, inputs=[profile_dd], outputs=[profile_card])

    return demo, theme


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

    app, theme = create_app(args.api_url)
    app.queue(default_concurrency_limit=8, max_size=64)
    app.launch(share=args.share, server_name=args.server_name,
               server_port=args.port, debug=True,
               theme=theme, css=CSS)


if __name__ == "__main__":
    main()
