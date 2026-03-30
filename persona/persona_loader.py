import json
import os
from pathlib import Path
from llm.generator import Persona

PERSONA_FILE = Path(os.environ.get("PERSONA_FILE", "persona.jsonl"))

def load_personas():
    personas = []
    if Path(PERSONA_FILE).exists():
        with PERSONA_FILE.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    data = json.loads(line)
                    # 호환성: background 필드가 있으면 summary로 매핑
                    if isinstance(data, dict) and "background" in data and "summary" not in data:
                        data["summary"] = data["background"]
                        # background 필드는 제거하지 않고 유지 (호환성)
                    # full_name 없으면 name 사용하여 채움
                    if isinstance(data, dict) and not data.get("full_name"):
                        data["full_name"] = data.get("name", "")
                    personas.append(Persona.model_validate(data))
    return personas

def get_persona(name):
    for p in load_personas():
        if p.name == name:
            return p
    return None