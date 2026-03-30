import argparse
import html as html_lib
import json
import os
import time
from pathlib import Path
from queue import Empty, Queue
from threading import Thread

import gradio as gr
import requests
from pydantic import BaseModel, ValidationError

PERSONA_FILE = Path(os.environ.get("PERSONA_FILE", "persona.jsonl"))

EXAMPLE_QUERIES = [
    "AAPL의 최근 실적과 투자 포인트 요약해줘",
    "TSLA의 기술적 분석 리포트 작성",
    "삼성전자(005930.KS) SWOT 분석",
    "쿠팡 매출액 알려줘",
]


AUTO_SCROLL_SCRIPT = """
<script>
(function () {
  function setupAutoScroll() {
    const root = document.getElementById("answer-wrapper");
    if (!root) return false;

    const scrollToBottom = () => {
      root.scrollTop = root.scrollHeight;
    };

    scrollToBottom();

    const observer = new MutationObserver(scrollToBottom);
    observer.observe(root, { childList: true, subtree: true, characterData: true });

    setInterval(scrollToBottom, 400);
    return true;
  }

  if (!setupAutoScroll()) {
    const timer = setInterval(() => {
      if (setupAutoScroll()) clearInterval(timer);
    }, 300);
  }
})();
</script>
"""


def to_markdown(text):
    # 텍스트를 마크다운 문자열로 반환
    return text or ""


def loading_markdown(message):
    # 로딩 메시지를 안전하게 HTML로 감싸서 반환
    safe_message = html_lib.escape(message or "")
    return (
        '<div class="ws-loading shimmer">'
        '<div class="ws-loading-title">⏳ 답변 준비 중</div>'
        f'<div class="ws-loading-msg">{safe_message}</div>'
        '</div>'
    )


def timer_text(elapsed):
    # 타이머 텍스트 형식으로 변환
    return f"⏱ {elapsed}"


class PersonaLine(BaseModel):
    # persona.jsonl 파일 한 줄 스키마
    name: str
    full_name: str
    background: str
    financial_mindset: str
    data_analysis_approach: str
    response_style: str
    key_principles: list[str]
    famous_quotes: list[str] | None = None


def load_persona_names():
    # persona.jsonl에서 persona 이름 목록 로드
    choices = ["없음"]
    if PERSONA_FILE.exists():
        with PERSONA_FILE.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    # full_name 없으면 name 사용하여 채움
                    if isinstance(data, dict) and not data.get("full_name"):
                        data["full_name"] = data.get("name", "")
                    persona = PersonaLine(**data)
                    name = persona.name.strip()
                    if name and name not in choices:
                        choices.append(name)
                except (json.JSONDecodeError, TypeError, ValidationError):
                    continue
    return choices


def _make_elapsed_factory():
    # 경과시간 문자열 생성기 팩토리 반환
    start_time = time.time()

    def elapsed_str():
        return f"{time.time() - start_time:.1f}초"

    return elapsed_str, start_time


def generate_persona_stream(info, endpoint):
    # API 서버 /persona/ 를 호출하여 persona 생성 (generator: 타이머 + 진행 메시지 표시)
    if not info or not info.strip():
        yield "인물 정보를 입력해주세요.", "{}", timer_text("0.0초")
        return

    persona_endpoint = endpoint.rstrip("/").rsplit("/", 1)[0] + "/persona/"
    elapsed_str, _ = _make_elapsed_factory()
    result_queue = Queue()

    def worker():
        try:
            resp = requests.post(
                persona_endpoint,
                json={"info": info.strip()},
                timeout=(10, 300),
            )
            resp.raise_for_status()
            result_queue.put(("ok", resp.json()))
        except requests.exceptions.ConnectionError:
            result_queue.put(("error", f"연결 실패: {persona_endpoint} 확인"))
        except requests.exceptions.Timeout:
            result_queue.put(("error", "요청 시간 초과"))
        except requests.RequestException as exc:
            result_queue.put(("error", f"요청 실패: {exc}"))

    Thread(target=worker, daemon=True).start()

    # 완료될 때까지 진행 메시지 + 타이머 갱신
    while True:
        try:
            kind, payload = result_queue.get_nowait()
            break
        except Empty:
            yield loading_markdown("페르소나 생성 중... (AI가 인물 정보를 검색하고 있습니다)"), "{}", timer_text(elapsed_str())
            time.sleep(0.3)

    if kind == "error":
        yield payload, "{}", timer_text(elapsed_str())
        return

    data = payload

    result_md = f"""**이름**: {data.get('name', '')}

**배경**: {data.get('background', '')}

**금융 사고 방식**: {data.get('financial_mindset', '')}

**데이터 분석 방식**: {data.get('data_analysis_approach', '')}

**답변 스타일**: {data.get('response_style', '')}

**핵심 원칙**: {', '.join(data.get('key_principles', []))}
"""
    quotes = data.get("famous_quotes") or []
    if quotes:
        result_md += f"\n**어록**: {' / '.join(quotes)}"

    yield result_md, json.dumps(data, ensure_ascii=False, indent=2), timer_text(elapsed_str())


