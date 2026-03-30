# Wallstreet-AI

Wallstreet-AI는 투자 대가 페르소나와 Agentic AI 파이프라인을 결합해 가격, 펀더멘털, 실적, 뉴스, 기술지표를 해석하고 맞춤형 투자 인사이트를 제공하는 금융 분석 어시스턴트입니다.

## Overview

Wallstreet-AI는 사용자의 투자 질문을 실제 분석 파이프라인으로 전환하는 Agentic AI 기반 금융 분석 어시스턴트입니다. LLM이 질문에서 종목, 분석 유형, 기간, 실적 대상 연도·분기를 추출하면, 그 결과에 맞춰 가격 데이터, 펀더멘털, 기술지표, 실적 정보, 뉴스 컨텍스트를 선택적으로 수집하고 하나의 분석 입력으로 조립합니다.

이후 OpenAI 기반 모델이 수집된 데이터를 바탕으로 일반 분석, 기술적 분석, 기본적 분석, 실적 분석, SWOT, 뉴스 요약, 비교 분석 등의 리포트를 생성합니다. 또한 워런 버핏 같은 금융 인물의 투자 철학을 반영한 페르소나를 적용할 수 있어, 같은 데이터라도 관점과 설명 방식이 달라지는 맞춤형 응답을 제공합니다.

프로젝트는 단순 질의응답형 챗봇이 아니라 `인텐트 파싱 -> 도구 라우팅 -> 데이터 수집 -> 뉴스 보강 -> 컨텍스트 빌드 -> LLM 생성 -> 결과 저장/스트리밍`으로 이어지는 분석 시스템으로 설계되어 있습니다. CLI, FastAPI SSE API, Gradio UI를 함께 제공해 로컬 실험, 웹 인터페이스, 서비스 연동까지 동일한 코어 파이프라인을 재사용할 수 있습니다.

프로젝트는 세 가지 진입점을 제공합니다.

- `pipeline.py`: CLI 기반 대화형 분석
- `api_server.py`: FastAPI 분석 API 및 SSE 스트리밍 서버
- `gradio_app.py`: Gradio 웹 UI

## 주요 기능

- 사용자 질문에서 티커, 분석 유형, 기간, 연도/분기 정보를 추출
- 분석 유형별로 필요한 데이터 소스를 자동 선택
- `yfinance` 기반 가격, 펀더멘털, 뉴스, 실적 데이터 수집
- 기술적 지표(RSI, MACD, 이동평균, 볼린저 밴드) 계산
- Google News RSS 기반 뉴스 본문 수집 및 요약 컨텍스트 생성
- OpenAI Responses API를 통한 분석 리포트 생성 및 스트리밍 출력
- 투자 대가 스타일의 페르소나 생성 및 적용
- 분석 결과와 페르소나를 JSONL 파일로 저장

## 전체 동작 흐름

```text
사용자 질문
  -> 인텐트 파싱
  -> 도구 라우팅
  -> 시장 데이터 수집
  -> 뉴스 컨텍스트 생성
  -> LLM 분석 생성
  -> 결과 저장(JSONL)
```
## Demo
주요 스크린샷

## 빠른 실행

로컬에서 가장 쉽게 확인하는 방법은 아래 순서입니다.
1. 가상환경 생성

```bash
uv venv
source .venv/bin/activate
```

2. 의존성 설치

```bash
uv pip install -r requirements.txt
```

3. 환경 변수 파일 준비

```bash
cp .env.example .env
```

4. FastAPI 서버 실행

```bash
uvicorn api_server:app --reload
```

5. Gradio UI 실행

```bash
python gradio_app.py --api-url http://127.0.0.1:8000/analyze/
```

6. 브라우저에서 UI에 접속해 질문 입력

CLI만 빠르게 확인하려면 `.env` 설정 후 `python pipeline.py`만 실행해도 됩니다.


## 설치

### 1. Python 환경 준비

Python 3.10 이상을 권장합니다.

```bash
uv venv
source .venv/bin/activate
```

### 2. 의존성 설치

```bash
uv pip install -r requirements.txt
```

### 3. 환경 변수 설정

```bash
cp .env.example .env
```

기본 예시는 아래와 같습니다.

```env
LLM_MODEL_NAME=gpt-5-mini
LLM_MODEL_API_KEY=<your api key>
LOG_FILE=analysis_results.jsonl
PERSONA_FILE=persona.jsonl
```

설명:

- `LLM_MODEL_NAME`: OpenAI 호출에 사용할 모델명
- `LLM_MODEL_API_KEY`: OpenAI API 키
- `LOG_FILE`: 분석 결과 JSONL 저장 경로
- `PERSONA_FILE`: 페르소나 JSONL 저장 경로

