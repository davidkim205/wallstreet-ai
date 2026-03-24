import argparse
import html as html_lib
import json
import re
import time
from typing import Generator, Tuple

import gradio as gr
import requests
import markdown as md_lib


EXAMPLE_QUERIES = [
    "AAPL의 최근 실적과 투자 포인트 요약해줘",
    "TSLA의 기술적 분석 리포트 작성",
    "삼성전자(005930.KS) SWOT 분석",
    "쿠팡 매출액 알려줘",
]


def to_html(text: str) -> str:
    try:
        body = md_lib.markdown(
            text,
            extensions=["tables", "fenced_code", "nl2br"]
        )
    except ImportError:
        escaped = html_lib.escape(text)
        escaped = re.sub(
            r'(https?://[^\s\)\]\"\'<>]+)',
            r'<a href="\1" target="_blank" style="color:#1a73e8;word-break:break-all">\1</a>',
            escaped,
        )
        body = escaped.replace("\n", "<br>")

    return f"""
    <div id="answer-content" style="
        font-family: 'Noto Sans KR', sans-serif;
        font-size: 14px;
        line-height: 1.6;
        padding: 14px 18px;
        word-break: break-word;
    ">{body}</div>
    <script>
        (function() {{
            var wrapper = document.getElementById('answer-wrapper');
            if (wrapper) wrapper.scrollTop = wrapper.scrollHeight;
        }})();
    </script>
    """


def stream_analyze(
    query: str, endpoint: str
) -> Generator[Tuple[str, str, str], None, None]:
    query = (query or "").strip()
    endpoint = (endpoint or "").strip()

    if not query:
        yield to_html(""), "질문을 입력해주세요.", ""
        return
    if not endpoint:
        yield to_html(""), "스트림 엔드포인트 URL을 입력해주세요.", ""
        return

    text_acc = ""
    meta_text = ""
    start_time = time.time()

    def elapsed_str():
        return f"⏱ {time.time() - start_time:.1f}초"

    yield to_html(""), "⏳ 요청 중...", meta_text

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

                event_type = payload.get("type")

                if event_type == "status":
                    status_msg = payload.get("message", "진행 중...")
                    yield to_html(text_acc), status_msg, meta_text

                elif event_type == "delta":
                    delta = payload.get("delta", "")
                    if delta:
                        text_acc += delta
                        yield to_html(text_acc), f"⏳ 답변을 작성하고 있어요... {elapsed_str()}", meta_text

                elif event_type == "result":
                    result = payload.get("result", {})
                    meta_text = json.dumps(result, ensure_ascii=False, indent=2)
                    if not text_acc:
                        text_acc = result.get("llm_response", "") or text_acc
                    yield to_html(text_acc), f"최종 결과 수신 | {elapsed_str()}", meta_text

                elif event_type == "error":
                    message = payload.get("message", "알 수 없는 오류")
                    yield to_html(text_acc), f"오류: {message} | {elapsed_str()}", meta_text
                    return

                elif event_type == "done":
                    yield to_html(text_acc), f"완료 | {elapsed_str()}", meta_text
                    return

            yield to_html(text_acc), f"연결 종료 | {elapsed_str()}", meta_text

    except requests.exceptions.ConnectionError:
        yield to_html(text_acc), f"연결 실패: {endpoint} 확인", meta_text
    except requests.exceptions.Timeout:
        yield to_html(text_acc), f"요청 시간 초과", meta_text
    except requests.RequestException as exc:
        yield to_html(text_acc), f"요청 실패: {exc}", meta_text


