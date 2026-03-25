import argparse
import html as html_lib
import json
import time
from queue import Empty, Queue
from threading import Thread
from typing import Generator, Tuple

import gradio as gr
import requests


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


def to_markdown(text: str) -> str:
    return text or ""


def loading_markdown(message: str) -> str:
    safe_message = html_lib.escape(message or "")
    return (
        '<div class="ws-loading shimmer">'
        '<div class="ws-loading-title">⏳ 답변 준비 중</div>'
        f'<div class="ws-loading-msg">{safe_message}</div>'
        '</div>'
    )


def timer_text(elapsed: str) -> str:
    return f"⏱ {elapsed}"


def stream_analyze(
    query: str, endpoint: str
) -> Generator[Tuple[str, str, str], None, None]:
    query = (query or "").strip()
    endpoint = (endpoint or "").strip()

    if not query:
        yield loading_markdown("질문을 입력해주세요."), timer_text("0.0초"), ""
        return
    if not endpoint:
        yield loading_markdown("스트림 엔드포인트 URL을 입력해주세요."), timer_text("0.0초"), ""
        return

    text_acc = ""
    meta_text = ""
    first_delta_received = False
    loading_msg = "요청 중..."
    worker_finished = False
    terminal_event = False
    start_time = time.time()

    event_queue: Queue = Queue()

    def elapsed_str() -> str:
        return f"{time.time() - start_time:.1f}초"

    def reader_worker() -> None:
        try:
            with requests.post(
                endpoint,
                json={"query": query},
                headers={"Accept": "text/event-stream"},
                stream=True,
                timeout=(10, 300),
            ) as response:
                response.raise_for_status()

                for raw_line in response.iter_lines(decode_unicode=True):
                    if not raw_line or not raw_line.startswith("data: "):
                        continue

                    payload_text = raw_line[6:]
                    try:
                        payload = json.loads(payload_text)
                    except json.JSONDecodeError:
                        continue

                    event_queue.put(("event", payload))
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
                    result = payload.get("result", {})
                    meta_text = json.dumps(result, ensure_ascii=False, indent=2)
                    if not text_acc:
                        llm_response = result.get("llm_response", "")
                        if llm_response:
                            first_delta_received = True
                            text_acc = llm_response

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
            # 서버가 done 이벤트 없이 종료된 경우 마지막 화면 갱신 후 종료
            if not first_delta_received:
                loading_msg = "연결 종료"
                yield loading_markdown(loading_msg), timer_text(elapsed_str()), meta_text
            else:
                yield to_markdown(text_acc), timer_text(elapsed_str()), meta_text
            break


def create_app(default_endpoint: str) -> gr.Blocks:
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
        max-height: 62vh;
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
        font-size: 0.92em;
        font-family: "JetBrains Mono", "IBM Plex Mono", "Source Code Pro", monospace !important;
        letter-spacing: 0.01em;
    }

    #answer-wrapper pre {
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

        with gr.Row():
            with gr.Column(scale=3):
                endpoint = gr.Textbox(
                    label="SSE Endpoint",
                    value=default_endpoint,
                )
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

        meta = gr.Code(label="최종 결과 (JSON)", language="json", elem_id="meta-box")

        gr.HTML(AUTO_SCROLL_SCRIPT, visible=False)

        run_btn.click(
            fn=stream_analyze,
            inputs=[query, endpoint],
            outputs=[answer, timer, meta],
        )
        query.submit(
            fn=stream_analyze,
            inputs=[query, endpoint],
            outputs=[answer, timer, meta],
        )
        clear_btn.click(
            fn=lambda: (to_markdown(""), timer_text("0.0초"), ""),
            outputs=[answer, timer, meta],
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