현재 코드 기준으로 OpenAI 클라이언트 인증에는 `LLM_MODEL_API_KEY`를 우선 사용하며, 값이 비어 있으면 OpenAI SDK의 기본 인증 환경을 사용합니다.

## 실행 방법

### 1. CLI 실행

```bash
python pipeline.py
```

동작:

- 시작 시 저장된 페르소나 목록을 보여줍니다.
- 원하는 페르소나 번호를 입력하거나 Enter로 기본 모드로 진행합니다.
- 이후 질문을 입력하면 분석 결과와 수집 데이터 요약이 출력됩니다.

종료 명령:

- `exit`
- `quit`
- `종료`

### 2. FastAPI 서버 실행

```bash
uvicorn api_server:app --reload
```

기본 주소:

- API 서버: `http://127.0.0.1:8000`

제공 엔드포인트:

- `POST /analyze/`: 분석 실행
- `POST /persona/`: 새 페르소나 생성

### 3. Gradio UI 실행

먼저 FastAPI 서버를 실행한 뒤:

```bash
python gradio_app.py --api-url http://127.0.0.1:8000/analyze/
```

기본 주소:

- Gradio UI: `http://0.0.0.0:7860`

옵션:

- `--api-url`: 연결할 분석 SSE 엔드포인트. 코드 기본값은 `http://0.0.0.0:8000/analyze/`이지만, 로컬 접속 시에는 `http://127.0.0.1:8000/analyze/`처럼 명시해서 실행하는 편이 안전합니다.
- `--port`: Gradio 포트
- `--server-name`: 바인딩 주소
- `--share`: 공개 링크 생성

Gradio UI에는 두 개의 탭이 있습니다.

- `질문하기`: 분석 요청, 스트리밍 응답, 진행 메타데이터 표시
- `페르소나 만들기`: 금융 인물 기반 페르소나 생성 및 저장

## API 사용 예시

### 1. 분석 API

```bash
curl -X POST "http://127.0.0.1:8000/analyze/" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "AAPL의 최근 실적과 투자 포인트 요약해줘",
    "stream": false
  }'
```

응답 예시:

```json
{
  "type": "result",
  "query": "AAPL의 최근 실적과 투자 포인트 요약해줘",
  "ticker": "AAPL",
  "analysis_type": "earnings",
  "data_context": {},
  "llm_response": "...",
  "timestamp": "2026-03-30 10:00:00",
  "stdout": "..."
}
```

#### 페르소나 지정 요청

```bash
curl -X POST "http://127.0.0.1:8000/analyze/" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "삼성전자 SWOT 분석",
    "stream": false,
    "persona_name": "워렌 버핏"
  }'
```

### 2. 페르소나 생성 API

```bash
curl -X POST "http://127.0.0.1:8000/persona/" \
  -H "Content-Type: application/json" \
  -d '{
    "info": "워렌 버핏"
  }'
```

## 페르소나 시스템

이 프로젝트는 분석 스타일을 바꿀 수 있는 페르소나 기능을 제공합니다.

- `persona/make_persona.py`
  입력된 인물 정보를 바탕으로 OpenAI + 웹 검색을 통해 페르소나를 생성합니다.
- `persona/persona_loader.py`
  `persona.jsonl`에서 페르소나를 읽어옵니다.
- `gradio_app.py`
  드롭다운으로 기존 페르소나를 선택하거나 새로 생성할 수 있습니다.

페르소나가 적용되면 다음 요소가 분석 프롬프트에 추가됩니다.

- 인물 배경
- 금융 사고방식
- 데이터 분석 방식
- 답변 스타일
- 핵심 원칙
- 대표 어록

## 데이터 소스

- OpenAI API
  인텐트 파싱, 분석 생성, 페르소나 생성
- `yfinance`
  가격, 펀더멘털, 종목 뉴스, 재무/실적 데이터
- Google News RSS
  최신 뉴스 검색
- `trafilatura`
  기사 본문 추출

## 결과 저장

### 분석 결과 로그

`pipeline.py`는 분석 결과를 JSONL 형식으로 저장합니다.

- 코드 내 기본값: `log_file.jsonl`
- `.env.example` 예시값: `analysis_results.jsonl`
- 환경 변수 `LOG_FILE`로 변경 가능

저장 항목:

- `timestamp`
- `query`
- `ticker`
- `analysis_type`
- `data_context`
- `llm_response`

### 페르소나 저장

생성한 페르소나는 JSONL 형식으로 저장됩니다.

- 기본 파일: `persona.jsonl`
- 환경 변수로 변경 가능: `PERSONA_FILE`

동일한 `name`이 이미 있으면 중복 저장하지 않습니다.

## 라이선스

이 저장소는 [LICENSE](LICENSE) 파일을 따릅니다.
