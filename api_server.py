import asyncio
import json
from contextlib import suppress
from uuid import uuid4

from fastapi import FastAPI
from pydantic import BaseModel
from fastapi.responses import StreamingResponse

from pipeline import pipeline

app = FastAPI()


class StreamAnalyzeRequest(BaseModel):
    query: str


@app.post("/analyze/")
async def analyze_stream(request: StreamAnalyzeRequest):
    request_id = uuid4().hex[:8]
    loop = asyncio.get_running_loop()
    queue = asyncio.Queue()
    state = {"result": None, "error": None}

    def emit_event(payload: dict):
        payload.setdefault("request_id", request_id)
        loop.call_soon_threadsafe(queue.put_nowait, payload)

    def on_delta(delta: str):
        emit_event({"type": "delta", "delta": delta})

    def on_status(message: str):
        emit_event({"type": "status", "message": message})

    async def run_pipeline_task():
        try:
            state["result"] = await pipeline(
                request.query,
                stream_output=True,
                stream_callback=on_delta,
                status_callback=on_status,
                request_id=request_id,
            )
        except Exception as exc:
            state["error"] = str(exc)
        finally:
            await queue.put(None)

    task = asyncio.create_task(run_pipeline_task())

    async def event_generator():
        try:
            while True:
                event = await queue.get()
                if event is None:
                    break
                payload = json.dumps(event, ensure_ascii=False)
                yield f"data: {payload}\n\n"

            if state["error"]:
                payload = json.dumps({"type": "error", "message": state["error"], "request_id": request_id}, ensure_ascii=False)
                yield f"data: {payload}\n\n"
            else:
                result_payload = json.dumps(
                    {
                        "type": "result",
                        "result": {
                            "request_id": request_id,
                            "query": state["result"].query,
                            "ticker": state["result"].ticker,
                            "analysis_type": state["result"].analysis_type,
                            "data_context": state["result"].data_context,
                            "llm_response": state["result"].llm_response,
                            "timestamp": getattr(state["result"], "timestamp", None),
                        },
                        "request_id": request_id,
                    },
                    ensure_ascii=False,
                )
                yield f"data: {result_payload}\n\n"
                yield f"data: {{\"type\":\"done\",\"request_id\":\"{request_id}\"}}\n\n"
        finally:
            if not task.done():
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task

    headers = {
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
    }
    return StreamingResponse(event_generator(), media_type="text/event-stream", headers=headers)