def stream_analyze(query, persona_name, endpoint):
    # 질의에 대해 SSE 스트림을 받아 점진적으로 응답과 메타데이터를 반환
    query = (query or "").strip()
    endpoint = (endpoint or "").strip()
    persona_name = (persona_name or "").strip()

    if not query:
        yield loading_markdown("질문을 입력해주세요."), timer_text("0.0초"), ""
        return
    if not endpoint:
        yield loading_markdown("스트림 엔드포인트 URL을 입력해주세요."), timer_text("0.0초"), ""
        return

    text_acc = ""
    result_meta_text = ""
    meta_text = ""
    stdout_acc = ""
    first_delta_received = False
    loading_msg = "요청 중..."
    worker_finished = False
    terminal_event = False
    elapsed_str, _ = _make_elapsed_factory()

    event_queue = Queue()

    def build_meta_text():
        sections = []
        if result_meta_text:
            sections.append(result_meta_text)
        if stdout_acc:
            sections.append(f"[stdout]\n{stdout_acc}")
        return "\n\n".join(sections)

    def reader_worker():
        try:
            payload = {"query": query}
            if persona_name and persona_name != "없음":
                payload["persona_name"] = persona_name

            with requests.post(
                endpoint,
                json=payload,
                headers={"Accept": "text/event-stream"},
                stream=True,
                timeout=(10, 300),
            ) as response:
                response.raise_for_status()

                for raw_line in response.iter_lines(chunk_size=1, decode_unicode=True):
                    if not raw_line:
                        continue

                    line = raw_line.strip()
                    if not line.startswith("data:"):
                        continue

                    payload_text = line[5:].strip()
                    try:
                        parsed = json.loads(payload_text)
                    except json.JSONDecodeError:
                        continue

                    event_queue.put(("event", parsed))
        except requests.exceptions.ConnectionError:
            event_queue.put(("exception", f"연결 실패: {endpoint} 확인"))
        except requests.exceptions.Timeout:
            event_queue.put(("exception", "요청 시간 초과"))
        except requests.RequestException as exc:
            event_queue.put(("exception", f"요청 실패: {exc}"))
        finally:
            event_queue.put(("worker_done", None))

    Thread(target=reader_worker, daemon=True).start()

    while True:
        try:
            kind, payload = event_queue.get(timeout=0.1)
            buffered = [(kind, payload)]
            while True:
                try:
                    buffered.append(event_queue.get_nowait())
                except Empty:
                    break
        except Empty:
            buffered = []

        for kind, payload in buffered:
            if kind == "event":
                event_type = payload.get("type")

                if event_type == "status":
                    if not first_delta_received:
                        loading_msg = payload.get("message", "진행 중...")

                elif event_type == "delta":
                    delta = payload.get("delta", "")
                    if delta:
                        first_delta_received = True
                        text_acc += delta

                elif event_type == "result":
                    result = payload.get("result", payload)
                    result_meta_text = json.dumps(result, ensure_ascii=False, indent=2)
                    meta_text = build_meta_text()
                    if not text_acc:
                        llm_response = result.get("llm_response", "")
                        if llm_response:
                            first_delta_received = True
                            text_acc = llm_response

                elif event_type == "stdout":
                    message = payload.get("message", "")
                    if message:
                        stdout_acc += message
                        meta_text = build_meta_text()

                elif event_type == "error":
                    message = payload.get("message", "알 수 없는 오류")
                    if text_acc:
                        text_acc += f"\n\n\n오류: {message}"
                        first_delta_received = True
                    else:
                        loading_msg = f"오류: {message}"
                    terminal_event = True

                elif event_type == "done":
                    terminal_event = True

            elif kind == "exception":
                loading_msg = str(payload)
                terminal_event = True

            elif kind == "worker_done":
                worker_finished = True

        elapsed = elapsed_str()
        if first_delta_received:
            yield to_markdown(text_acc), timer_text(elapsed), meta_text
        else:
            yield loading_markdown(loading_msg), timer_text(elapsed), meta_text

        if worker_finished and terminal_event:
            break
        if worker_finished and not terminal_event:
            if not first_delta_received:
                loading_msg = "연결 종료"
                yield loading_markdown(loading_msg), timer_text(elapsed_str()), meta_text
            else:
                yield to_markdown(text_acc), timer_text(elapsed_str()), meta_text
            break


