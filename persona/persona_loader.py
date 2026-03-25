import json
from pathlib import Path
from llm.generator import Persona

PERSONA_FILE = Path("persona.jsonl")

def load_personas():
    personas = []
    with PERSONA_FILE.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                personas.append(Persona.model_validate_json(line))
    return personas

def get_persona(name):
    for p in load_personas():
        if p.name == name:
            return p
    return None