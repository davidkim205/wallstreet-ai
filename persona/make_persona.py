import os
import json
import time
from datetime import datetime

from openai import OpenAI

from dotenv import load_dotenv


from llm.generator import generate_persona

load_dotenv()


api_key = os.environ.get("LLM_MODEL_API_KEY")
if api_key:
    client = OpenAI(api_key=api_key)
else:
    client = OpenAI()



def timed(timings, key):
    class _Timer:
        def __enter__(self):
            self._t = time.time()
        def __exit__(self, *_):
            timings[key] = time.time() - self._t
    return _Timer()


def print_timings(timings):
    print("\n[소요시간(sec)]")
    for k, v in timings.items():
        print(f"  {k:20s}: {v:.2f}s")


def save_persona_jsonl(persona, query, file_name=None):
    file_name = file_name or os.environ.get("PERSONA_FILE", "persona.jsonl")

    data = persona.model_dump() if hasattr(persona, "model_dump") else dict(persona)

    # 중복 이름 체크
    name = data.get("name", "")
    if name and os.path.exists(file_name):
        with open(file_name, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    if json.loads(line).get("name") == name:
                        return
                except json.JSONDecodeError:
                    pass

    record = {
        "saved_at": datetime.now().isoformat(timespec="seconds"),
        "query": query,
        **data
    }

    with open(file_name, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def print_persona(persona):
    print("\n[Persona 생성 결과]")
    print(f"- 이름: {persona.name}")
    print(f"- 배경: {persona.background}")
    print(f"- 금융 사고 방식: {persona.financial_mindset}")
    print(f"- 데이터 분석 방식: {persona.data_analysis_approach}")
    print(f"- 답변 스타일: {persona.response_style}")
    print(f"- 핵심 원칙: {', '.join(persona.key_principles)}")
    if getattr(persona, "famous_quotes", None):
        print(f"- 어록: {', '.join(persona.famous_quotes)}")


def make_persona(info):
    global client
    timings = {}
    start_total = time.time()

    with timed(timings, "persona_generate"):
        persona = generate_persona(client, info)

    if persona is None:
        print("[실패] persona 생성 실패")
        return None

    with timed(timings, "persona_save"):
        save_persona_jsonl(persona, info)

    timings["total"] = time.time() - start_total

    print_persona(persona)
    print_timings(timings)

    return persona


def main():
    while True:
        text = input("\n원하는 인물을 입력 > ")
        if text.lower() in ("exit", "quit", "종료"):
            break
        if not text:
            continue        
        persona = make_persona(text)
        if persona:
            print("\n[완료] Persona 생성 완료 ✓")


if __name__ == "__main__":
    main()