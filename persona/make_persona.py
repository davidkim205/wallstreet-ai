import os
import json
import time
from datetime import datetime
from pathlib import Path

from openai import OpenAI

from dotenv import load_dotenv


from llm.generator import generate_persona, Persona

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


def _find_persona_image_path(name):
    persona_dir = Path(__file__).resolve().parent
    project_root = persona_dir.parent
    image_dir = persona_dir / "images"

    def _to_relative_path(path: Path) -> str:
        try:
            return path.relative_to(project_root).as_posix()
        except ValueError:
            return path.as_posix()

    normalized_name = name.strip().lower()

    if image_dir.exists() and image_dir.is_dir():
        for image_file in image_dir.iterdir():
            if not image_file.is_file():
                continue
            candidate_name = image_file.stem.replace("_", " ").strip().lower()
            if candidate_name == normalized_name:
                return _to_relative_path(image_file)

        # 일치하는 파일 없으면 general 지정
        fallback_path = image_dir / "general.png"
        if fallback_path.exists():
            return _to_relative_path(fallback_path)

    return ""


def save_persona_jsonl(persona, query, file_name=None):
    file_name = file_name or os.environ.get("PERSONA_FILE", "persona.jsonl")

    data = persona.model_dump() if hasattr(persona, "model_dump") else dict(persona)

    # persona.name과 이미지 파일명을 매핑하여 이미지 정보 저장
    name = data.get("full_name", "")
    if name:
        image_path = _find_persona_image_path(name)
        if image_path:
            data["image_path"] = image_path

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
    print(f"- 요약: {persona.summary}")
    print(f"- 금융 사고 방식: {persona.financial_mindset}")
    print(f"- 데이터 분석 방식: {persona.data_analysis_approach}")
    print(f"- 답변 스타일: {persona.response_style}")
    print(f"- 핵심 원칙: {', '.join(persona.key_principles)}", flush=True)
    if getattr(persona, "famous_quotes", None):
        print(f"- 어록: {', '.join(persona.famous_quotes)}", flush=True)


def search_exist_persona(query, file_name=None):
    file_name = file_name or os.environ.get("PERSONA_FILE", "persona.jsonl")
    if not os.path.exists(file_name):
        return None

    with open(file_name, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
                if data.get("query") == query:
                    return Persona.model_validate_json(line)
            except json.JSONDecodeError:
                pass
    return None


def make_persona(info):
    global client
    timings = {}
    start_total = time.time()
    
    retrieved_persona = search_exist_persona(info)
    if retrieved_persona:
        print("[캐시] 기존에 저장된 persona 정보가 있습니다. 해당 정보를 사용합니다.")
        print_persona(retrieved_persona)
        return retrieved_persona

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