def create_app(default_endpoint: str) -> gr.Blocks:
    custom_css = """
    /* 답변 wrapper: 높이 고정 + 스크롤 */
    #answer-wrapper {
        min-height: 400px;
        max-height: 60vh;
        overflow-y: auto !important;
        border: 1px solid #e0e0e0 !important;
        border-radius: 8px !important;
        background: #fafafa !important;
        padding: 0 !important;
    }
    #answer-wrapper p {
        margin: 0.2em 0 !important;
    }  
    #answer-wrapper br {
        line-height: 1.2 !important;
    }
    #answer-wrapper ul, #answer-wrapper ol {
        margin: 0.3em 0 !important;
        padding-left: 1.4em !important;
    }
    #answer-wrapper li {
        margin: 0.1em 0 !important;
    }
    #answer-wrapper h1, #answer-wrapper h2, #answer-wrapper h3 {
        margin: 0.6em 0 0.2em !important;
    }
    /* Gradio가 gr.HTML 안쪽에 추가하는 래퍼 여백 제거 */
    #answer-wrapper > .wrap,
    #answer-wrapper > div.prose,
    #answer-wrapper > .html-container {
        padding: 0 !important;
        margin: 0 !important;
        border: none !important;
        box-shadow: none !important;
    }

    /* status 텍스트박스 높이 최소화 */
    #status-bar textarea {
        min-height: 36px !important;
        height: 36px !important;
        resize: none !important;
        font-size: 13px !important;
        color: #555 !important;
        background: #f5f5f5 !important;
    }

    /* meta JSON 코드블록 최대 높이 */
    #meta-box {
        max-height: 300px;
        overflow-y: auto;
    }
    """

    with gr.Blocks(title="Wallstreet-AI Stream Viewer", css=custom_css) as demo:
        gr.Markdown("## 📈 Wallstreet-AI Stream Viewer")
        gr.Markdown("`/analyze/stream` SSE 응답을 실시간으로 표시합니다.")

        with gr.Row():
            with gr.Column(scale=3):
                endpoint = gr.Textbox(
                    label="SSE Endpoint",
                    value=default_endpoint,
                )
                query = gr.Textbox(
                    label="질문",
                    lines=3,
                    placeholder="예: 엔비디아 단기 전망 알려줘",
                )
                with gr.Row():
                    run_btn   = gr.Button("🔍 질문하기", variant="primary", scale=3)
                    clear_btn = gr.Button("🗑 초기화", scale=1)

            with gr.Column(scale=1):
                gr.Markdown("**예시 질의 (클릭 → 자동 입력)**")
                for ex in EXAMPLE_QUERIES:
                    gr.Button(ex, size="sm").click(
                        fn=lambda x=ex: x, outputs=query
                    )

        # 상태 표시
        status = gr.Textbox(
            label="상태",
            interactive=False,
            elem_id="status-bar",
        )

        # 실시간 답변 (HTML + 고정 높이 + 자동 스크롤)
        gr.Markdown("### 📝 실시간 답변")
        answer = gr.HTML(value=to_html(""), elem_id="answer-wrapper")

        # 최종 결과 JSON
        meta = gr.Code(label="최종 결과 (JSON)", language="json", elem_id="meta-box")

        run_btn.click(
            fn=stream_analyze,
            inputs=[query, endpoint],
            outputs=[answer, status, meta],
        )
        query.submit(
            fn=stream_analyze,
            inputs=[query, endpoint],
            outputs=[answer, status, meta],
        )
        clear_btn.click(
            fn=lambda: (to_html(""), "", ""),
            outputs=[answer, status, meta],
        )

    return demo


def main():
    parser = argparse.ArgumentParser(description="Wallstreet-AI Gradio UI")
    parser.add_argument("--api-url",type=str,default="http://0.0.0.0:8000/analyze",help="FastAPI SSE 엔드포인트 URL",)
    parser.add_argument("--share",action="store_true")
    parser.add_argument("--server-name",type=str, default="0.0.0.0")
    parser.add_argument("--port",type=int, default=7860)
    args = parser.parse_args()

    print(f"FastAPI : {args.api_url}")
    print(f"Gradio  : http://{args.server_name}:{args.port}")

    app = create_app(args.api_url)
    app.launch(
        share=args.share,
        server_name=args.server_name,
        server_port=args.port,
        debug=True,
    )


if __name__ == "__main__":
    main()