"""Parse an ingredient line into quantity, unit, name and preparation using rules only.

A line the rules can't settle (an "or" or "and" inside the name, a leftover bracket, no name at all) comes back with
`needs_model` set, so a later step can ask the model about just those lines.
"""

import re
from dataclasses import asdict, dataclass, field
from fractions import Fraction

_UNICODE_FRACTIONS = {"½": "1/2", "⅓": "1/3", "⅔": "2/3", "¼": "1/4", "¾": "3/4", "⅛": "1/8", "⅜": "3/8", "⅝": "5/8", "⅞": "7/8"}

# canonical unit -> spellings, all lower case and without the trailing dot
_UNITS = {
    "tsp": ("tsp", "tsps", "teaspoon", "teaspoons"),
    "tbsp": ("tbsp", "tbsps", "tablespoon", "tablespoons"),
    "cup": ("cup", "cups"),
    "oz": ("oz", "ounce", "ounces"),
    "lb": ("lb", "lbs", "pound", "pounds"),
    "g": ("g", "gram", "grams"),
    "kg": ("kg", "kilogram", "kilograms"),
    "ml": ("ml", "milliliter", "milliliters"),
    "l": ("l", "liter", "liters"),
    "quart": ("quart", "quarts", "qt", "qts"),
    "pint": ("pint", "pints", "pt"),
    "gallon": ("gallon", "gallons"),
    "fl oz": ("fl oz", "fluid ounce", "fluid ounces"),
    "pinch": ("pinch", "pinches"),
    "dash": ("dash", "dashes"),
    "clove": ("clove", "cloves"),
    "can": ("can", "cans"),
    "package": ("package", "packages", "pkg", "pkgs"),
    "slice": ("slice", "slices"),
    "head": ("head", "heads"),
    "bunch": ("bunch", "bunches"),
    "stalk": ("stalk", "stalks"),
    "sprig": ("sprig", "sprigs"),
    "jar": ("jar", "jars"),
    "bottle": ("bottle", "bottles"),
    "bag": ("bag", "bags"),
    "box": ("box", "boxes"),
    "container": ("container", "containers"),
    "stick": ("stick", "sticks"),
    "piece": ("piece", "pieces"),
    "handful": ("handful", "handfuls"),
    "drop": ("drop", "drops"),
    "cube": ("cube", "cubes"),
    "ear": ("ear", "ears"),
    "fillet": ("fillet", "fillets"),
    "strip": ("strip", "strips"),
    "sheet": ("sheet", "sheets"),
    "dollop": ("dollop", "dollops"),
    "splash": ("splash", "splashes"),
    "log": ("log", "logs"),
    "sleeve": ("sleeve", "sleeves"),
}
_UNIT_BY_SPELLING = {spelling: unit for unit, spellings in _UNITS.items() for spelling in spellings}

_SIZE_WORDS = {"small", "medium", "large", "extra-large", "extra large", "big", "whole", "heaping", "scant", "level", "generous"}
# words that describe how the ingredient is cut or treated; at the front of the name they belong to the preparation
_PREPARATION_WORDS = {
    "chopped", "minced", "diced", "sliced", "grated", "shredded", "crushed", "peeled", "cubed", "mashed", "melted",
    "softened", "beaten", "cooked", "drained", "rinsed", "packed", "crumbled", "halved", "toasted", "thawed", "trimmed",
    "julienned", "juiced", "zested", "firmly", "finely", "coarsely", "roughly", "thinly", "thickly", "freshly",
    "lightly", "uncooked", "unpeeled", "seeded", "pitted", "cored", "divided", "sifted", "squeezed", "torn", "quartered",
}
_NOISE_TAILS = (
    r"or\s+(?:(?:more|less)\s+)?(?:as\s+)?(?:to\s+taste|needed|desired|required)",
    r"or\s+(?:more|less)",
    r"as\s+needed",
    r"to\s+taste",
    r"to\s+cover",
    r"for\s+(?:garnish|serving|frying|dusting|greasing|sprinkling|topping|drizzling|brushing|decoration)",
)
_NOISE = re.compile(r"\s*,?\s*\b(?:" + "|".join(_NOISE_TAILS) + r")\b.*$", re.IGNORECASE)
_NUMBER = r"(?:\d+\s+\d+/\d+|\d+/\d+|\d+(?:\.\d+)?)"
_QUANTITY = re.compile(rf"^(?P<low>{_NUMBER})(?:\s*(?:-|–|to)\s*(?P<high>{_NUMBER}))?\s+")
_CONTAINER = re.compile(r"^\((?P<size>[^()]*?)\)\s*")
_TRADEMARK = re.compile(r"[®™©]")
_PARENTHESES = re.compile(r"\s*\(([^()]*)\)?")
_SPLIT = re.compile(r",|\s+-\s+|\s+–\s+|;")


