import json
from queue import Empty, Queue
from threading import Thread
from typing import Optional

from fastapi import FastAPI
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from pipeline import pipeline as run_pipeline
from persona.make_persona import make_persona

app = FastAPI()


class PersonaRequest(BaseModel):
    info: str


@app.post("/persona/")
async def create_persona(request: PersonaRequest):
    info = (request.info or "").strip()
    if not info:
        return JSONResponse(status_code=400, content={"error": "info 필드가 비어 있습니다."})

    try:
        persona = make_persona(info)
    except Exception as exc:
        return JSONResponse(status_code=500, content={"error": str(exc)})

    if persona is None:
        return JSONResponse(status_code=500, content={"error": "페르소나 생성에 실패했습니다."})

    return JSONResponse(content=persona.model_dump())

class QueryRequest(BaseModel):
    query: str
    stream: bool = True
    persona_name: Optional[str] = None


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


@app.post("/analyze/")
async def analyze(request: QueryRequest):
    query = (request.query or "").strip()
    stream = request.stream

    persona_name = (request.persona_name or "").strip() or None

    if not stream:
        result = run_pipeline(
            query,
            persona_name=persona_name,
            status_callback=None,
            stream_callback=None,
            stream=False,
        )
        return JSONResponse(
            content=jsonable_encoder(
                {
                    "type": "result",
                    "query": result.query,
                    "ticker": result.ticker,
                    "analysis_type": result.analysis_type,
                    "data_context": result.data_context,
                    "llm_response": result.llm_response,
                    "timestamp": getattr(result, "timestamp", None),
                }
            )
        )

    def event_stream():
        event_queue: Queue = Queue()

        def on_status(message: str):
            event_queue.put({"type": "status", "message": message})

        def on_delta(delta: str):
            if stream:
                event_queue.put({"type": "delta", "delta": delta})

        def worker():
            try:
                result = run_pipeline(
                    query,
                    persona_name=persona_name,
                    status_callback=on_status,
                    stream_callback=on_delta if stream else None,
                    stream=stream,
                )
                event_queue.put(
                    {
                        "type": "result",
                        "query": result.query,
                        "ticker": result.ticker,
                        "analysis_type": result.analysis_type,
                        "data_context": result.data_context,
                        "llm_response": result.llm_response,
                        "timestamp": getattr(result, "timestamp", None),
                    }
                )
            except Exception as exc:
                event_queue.put({"type": "error", "message": str(exc)})
            finally:
                event_queue.put({"type": "done"})

        yield _sse({"type": "status", "message": "요청 수신. 분석 준비 중..."})
        Thread(target=worker, daemon=True).start()

        done = False
        while not done:
            try:
                event = event_queue.get(timeout=0.2)
            except Empty:
                continue
            yield _sse(jsonable_encoder(event))
            if event.get("type") == "done":
                done = True

    headers = {
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
    }
    return StreamingResponse(event_stream(), media_type="text/event-stream", headers=headers)