def create_app(default_endpoint):
    # Gradio 앱 생성 및 레이아웃 구성
    custom_css = """
    @import url("https://fonts.googleapis.com/css2?family=IBM+Plex+Sans+KR:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;600&display=swap");

    :root {
        --ws-bg: #f7faf9;
        --ws-surface: #ffffff;
        --ws-border: #dbe5e2;
        --ws-text: #182022;
        --ws-muted: #5d6b70;
        --ws-accent: #0f766e;
        --ws-code-bg: #f1f5f9;
    }

    .gradio-container {
        background: radial-gradient(circle at top left, #edf9f6 0%, #f8fbfc 35%, #fdfefe 100%);
    }

    .gradio-container,
    .gradio-container :is(h1, h2, h3, h4, h5, h6, p, span, div, label, button, input, textarea, select) {
        font-family: "IBM Plex Sans KR", "Noto Sans KR", "Source Sans 3", sans-serif !important;
        letter-spacing: 0.005em;
    }

    .ws-loading {
        position: relative;
        overflow: hidden;
        border: 1px solid #cde8e3;
        border-radius: 12px;
        background: linear-gradient(180deg, #f9fefd 0%, #f3fbf9 100%);
        padding: 14px 16px;
    }

    .ws-loading-title {
        color: #0f766e;
        font-weight: 700;
        margin-bottom: 6px;
    }

    .ws-loading-msg {
        color: #365055;
        font-size: 14px;
    }

    .shimmer::after {
        content: "";
        position: absolute;
        top: 0;
        left: -140%;
        width: 80%;
        height: 100%;
        background: linear-gradient(
            100deg,
            rgba(255, 255, 255, 0) 0%,
            rgba(255, 255, 255, 0.55) 45%,
            rgba(255, 255, 255, 0) 100%
        );
        animation: ws-shimmer 1.6s ease-in-out infinite;
    }

    @keyframes ws-shimmer {
        0% { left: -140%; }
        100% { left: 150%; }
    }

    #timer-row {
        margin-top: 8px;
        display: flex;
        justify-content: flex-end;
    }

    #timer-row p {
        margin: 0 !important;
        padding: 4px 10px;
        border-radius: 999px;
        background: #e6fffb;
        border: 1px solid #99f6e4;
        color: #0f766e;
        font-size: 12px;
        font-weight: 600;
    }

    #timer-row,
    #timer-row > .wrap,
    #timer-row > div.prose,
    #timer-row > .html-container {
        border: none !important;
        box-shadow: none !important;
        background: transparent !important;
        padding: 0 !important;
        margin: 0 !important;
    }

    #timer-row hr {
        display: none !important;
        border: 0 !important;
        margin: 0 !important;
    }


    #answer-wrapper {
        min-height: 420px;
        max-height: 42vh;
        overflow-y: auto !important;
        border: 1px solid var(--ws-border) !important;
        border-radius: 14px !important;
        background: var(--ws-surface) !important;
        padding: 16px 20px !important;
        box-shadow: 0 8px 24px rgba(16, 24, 40, 0.06) !important;
    }

    #answer-wrapper, #answer-wrapper .md {
        color: var(--ws-text) !important;
        line-height: 1.72 !important;
        font-size: 15px !important;
        font-family: "IBM Plex Sans KR", "Noto Sans KR", "Source Sans 3", sans-serif !important;
        letter-spacing: 0.005em;
    }

    #answer-wrapper h1, #answer-wrapper h2, #answer-wrapper h3 {
        margin: 0.8em 0 0.35em !important;
        letter-spacing: -0.01em;
        color: #0b3b39 !important;
    }

    #answer-wrapper p {
        margin: 0.35em 0 !important;
    }

    #answer-wrapper strong {
        font-weight: 650;
        letter-spacing: 0.01em;
    }

    #answer-wrapper ul, #answer-wrapper ol {
        margin: 0.4em 0 !important;
        padding-left: 1.4em !important;
    }

    #answer-wrapper li {
        margin: 0.15em 0 !important;
    }

    #answer-wrapper blockquote {
        margin: 0.8em 0 !important;
        padding: 0.65em 0.9em !important;
        border-left: 4px solid #14b8a6 !important;
        background: #f0fdfa !important;
        color: #115e59 !important;
        border-radius: 8px;
    }

    #answer-wrapper a {
        color: #0f766e !important;
        text-decoration: underline;
        text-underline-offset: 2px;
    }

    #answer-wrapper code {
        background: var(--ws-code-bg) !important;
        color: #0b3b39 !important;
        border: 1px solid #d9e2ec;
        border-radius: 6px;
        padding: 0.1em 0.35em;
        font-size: 0.92em;answer-wrapper
        background: #0f172a !important;
        color: #e2e8f0 !important;
        border-radius: 10px;
        border: 1px solid #1e293b;
        padding: 0.85em 1em !important;
        overflow-x: auto;
    }

    #answer-wrapper pre code {
        background: transparent !important;
        border: none;
        color: inherit !important;
        padding: 0;
    }

    #answer-wrapper table {
        width: 100%;
        border-collapse: collapse;
        margin: 0.7em 0;
        border: 1px solid #dbe5e2;
    }

    #answer-wrapper th {
        background: #eef6f4;
        color: #0f3f3b;
        font-weight: 600;
    }

    #answer-wrapper th,
    #answer-wrapper td {
        border: 1px solid #dbe5e2;
        padding: 0.5em 0.6em;
        text-align: left;
        vertical-align: top;
    }

    #answer-wrapper > .wrap,
    #answer-wrapper > div.prose,
    #answer-wrapper > .html-container {
        padding: 0 !important;
        margin: 0 !important;
        border: none !important;
        box-shadow: none !important;
    }

    #meta-box {
        max-height: 300px;
        overflow-y: auto;
    }

    #persona-result-wrapper {
        min-height: 200px;
        max-height: 50vh;
        overflow-y: auto !important;
        border: 1px solid var(--ws-border) !important;
        border-radius: 14px !important;
        background: var(--ws-surface) !important;
        padding: 16px 20px !important;
        box-shadow: 0 8px 24px rgba(16, 24, 40, 0.06) !important;
    }

    /* 드롭다운 열릴 때 페이지 스크롤 고정 */
    body:has(.options:not(.hide)) {
        overflow: hidden !important;
    }
    """

    theme = gr.themes.Soft(
        primary_hue="emerald",
        secondary_hue="blue",
        neutral_hue="slate",
        radius_size="lg",
    )

    with gr.Blocks(title="Wallstreet-AI", css=custom_css, theme=theme) as demo:
        gr.Markdown("## 📈 Wallstreet-AI")
        gr.Markdown("A finance AI that combines earnings, news, and market trends in one place.")

        with gr.Tabs():
            with gr.Tab("💬 질문하기"):
                with gr.Row():
                    with gr.Column(scale=3):
                        endpoint = gr.Textbox(
                            label="SSE Endpoint",
                            value=default_endpoint,
                        )
                        persona_dropdown = gr.Dropdown(
                            label="페르소나 선택",
                            choices=load_persona_names(),
                            value="없음",
                            interactive=True,
                        )
                        refresh_btn = gr.Button("🔄 페르소나 목록 새로고침", size="sm")
                        query = gr.Textbox(
                            label="질문",
                            lines=3,
                            value=EXAMPLE_QUERIES[0],
                        )
                        with gr.Row():
                            run_btn = gr.Button("🔍 질문하기", variant="primary", scale=3)
                            clear_btn = gr.Button("🗑 초기화", scale=1)

                    with gr.Column(scale=1):
                        gr.Markdown("**Example Questions**")
                        for ex in EXAMPLE_QUERIES:
                            gr.Button(ex, size="sm").click(
                                fn=lambda x=ex: x, outputs=query
                            )

                answer = gr.Markdown(value=to_markdown(""), label="답변", elem_id="answer-wrapper")
                timer = gr.Markdown(value=timer_text("0.0초"), elem_id="timer-row")
                meta = gr.Code(label="진행 과정 출력", language="json", elem_id="meta-box")

                gr.HTML(AUTO_SCROLL_SCRIPT, visible=False)

                run_btn.click(
                    fn=stream_analyze,
                    inputs=[query, persona_dropdown, endpoint],
                    outputs=[answer, timer, meta],
                )
                query.submit(
                    fn=stream_analyze,
                    inputs=[query, persona_dropdown, endpoint],
                    outputs=[answer, timer, meta],
                )
                clear_btn.click(
                    fn=lambda: (to_markdown(""), timer_text("0.0초"), ""),
                    outputs=[answer, timer, meta],
                )
                refresh_btn.click(
                    fn=lambda: gr.Dropdown(choices=load_persona_names(), value="없음"),
                    outputs=[persona_dropdown],
                )

            with gr.Tab("🧑‍💼 페르소나 만들기"):
                gr.Markdown("### 새 페르소나 생성")
                gr.Markdown(
                    "금융 인물의 이름이나 설명을 입력하면 AI가 해당 인물의 금융 사고방식, "
                    "분석 스타일, 답변 스타일을 자동으로 생성합니다. "
                )

                with gr.Row():
                    with gr.Column(scale=2):
                        persona_info_input = gr.Textbox(
                            label="인물 정보",
                            placeholder="예: 워렌 버핏, JP모건, 가타야마 아키라  ...",
                            lines=3,
                        )
                        persona_gen_btn = gr.Button("✨ 페르소나 생성", variant="primary")

                    with gr.Column(scale=1):
                        gr.Markdown("**예시 인물**")
                        example_personas = ["워렌 버핏", "JP모건", "가타야마 아키라"]
                        for ep in example_personas:
                            gr.Button(ep, size="sm").click(
                                fn=lambda x=ep: x, outputs=persona_info_input
                            )

                persona_result_md = gr.Markdown(
                    value="",
                    label="생성 결과",
                    elem_id="persona-result-wrapper",
                )
                persona_timer = gr.Markdown(value=timer_text("0.0초"), elem_id="timer-row")
                persona_result_json = gr.Code(
                    label="페르소나 JSON",
                    language="json",
                    elem_id="meta-box",
                )

                persona_gen_btn.click(
                    fn=generate_persona_stream,
                    inputs=[persona_info_input, endpoint],
                    outputs=[persona_result_md, persona_result_json, persona_timer],
                )

    return demo


def main():
    parser = argparse.ArgumentParser(description="Wallstreet-AI Gradio UI")
    parser.add_argument("--api-url", type=str, default="http://0.0.0.0:8000/analyze/", help="FastAPI SSE 엔드포인트 URL")
    parser.add_argument("--share", action="store_true")
    parser.add_argument("--server-name", type=str, default="0.0.0.0")
    parser.add_argument("--port", type=int, default=7860)
    args = parser.parse_args()

    print(f"FastAPI : {args.api_url}")
    print(f"Gradio  : http://{args.server_name}:{args.port}")

    app = create_app(args.api_url)
    app.queue(default_concurrency_limit=8, max_size=64)
    app.launch(
        share=args.share,
        server_name=args.server_name,
        server_port=args.port,
        debug=True,
    )


if __name__ == "__main__":
    main()
