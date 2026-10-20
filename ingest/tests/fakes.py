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
