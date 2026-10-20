"""Translate one recipe into es-MX, using the names the vocabulary already settled on.

The model gets the resolved ingredient and equipment names and the metric amounts as exact hints, because the app
matches on those words and does its own arithmetic better than a model does. Code checks the result afterwards.
"""

import json
import re
from dataclasses import dataclass, field

from recetario_ingest.llm.adapter import LlmAdapter, Usage
from recetario_ingest.llm.prompts import Prompt
from recetario_ingest.text import normalize

PROMPT_ID = "localize"

LOCALIZED_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "description": {"type": "string"},
        "ingredient_lines": {"type": "array", "items": {"type": "string"}},
        "steps": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["title", "description", "ingredient_lines", "steps"],
    "additionalProperties": False,
}

LOCALIZE_V1 = Prompt(
    id=PROMPT_ID,
    version="v1",
    system="""You translate one recipe from English into Mexican Spanish (es-MX) for a cook in Mexico.

You get the title, description, ingredient lines and steps, plus hints: for each ingredient line the es-MX name of the ingredient (the app's vocabulary) and sometimes a metric amount; and the es-MX names of the equipment.

Rules:
- Natural cooking Spanish, second person singular imperative for steps ("Precalienta el horno", "Mezcla"). No Spain-only words.
- Use the hinted ingredient and equipment names exactly, in every line and step that mentions them ("jitomate", "horno"). Without a hint, use the word a Mexican market uses.
- Keep the quantities. When a line has an "amount" hint, write that amount instead of the pounds or ounces in the original, and never convert anything yourself. Cups, tablespoons and teaspoons stay: taza, cucharada, cucharadita (cda., cdita.), pizca, diente de ajo, lata, paquete.
- Temperatures only in °C. If the text gives both, keep the °C one. If it gives only °F, leave the number as written: code converts it afterwards.
- Return exactly as many ingredient lines and steps as you were given, in the same order. Do not add, merge, drop or reword steps beyond translating them.
- Keep brand and product names. An empty description stays empty.""",
    template="{text}",
)


LOCALIZE_V2 = Prompt(
    id=PROMPT_ID,
    version="v2",
    system="""You translate one recipe from English into Mexican Spanish (es-MX) for a cook in Mexico.

You get the title, description, ingredient lines and steps, plus hints: for each ingredient line the es-MX name of the ingredient (the app's vocabulary) and sometimes a metric amount; and the es-MX names of the equipment.

Rules:
- Natural cooking Spanish, second person singular imperative for steps ("Precalienta el horno", "Mezcla"). No Spain-only words.
- Use the hinted ingredient and equipment names exactly, in every line and step that mentions them ("jitomate", "horno"). Without a hint, use the word a Mexican market uses.
- Keep the quantities. When a line has an "amount" hint, write that amount instead of the pounds or ounces in the original, and never convert anything yourself. Cups, tablespoons and teaspoons stay: taza, cucharada, cucharadita (cda., cdita.), pizca, diente de ajo, lata, paquete.
- Temperatures only in °C. If the text gives both, keep the °C one. If it gives only °F, leave the number as written: code converts it afterwards.
- Return exactly as many ingredient lines and steps as you were given, in the same order. Do not add, merge, drop or reword steps beyond translating them.
- The bracketed hints after an ingredient line are for you only: never copy a bracket, a hint or an "es-MX name" into your answer.
- Keep brand and product names. An empty description stays empty.""",
    template="{text}",
)

_FAHRENHEIT = re.compile(r"(\d+(?:\.\d+)?)\s*(?:°|º|degrees?)\s*F\b", re.IGNORECASE)
_HINT_ECHO = re.compile(r"\s*\[[^\]]*\]\s*$")
_NUMBER = re.compile(r"\d+(?:[.,/]\d+)?")


@dataclass(frozen=True)
class Issue:
    severity: str  # error | warning
    rule: str
    detail: str


@dataclass
class Localization:
    translated: dict | None
    issues: list[Issue]
    attempts: int
    usage: Usage = field(default_factory=Usage)

    @property
    def status(self) -> str:
        return "needs_review" if self.translated is None or any(i.severity == "error" for i in self.issues) else "ok"


def metric_amount(qty: float | None, unit: str | None) -> str | None:
    """Pounds and ounces as grams, rounded the way a person would write them; None for any other unit."""
    if qty is None or unit not in ("lb", "oz"):
        return None
    grams = qty * (453.59 if unit == "lb" else 28.35)
    step = 5 if grams >= 100 else 1
    return f"{round(grams / step) * step:g} g"


def celsius(match: re.Match) -> str:
    degrees = (float(match.group(1)) - 32) * 5 / 9
    return f"{round(degrees / 5) * 5:g}°C"


def strip_hint_echo(translated: str, original: str) -> str:
    """A model sometimes copies the hint brackets I gave it ("2 tazas de agua [agua]"); drop one unless the original had brackets."""
    return translated if "[" in original else _HINT_ECHO.sub("", translated)


