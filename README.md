# Wallstreet-AI

Wallstreet-AI is a financial analysis assistant that combines legendary investor personas with an agentic AI pipeline to interpret prices, fundamentals, earnings, news, and technical indicators, then provide tailored investment insights.

## Overview

Wallstreet-AI is an agentic AI financial analysis assistant that turns user questions into a structured analysis workflow. When the LLM extracts the ticker, analysis type, time period, and target earnings year/quarter from a question, the system selectively gathers price data, fundamentals, technical indicators, earnings information, and news context to assemble a single analysis input.

Then, an OpenAI-based model generates reports such as general, technical, fundamental, earnings, SWOT, news-summary, and comparative analyses from the collected data. It can also apply personas based on the investment philosophies of figures like Warren Buffett, so the perspective and explanation style can change even when the underlying data is the same.

Rather than being a simple Q&A chatbot, the project is designed as an analysis system that follows the flow of `intent parsing -> tool routing -> data collection -> news enrichment -> context building -> LLM generation -> result streaming and storage`. It provides a CLI, a FastAPI SSE API, and a Gradio UI so the same core pipeline can be reused for local experiments, web interfaces, and service integrations.

The project provides three entry points.

- `pipeline.py`: CLI-based interactive analysis
- `api_server.py`: FastAPI analysis API and SSE streaming server
- `gradio_app.py`: Gradio web UI

## Key Features

- Extracts ticker symbols, analysis types, periods, and year/quarter information from user questions
- Automatically selects the required data sources based on the analysis type
- Collects price, fundamentals, news, and earnings data using `yfinance`
- Calculates technical indicators such as RSI, MACD, moving averages, and Bollinger Bands
- Collects article bodies from Google News RSS and builds news context
- Generates analysis reports and streams responses through the OpenAI Responses API
- Creates and applies investor-style personas
- Stores analysis results and personas in JSONL files

## Overall Flow

```text
User question
  -> Intent parsing
  -> Tool routing
  -> Market data collection
  -> News context generation
  -> LLM analysis generation
  -> Result storage (JSONL)
```

## Demo

![alt text](docs/assets/analyze.gif)

Streaming stock analysis with pipeline progress and final results.


![alt text](docs/assets/persona.gif)

Persona generation and saving based on a financial figure.

## Quick Start

The easiest way to try it locally is to follow the steps below.

1. Create a virtual environment

```bash
uv venv
source .venv/bin/activate
```

2. Install dependencies

```bash
uv pip install -r requirements.txt
```

3. Prepare the environment variable file

```bash
cp .env.example .env
```

4. Run the FastAPI server

```bash
uvicorn api_server:app --reload
```

5. Run the Gradio UI

```bash
python gradio_app.py --api-url http://127.0.0.1:8000/analyze/
```

6. Open the UI in your browser and enter a question

If you only want to test the CLI, simply configure `.env` and run `python pipeline.py`.

## Installation

### 1. Prepare the Python environment

Python 3.10 or later is recommended.

```bash
uv venv
source .venv/bin/activate
```

### 2. Install dependencies

```bash
uv pip install -r requirements.txt
```

### 3. Configure environment variables

```bash
cp .env.example .env
```

A basic example is shown below.

```env
LLM_MODEL_NAME=gpt-5-mini
LLM_MODEL_API_KEY=<your api key>
LOG_FILE=analysis_results.jsonl
PERSONA_FILE=persona.jsonl
```

Environment variables:

- `LLM_MODEL_NAME`: The model name used for OpenAI calls
- `LLM_MODEL_API_KEY`: Your OpenAI API key
- `LOG_FILE`: Path for saving analysis results in JSONL format
- `PERSONA_FILE`: Path for saving personas in JSONL format

In the current codebase, OpenAI client authentication uses `LLM_MODEL_API_KEY` first. If it is not set, it falls back to the OpenAI SDK's default authentication environment.

## How to Run

### 1. Run the CLI

```bash
python pipeline.py
```

Behavior:

- Shows the list of saved personas at startup
- Lets you enter a persona number or press Enter to continue without a persona
- After you enter a question, the analysis result and a summary of the collected data are printed

Exit commands:

- `exit`
- `quit`
- `종료`

### 2. Run the FastAPI server

