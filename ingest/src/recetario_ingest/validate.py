"""Code-based checks on an extracted recipe. They compare the model's output with the segment text it came from.

An `error` means the output is wrong enough to retry; a `warning` is kept on the recipe for review.
"""

import re
from dataclasses import dataclass

from recetario_ingest.text import normalize

_NON_WORD = re.compile(r"[^0-9a-z]+")
_NUMBER = re.compile(r"\d+(?:[./]\d+)?")
_TIME_KEY = re.compile(r"prep|cook|ready|total|time|bake|chill|rest|marinat", re.IGNORECASE)
_DURATION_PART = re.compile(r"(\d+)\s*(h(?:ours?|rs?)?|m(?:in(?:ute)?s?)?)(?![a-z])", re.IGNORECASE)


@dataclass(frozen=True)
class Issue:
    severity: str  # "error" | "warning"
    rule: str
    detail: str


def squash(text: str) -> str:
    """Compare text ignoring case, accents and punctuation: "1/2 tsp." -> "1 2 tsp"."""
    return _NON_WORD.sub(" ", normalize(text)).strip()


def parse_duration_minutes(text: str) -> int | None:
    """"1h5m" -> 65, "25 minutes" -> 25, "2 hrs" -> 120; None when the text isn't a duration."""
    parts = _DURATION_PART.findall(text)
    if not parts or _DURATION_PART.sub("", text).strip(" ,.&and"):
        return None
    return sum(int(number) * (60 if unit[0].lower() == "h" else 1) for number, unit in parts)


def validate(extracted: object, source_text: str) -> list[Issue]:
    if not isinstance(extracted, dict):
        return [Issue("error", "bad_shape", f"expected an object, got {type(extracted).__name__}")]

    issues: list[Issue] = []
    source = squash(source_text)
    source_numbers = set(_NUMBER.findall(source_text))
    ingredients = _list(extracted, "ingredient_lines")
    steps = _list(extracted, "steps")

    if not str(extracted.get("title", "")).strip():
        issues.append(Issue("error", "missing_title", "the title is empty"))
    if not ingredients:
        issues.append(Issue("error", "no_ingredients", "there are no ingredient lines"))
    if not steps:
        issues.append(Issue("error", "no_steps", "there are no steps"))

    for line in ingredients:
        if squash(str(line)) not in source:
            issues.append(Issue("error", "ingredient_not_in_source", f"not found in the text: {line!r}"))
        missing = [number for number in _NUMBER.findall(str(line)) if number not in source_numbers]
        if missing:
            issues.append(Issue("error", "quantity_not_in_source", f"{', '.join(missing)} is not in the text (line {line!r})"))

    for step in steps:
        if squash(str(step)) not in source:
            issues.append(Issue("warning", "step_not_in_source", f"not found in the text: {str(step)[:60]!r}"))

    for entry in _list(extracted, "meta"):
        key, value = str(entry.get("key_text", "")), str(entry.get("value_text", ""))
        if _TIME_KEY.search(key) and parse_duration_minutes(value) is None:
            issues.append(Issue("error", "time_not_parsed", f"{key}: {value!r} is not a duration"))
        if squash(value) not in source:
            issues.append(Issue("warning", "meta_not_in_source", f"{key}: {value!r} is not in the text"))

    for entry in _list(extracted, "extras"):
        if squash(f"{entry.get('key_text', '')} {entry.get('value_text', '')}") not in source:
            issues.append(Issue("warning", "extra_not_in_source", f"{entry.get('key_text')}: {entry.get('value_text')!r}"))

    for mention in _list(extracted, "equipment_mentions"):
        index, name = mention.get("step_index"), str(mention.get("text", ""))
        if not isinstance(index, int) or not 0 <= index < len(steps):
            issues.append(Issue("error", "equipment_step_out_of_range", f"{name!r} points at step {index} of {len(steps)}"))
        elif not mention.get("listed") and squash(name) not in squash(str(steps[index])):
            issues.append(Issue("warning", "equipment_not_in_step", f"{name!r} is not in step {index}"))
    return issues


def _list(data: dict, key: str) -> list:
    value = data.get(key)
    return [item for item in value if isinstance(item, (str, dict))] if isinstance(value, list) else []