def fahrenheit_to_celsius(text: str) -> str:
    return _FAHRENHEIT.sub(celsius, text)


def build_input(recipe: dict) -> str:
    """`recipe` carries title, description, steps, ingredient lines with hints, and equipment names (see stages.LocalizeStage)."""
    lines = [f"title: {recipe['title']}", f"description: {recipe['description'] or '(none)'}", "ingredient lines:"]
    for number, line in enumerate(recipe["ingredient_lines"], 1):
        hints = []
        if line["names"]:
            hints.append("es-MX name: " + ", ".join(line["names"]))
        if line["amount"]:
            hints.append("amount: " + line["amount"])
        lines.append(f"{number}. {line['text']}" + (f"  [{'; '.join(hints)}]" if hints else ""))
    lines.append("steps:")
    lines += [f"{number}. {step}" for number, step in enumerate(recipe["steps"], 1)]
    if recipe["equipment"]:
        lines.append("equipment es-MX names: " + "; ".join(f"{english} = {spanish}" for english, spanish in recipe["equipment"].items()))
    return "\n".join(lines)


def check(recipe: dict, translated: object) -> list[Issue]:
    if not isinstance(translated, dict):
        return [Issue("error", "bad_shape", "expected an object")]
    issues = []
    if not str(translated.get("title", "")).strip():
        issues.append(Issue("error", "missing_title", "the translated title is empty"))
    for key, source, label in (("ingredient_lines", recipe["ingredient_lines"], "ingredient lines"), ("steps", recipe["steps"], "steps")):
        got = translated.get(key)
        if not isinstance(got, list) or len(got) != len(source):
            issues.append(Issue("error", f"{key}_count", f"expected {len(source)} {label}, got {len(got) if isinstance(got, list) else 'none'}"))
    if issues:
        return issues

    for original, text in zip(recipe["ingredient_lines"], translated["ingredient_lines"]):
        shown = normalize(text)
        for name in original["names"]:
            if not all(_stem(word) in shown for word in normalize(name).split()):
                issues.append(Issue("warning", "name_not_used", f"{name!r} is not in {text!r}"))
        needed = set() if original["amount"] else {n for n in _NUMBER.findall(_without_lengths(original["text"]))}  # converted amounts change their numbers
        missing = sorted(n for n in needed if n not in text.replace(",", "."))
        if missing:
            issues.append(Issue("warning", "number_missing", f"{missing} from {original['text']!r} not in {text!r}"))
    return issues


def _without_lengths(text: str) -> str:
    """Inches and fluid ounces are fine to convert to cm and ml, so their numbers aren't expected to survive."""
    return re.sub(r"[\d/. ]+[- ]?(?:inch(?:es)?|in\.?|\"|”|fl\.? ?oz\.?|fluid oz\.?|fluid ounces?)", " ", text, flags=re.IGNORECASE)


def _stem(name: str) -> str:
    """Compare names without the plural: "papas" counts as "papa", "chiles" as "chile"."""
    return name[:-2] if name.endswith("es") and len(name) > 5 else name[:-1] if name.endswith("s") and len(name) > 3 else name


def localize_recipe(llm: LlmAdapter, version: str, recipe: dict) -> Localization:
    text = build_input(recipe)
    result = llm.structured(PROMPT_ID, version, text, LOCALIZED_SCHEMA)
    issues = check(recipe, result.data)
    usage, attempts = result.usage, 1

    errors = [issue for issue in issues if issue.severity == "error"]
    if errors:
        feedback = "\n".join(f"- {issue.rule}: {issue.detail}" for issue in errors)
        retry_input = f"{text}\n\n---\nYour previous answer was:\n{json.dumps(result.data, ensure_ascii=False)}\n\nIt had these problems. Answer again with them fixed:\n{feedback}"
        retry = llm.structured(PROMPT_ID, version, retry_input, LOCALIZED_SCHEMA)
        usage = Usage(usage.tokens_in + retry.usage.tokens_in, usage.tokens_out + retry.usage.tokens_out, usage.web_searches + retry.usage.web_searches, usage.cost_usd + retry.usage.cost_usd)
        result, issues, attempts = retry, check(recipe, retry.data), 2

    translated = result.data if isinstance(result.data, dict) else None
    if translated and all(isinstance(translated.get(k), list) for k in ("steps",)):
        translated = {**translated, "steps": [fahrenheit_to_celsius(step) for step in translated["steps"]]}
    if translated and isinstance(translated.get("ingredient_lines"), list) and len(translated["ingredient_lines"]) == len(recipe["ingredient_lines"]):
        translated = {**translated, "ingredient_lines": [strip_hint_echo(text, line["text"]) for text, line in zip(translated["ingredient_lines"], recipe["ingredient_lines"])]}
    return Localization(translated, issues, attempts, usage)
