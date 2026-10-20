"""A stand-in for a perfect model: reads the labelled sections of a segment text and returns them as the schema asks."""

import re


def _lines(text, start, stops):
    lines = text.splitlines()
    begin = next(i for i, line in enumerate(lines) if line == start) + 1
    end = next((i for i, line in enumerate(lines) if i >= begin and line in stops), len(lines))
    return [re.sub(r"^•\s*", "", line).strip() for line in lines[begin:end]]


def perfect_extraction(text: str) -> dict:
    lines = text.splitlines()
    nutrition = _lines(text, "Nutrition Information", ())
    return {
        "title": re.sub(r"^\d+\.\s*", "", lines[0]),
        "description": lines[1].strip('"') if lines[1].startswith('"') else "",
        "meta": [
            {"key_text": key.strip(), "value_text": value.strip()}
            for key, value in (part.split(":", 1) for part in next(l for l in lines if l.startswith("Serving")).split("|"))
        ],
        "ingredient_lines": _lines(text, "Ingredients", ("Direction",)),
        "steps": _lines(text, "Direction", ("Nutrition Information",)),
        "equipment_mentions": [],
        "extras": [{"key_text": key, "value_text": value.strip()} for key, value in (n.split(":", 1) for n in nutrition)],
    }


class ResolveScript:
    """Stands in for the model during resolution: returns queued answers and keeps the inputs it was shown."""

    model = "scripted"

    def __init__(self, *answers):
        self.answers, self.inputs = list(answers), []

    def structured(self, prompt_id, version, input, schema, tools=()):
        from recetario_ingest.llm.adapter import LlmResult, Usage

        assert prompt_id == "resolve"
        self.inputs.append(input)
        return LlmResult(self.answers.pop(0), Usage(tokens_in=50, tokens_out=20, cost_usd=0.001))


def map_answer(target, confidence=0.95, reason="same thing"):
    return {"action": "map_to", "target": target, "new_entry": _blank(), "confidence": confidence, "reason": reason}


def new_answer(name_en, name_es_mx, confidence=0.8, parent_en="", value_type="", unit=""):
    entry = {"name_en": name_en, "name_es_mx": name_es_mx, "parent_en": parent_en, "value_type": value_type, "unit": unit}
    return {"action": "new_entry", "target": "", "new_entry": entry, "confidence": confidence, "reason": "no candidate fits"}


def _blank():
    return {"name_en": "", "name_es_mx": "", "parent_en": "", "value_type": "", "unit": ""}
