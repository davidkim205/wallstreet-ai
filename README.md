# wallstreet-ai

## 프로젝트 소개

**wallstreet-ai**는 최신 LLM(OpenAI)과 금융 데이터 API를 활용하여 주식 시장 분석, 실적 분석, 기술적/기본적 분석, 뉴스 요약 등 다양한 투자 인사이트를 자동으로 생성하는 AI 투자 분석 어시스턴트입니다.

---

## 설치 및 환경설정

1. Python 3.10+ 환경을 준비하세요.
2. 가상환경 생성 및 활성화:
   ```bash
   uv venv
   source .venv/bin/activate
   ```
3. 필수 패키지 설치:
   ```bash
   pip install -r requirements.txt
   ```
4. 환경 변수 파일 설정:
   ```bash
   cp .env.example .env
   # .env 파일에서 OpenAI 등 필요한 API 키를 입력하세요.
   ```

---

## 사용법

### 1. 파이프라인 실행

```bash
python pipeline.py
```

- 실행 후 프롬프트에 투자 관련 질의를 입력하면 AI가 자동으로 분석 리포트를 생성합니다.
- 예시 질의:
  - "AAPL의 최근 실적과 투자 포인트 요약해줘"
  - "TSLA의 기술적 분석 리포트 작성"
  - "삼성전자(005930.KS) SWOT 분석"

### 2. REST API 서버 실행 (FastAPI)

```bash
uvicorn api_server:app --reload
```

- REST API 엔드포인트: `/analyze`
- 예시 요청 (curl):
  ```bash
  curl -X POST "http://localhost:8000/analyze" \
    -H "Content-Type: application/json" \
    -d '{"query": "AAPL의 최근 실적과 투자 포인트 요약해줘"}'
  ```

- 예시 요청 (Python):
  ```python
  import requests
  resp = requests.post("http://localhost:8000/analyze", json={"query": "AAPL의 최근 실적과 투자 포인트 요약해줘"})
  print(resp.json())
  ```

- 서버가 실행 중일 때, 위와 같이 POST 요청을 보내면 분석 결과를 JSON 형태로 받을 수 있습니다.