```bash
uvicorn api_server:app --reload
```

Default address:

- API server: `http://127.0.0.1:8000`

Available endpoints:

- `POST /analyze/`: Run analysis
- `POST /persona/`: Create a new persona

### 3. Run the Gradio UI

First, run the FastAPI server, then:

```bash
python gradio_app.py --api-url http://127.0.0.1:8000/analyze/
```
By default, Gradio binds to `0.0.0.0:7860`.


Options:

- `--api-url`: The analysis SSE endpoint to connect to. The code defaults to `http://0.0.0.0:8000/analyze/`, but for local use it is better to set an explicit address such as `http://127.0.0.1:8000/analyze/`.
- `--port`: Gradio port
- `--server-name`: Binding address
- `--share`: Generate a public link

The Gradio UI has three tabs.

- `Ask a Question`: Submit analysis requests, view streaming responses, and display progress metadata
- `Create a Persona`: Create and save personas based on financial figures
- `Investor Profile`: Browse saved investor profiles and personas


## API Usage Examples

### 1. Analysis API

```bash
curl -X POST "http://127.0.0.1:8000/analyze/" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "Summarize AAPL's recent earnings and key investment points",
    "stream": false
  }'
```

Example response:

```json
{
  "type": "result",
  "query": "Summarize AAPL's recent earnings and key investment points",
  "ticker": "AAPL",
  "analysis_type": "earnings",
  "data_context": {},
  "llm_response": "...",
  "timestamp": "2026-03-30 10:00:00",
  "stdout": "..."
}
```

Streaming request example:

```bash
curl -N -X POST "http://127.0.0.1:8000/analyze/" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "Summarize AAPL'\''s recent earnings and key investment points",
    "stream": true
  }'
```

Example SSE event flow:

```text
data: {"type":"status","message":"인텐트 분석 중..."}

data: {"type":"stdout","message":"[③] 데이터 수집 중 (ticker=AAPL, period=1y)..."}

data: {"type":"delta","delta":"애플의 최근 실적은 마진 개선 흐름을 보여주며 ..."}

data: {"type":"result","ticker":"AAPL","analysis_type":"earnings", ...}

data: {"type":"done"}
```

Notes:

- `status`: High-level pipeline progress updates
- `stdout`: Server-side logs emitted during analysis
- `delta`: Incremental response chunks from the model
- `result`: Final analysis payload, same as the `stream: false` response
- `done`: Indicates that the stream has finished

#### Request with a persona

```bash
curl -X POST "http://127.0.0.1:8000/analyze/" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "SWOT analysis of Samsung Electronics",
    "stream": false,
    "persona_name": "Warren Buffett"
  }'
```

### 2. Persona Creation API

```bash
curl -X POST "http://127.0.0.1:8000/persona/" \
  -H "Content-Type: application/json" \
  -d '{
    "info": "Warren Buffett"
  }'
```

## Persona System

This project includes a persona system that changes the tone and style of the analysis.

- `persona/make_persona.py`
  Creates a persona from input figure information using OpenAI + web search.
- `persona/persona_loader.py`
  Loads personas from `persona.jsonl`.
- `gradio_app.py`
  Lets users select an existing persona from a dropdown or create a new one.

When a persona is applied, the following elements are added to the analysis prompt.

- Summary of the figure
- Financial mindset
- Data analysis style
- Response style
- Core principles
- Representative quotes

## Data Sources

- OpenAI API
  Used for intent parsing, analysis generation, and persona generation
- `yfinance`
  Used for price, fundamentals, stock news, financial data, and earnings data
- Google News RSS
  Used to search for recent news
- `trafilatura`
  Used to extract article bodies

## Result Storage

### Analysis result logs

`pipeline.py` stores analysis results in JSONL format.

- Default in code: `log_file.jsonl`
- Example in `.env.example`: `analysis_results.jsonl`
- Configurable via the `LOG_FILE` environment variable

Stored fields:

- `timestamp`
- `query`
- `ticker`
- `analysis_type`
- `data_context`
- `llm_response`

### Persona storage

Created personas are stored in JSONL format.

- Default file: `persona.jsonl`
- Can be changed via environment variable: `PERSONA_FILE`

Duplicate entries with the same `name` are skipped.

## License

See [LICENSE](LICENSE) for details.