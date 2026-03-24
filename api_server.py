from fastapi import FastAPI, Request
from pydantic import BaseModel
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
        "data_context": result.data_context,
        "llm_response": result.llm_response,
        "timestamp": getattr(result, "timestamp", None)
    })
