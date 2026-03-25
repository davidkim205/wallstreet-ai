from fastapi import FastAPI, Request
from pydantic import BaseModel
from dataclasses import asdict, is_dataclass
from pipeline import pipeline
from fastapi.responses import JSONResponse

app = FastAPI()

class QueryRequest(BaseModel):
    query: str

@app.post("/analyze")
async def analyze(request: QueryRequest):
    result = pipeline(request.query)
    # Convert AnalysisResult to dict for JSON serialization
    return JSONResponse(content={
        "query": result.query,
        "ticker": result.ticker,
        "analysis_type": result.analysis_type,
        "data_context": asdict(result.data_context) if is_dataclass(result.data_context) else result.data_context,
        "llm_response": result.llm_response,
        "timestamp": getattr(result, "timestamp", None)
    })
