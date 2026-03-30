import json
import sys
import threading
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


class _ThreadStdoutProxy:
    def __init__(self, target):
        self._target = target
        self._handlers = {}
        self._lock = threading.RLock()
        self.encoding = getattr(target, "encoding", "utf-8")
        self.errors = getattr(target, "errors", None)

    def register(self, thread_id: int, handler) -> None:
        with self._lock:
            self._handlers[thread_id] = handler

    def unregister(self, thread_id: int) -> None:
        with self._lock:
            self._handlers.pop(thread_id, None)

    def _resolve(self):
        thread_id = threading.get_ident()
        with self._lock:
            return self._handlers.get(thread_id), self._target

    def write(self, data):
        handler, target = self._resolve()
        if handler:
            return handler.write(data)
        return target.write(data)

    def flush(self):
        handler, target = self._resolve()
        if handler:
            handler.flush()
        return target.flush()

    def isatty(self):
        return getattr(self._target, "isatty", lambda: False)()

    def fileno(self):
        return self._target.fileno()

    def writable(self):
        return True

    def __getattr__(self, name):
        return getattr(self._target, name)


class _QueueingStdoutTee:
    def __init__(self, target, event_queue: Queue):
        self._target = target
        self._event_queue = event_queue

    def write(self, data):
        written = self._target.write(data)
        if data:
            self._event_queue.put({"type": "stdout", "message": data})
        return written

    def flush(self):
        self._target.flush()


_stdout_proxy = _ThreadStdoutProxy(sys.stdout)
sys.stdout = _stdout_proxy


class PersonaRequest(BaseModel):
    info: str
    stream: bool = True


PERSONA_STATUS_MESSAGES = [
    "인물 정보 수집 중...",
    "웹 검색을 통해 배경 조사 중...",
    "금융 사고 방식 분석 중...",
    "데이터 분석 접근법 평가 중...",
    "답변 스타일 특성 파악 중...",
    "핵심 투자 원칙 추출 중...",
    "대표 어록 정리 중...",
    "페르소나 프로필 구성 중...",
    "최종 검증 및 저장 준비 중...",
]


def _build_persona_payload(persona) -> dict:
    return {
        "type": "result",
        "name": persona.name,
        "full_name": persona.full_name,
        "summary": persona.summary,
        "financial_mindset": persona.financial_mindset,
        "data_analysis_approach": persona.data_analysis_approach,
        "response_style": persona.response_style,
        "key_principles": persona.key_principles,
        "famous_quotes": getattr(persona, "famous_quotes", None),
    }


@app.post("/persona/")
async def create_persona(request: PersonaRequest):
    info = (request.info or "").strip()
    stream = request.stream

    if not info:
        return JSONResponse(status_code=400, content={"error": "info 필드가 비어 있습니다."})

    if not stream:
        try:
            persona = make_persona(info)
        except Exception as exc:
            return JSONResponse(status_code=500, content={"error": str(exc)})

        if persona is None:
            return JSONResponse(status_code=500, content={"error": "페르소나 생성에 실패했습니다."})

        return JSONResponse(content=persona.model_dump())

    def event_stream():
        event_queue: Queue = Queue()

        def status_sender():
            import asyncio

            async def send_status():
                for i, message in enumerate(PERSONA_STATUS_MESSAGES[:-1]):  # 마지막 메시지는 완료 시점에 사용
                    event_queue.put({"type": "status", "message": message})
                    await asyncio.sleep(8)

            # 비동기 이벤트 루프에서 실행
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(send_status())

        def worker():
            thread_id = threading.get_ident()
            _stdout_proxy.register(thread_id, _QueueingStdoutTee(_stdout_proxy._target, event_queue))
            try:
                # status 메시지 전송 스레드 시작
                status_thread = Thread(target=status_sender, daemon=True)
                status_thread.start()

                persona = make_persona(info)

                if persona is None:
                    event_queue.put({"type": "error", "message": "페르소나 생성에 실패했습니다."})
                else:
                    event_queue.put(_build_persona_payload(persona))
            except Exception as exc:
                event_queue.put({"type": "error", "message": str(exc)})
            finally:
                _stdout_proxy.unregister(thread_id)
                event_queue.put({"type": "done"})

        yield _sse({"type": "status", "message": "페르소나 생성 준비 중..."})
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

class QueryRequest(BaseModel):
    query: str
    stream: bool = True
    persona_name: Optional[str] = None


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _build_result_payload(result, stdout: str = "") -> dict:
    payload = {
        "type": "result",
        "query": result.query,
        "ticker": result.ticker,
        "analysis_type": result.analysis_type,
        "data_context": result.data_context,
        "llm_response": result.llm_response,
        "timestamp": getattr(result, "timestamp", None),
    }
    if stdout:
        payload["stdout"] = stdout
    return payload


@app.post("/analyze/")
async def analyze(request: QueryRequest):
    query = (request.query or "").strip()
    stream = request.stream

    persona_name = (request.persona_name or "").strip() or None

    if not stream:
        stdout_messages = []

        class _ListStdoutTee:
            def __init__(self, target):
                self._target = target

            def write(self, data):
                written = self._target.write(data)
                if data:
                    stdout_messages.append(data)
                return written

            def flush(self):
                self._target.flush()

        thread_id = threading.get_ident()
        _stdout_proxy.register(thread_id, _ListStdoutTee(_stdout_proxy._target))
        try:
            result = run_pipeline(
                query,
                persona_name=persona_name,
                status_callback=None,
                stream_callback=None,
                stream=False,
            )
        finally:
            _stdout_proxy.unregister(thread_id)
        return JSONResponse(
            content=jsonable_encoder(_build_result_payload(result, stdout="".join(stdout_messages)))
        )

    def event_stream():
        event_queue: Queue = Queue()

        def on_status(message: str):
            event_queue.put({"type": "status", "message": message})

        def on_delta(delta: str):
            if stream:
                event_queue.put({"type": "delta", "delta": delta})

        def worker():
            thread_id = threading.get_ident()
            _stdout_proxy.register(thread_id, _QueueingStdoutTee(_stdout_proxy._target, event_queue))
            try:
                result = run_pipeline(
                    query,
                    persona_name=persona_name,
                    status_callback=on_status,
                    stream_callback=on_delta if stream else None,
                    stream=stream,
                )
                event_queue.put(_build_result_payload(result))
            except Exception as exc:
                event_queue.put({"type": "error", "message": str(exc)})
            finally:
                _stdout_proxy.unregister(thread_id)
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
