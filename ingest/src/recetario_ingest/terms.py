"""Turn raw names into the normalized terms the resolver works on, and collect a book's unique terms with examples."""

import re
from collections import defaultdict
from dataclasses import dataclass

from recetario_ingest import lines
from recetario_ingest.text import normalize

_TRADEMARKS = re.compile(r"[®™©*†‡]")  # also footnote marks: "bacon bits*"
_PARENTHETICAL = re.compile(r"\([^)]*\)")
_DIMENSIONS = re.compile(r"\b\d+(?:\.\d+)?\s*[”“\"']?\s*[x×]\s*\d+(?:\.\d+)?\s*[”“\"']?|\b\d+(?:\.\d+)?[- ]?(?:qt|quart|inch|in|cup)s?\b\.?")
_EQUIPMENT_FILLER = {"big", "large", "small", "medium", "heavy", "deep", "shallow", "preheated", "greased", "prepared", "extra", "sized"}
_NOT_PLURAL = {"asparagus", "hummus", "couscous", "molasses", "swiss", "citrus", "watercress", "cress", "grits", "oats", "bass", "lemongrass", "glass", "brass", "series"}
# state words that don't change what you buy (NAMING.md: one entry, the form is preparation); "dried" stays
_INGREDIENT_FILLER = {"fresh", "frozen", "freshly", "raw", "uncooked"}
_IRREGULAR_PLURALS = {"leaves": "leaf", "loaves": "loaf", "halves": "half", "knives": "knife"}
_ES_ENDINGS = ("ches", "shes", "sses", "xes", "zes")


def singular(word: str) -> str:
    """Conservative English singular: "potatoes" -> "potato", "cherries" -> "cherry", "glass" stays."""
    if word in _IRREGULAR_PLURALS:
        return _IRREGULAR_PLURALS[word]
    if word in _NOT_PLURAL or len(word) <= 3 or not word.endswith("s"):
        return word
    if word.endswith("ies") and len(word) > 4:
        return word[:-3] + "y"
    if word.endswith("oes"):
        return word[:-2]
    if word.endswith(_ES_ENDINGS):
        return word[:-2]
    if word.endswith(("ss", "us", "is")):
        return word
    return word[:-1]


def normalize_term(kind: str, text: str) -> str:
    """Lowercase, no accents, trademark signs, parentheticals or hyphens; equipment also loses sizes and state words.

    The last word is made singular, which is enough for names like "baking sheets" and "tomatoes".
    """
    cleaned = _PARENTHETICAL.sub(" ", _TRADEMARKS.sub("", text))
    if kind == "equipment":
        cleaned = _DIMENSIONS.sub(" ", cleaned)
    words = normalize(cleaned).replace("’", "'").replace("-", " ").strip(" .,;:\"'").split()
    if kind == "equipment":
        stripped = [word for word in words if word not in _EQUIPMENT_FILLER]
        words = stripped or words
    if kind == "ingredient":
        stripped = [word for word in words if word not in _INGREDIENT_FILLER]
        words = stripped or words
    if words:
        words[-1] = singular(words[-1])
    return " ".join(words)


@dataclass(frozen=True)
class TermExample:
    recipe: str
    text: str  # the step an equipment name appeared in, or "key: value" for an attribute key


def collect_terms(extractions: list[dict], splits: dict[str, list[str]] | None = None) -> dict[tuple[str, str], list[TermExample]]:
    """Unique (kind, normalized term) -> up to three examples, from extract artifacts, in a stable order.

    `splits` maps an ingredient line the rules couldn't read to the names the model found in it; without an
    entry such a line adds no ingredient term.
    """
    splits = splits or {}
    found: dict[tuple[str, str], list[TermExample]] = defaultdict(list)

    def add(kind: str, raw: str, example: TermExample) -> None:
        term = normalize_term(kind, raw)
        if term and len(found[(kind, term)]) < 3:
            found[(kind, term)].append(example)

    for item in extractions:
        data = item["extracted"]
        if not data:
            continue
        title = data["title"]
        for raw in data["ingredient_lines"]:
            parsed = lines.parse_line(raw)
            names = splits.get(raw, []) if parsed.needs_model else [parsed.name]
            for name in names:
                add("ingredient", name, TermExample(title, raw))
        for mention in data["equipment_mentions"]:
            steps = data["steps"]
            index = mention["step_index"]
            step = steps[index] if isinstance(index, int) and 0 <= index < len(steps) else ""
            add("equipment", mention["text"], TermExample(title, step))
        for group in ("meta", "extras"):
            for pair in data[group]:
                add("attribute_key", pair["key_text"], TermExample(title, f"{pair['key_text']}: {pair['value_text']}"))
    return dict(sorted(found.items()))