@dataclass
class ParsedLine:
    raw: str
    qty: float | None = None
    qty_max: float | None = None
    unit: str | None = None
    container: str | None = None  # "12 oz" for "1 (12 oz.) can ..."
    name: str = ""
    preparation: str | None = None
    optional: bool = False
    to_taste: bool = False
    notes: list[str] = field(default_factory=list)  # bracketed text that isn't "optional"
    needs_model: str | None = None  # why the rules gave up, or None

    def as_json(self) -> dict:
        return asdict(self)


def parse_quantity(text: str) -> float:
    whole, _, fraction = text.rpartition(" ")
    return float(Fraction(whole or "0") + Fraction(fraction)) if "/" in text else float(text)


def parse_line(raw: str) -> ParsedLine:
    parsed = ParsedLine(raw=raw)
    text = _clean(raw)

    quantity = _QUANTITY.match(text)
    if quantity:
        parsed.qty = parse_quantity(quantity["low"])
        if quantity["high"]:
            parsed.qty_max = parse_quantity(quantity["high"])
        text = text[quantity.end() :]

    container = _CONTAINER.match(text)
    if container:
        parsed.container = _tidy(container["size"])
        text = text[container.end() :]

    text, parsed.unit = _take_unit(_skip_size_before_unit(text))

    text, brackets = _take_brackets(text)
    for bracket in brackets:
        if re.fullmatch(r"optional", bracket, re.IGNORECASE):
            parsed.optional = True
        elif bracket:
            parsed.notes.append(bracket)

    noise = _NOISE.search(text)
    if noise:
        parsed.to_taste = True
        text = text[: noise.start()]

    head, _, tail = _split_preparation(text)
    parsed.name, leading = _split_name(head)
    preparation = ", ".join(part for part in (leading, tail) if part)
    parsed.preparation = preparation or None

    if re.search(r"\boptional\b", parsed.preparation or "", re.IGNORECASE):
        parsed.optional = True
        parsed.preparation = re.sub(r"[,;\s]*\boptional\b[,;\s]*", " ", parsed.preparation, flags=re.IGNORECASE).strip(" ,;") or None

    parsed.needs_model = _why_model(parsed)
    return parsed


def _clean(raw: str) -> str:
    text = _TRADEMARK.sub("", raw)
    for symbol, replacement in _UNICODE_FRACTIONS.items():
        text = re.sub(rf"(\d)\s*{symbol}", rf"\1 {replacement}", text).replace(symbol, replacement)
    text = re.sub(r"\(\s+", "(", text)
    text = re.sub(r"\s+\)", ")", text)
    return " ".join(text.split())


def _tidy(text: str) -> str:
    return " ".join(text.replace(".", "").split())


def _skip_size_before_unit(text: str) -> str:
    """"large head cauliflower" -> "head cauliflower": a size word in front of a unit would hide the unit."""
    word, _, rest = text.partition(" ")
    if word.lower() in _SIZE_WORDS and _take_unit(rest)[1]:
        return rest
    return text


def _take_unit(text: str) -> tuple[str, str | None]:
    """Remove a leading unit; "fl oz" and "fluid ounces" are two words, everything else is one."""
    lowered = text.lower()
    for spelling in sorted((s for s in _UNIT_BY_SPELLING if " " in s), key=len, reverse=True):
        if lowered.startswith(spelling + " ") or lowered.startswith(spelling + ". "):
            return text[len(spelling) :].lstrip(". "), _UNIT_BY_SPELLING[spelling]
    word, _, rest = text.partition(" ")
    unit = _UNIT_BY_SPELLING.get(word.lower().rstrip("."))
    if unit and rest:
        return rest.removeprefix("of ").strip(), unit
    return text, None


def _take_brackets(text: str) -> tuple[str, list[str]]:
    """Pull out bracketed text; an unclosed bracket takes the rest of the line (the book truncates some lines)."""
    found = [match.strip() for match in _PARENTHESES.findall(text)]
    return _PARENTHESES.sub("", text).strip(), found


def _split_preparation(text: str) -> tuple[str, str, str]:
    match = _SPLIT.search(text)
    if not match:
        return text.strip(), "", ""
    return text[: match.start()].strip(), match.group(0), text[match.end() :].strip(" ,;")


def _split_name(head: str) -> tuple[str, str]:
    """Drop size words and move leading preparation words ("finely chopped fresh basil") out of the name."""
    words = head.split()
    leading = []
    while words and (words[0].lower() in _SIZE_WORDS or words[0].lower() in _PREPARATION_WORDS):
        word = words.pop(0)
        if word.lower() not in _SIZE_WORDS:
            leading.append(word)
    if words and words[0].lower() == "of":
        words.pop(0)
    return " ".join(words), " ".join(leading)


def _why_model(parsed: ParsedLine) -> str | None:
    if not parsed.name:
        return "no name"
    if re.search(r"(?:^|\s)(?:or|and|and/or)\s", parsed.name, re.IGNORECASE) or "/" in parsed.name:
        return "alternatives or a combination in the name"
    if parsed.name != parsed.name.strip(" .:") or re.search(r"[()\[\]]", parsed.name):
        return "stray punctuation in the name"
    return None
