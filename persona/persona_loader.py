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