"""Split the few ingredient lines the rules can't ("salt and pepper", "butter or margarine") into separate names."""

from recetario_ingest.llm.adapter import LlmAdapter, Usage
from recetario_ingest.llm.prompts import Prompt

PROMPT_ID = "split_line"

SPLIT_SCHEMA = {
    "type": "object",
    "properties": {
        "parts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "relation": {"type": "string", "enum": ["first", "and", "or"]},
                },
                "required": ["name", "relation"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["parts"],
    "additionalProperties": False,
}

SPLIT_V1 = Prompt(
    id=PROMPT_ID,
    version="v1",
    system="""You get one ingredient line from a cookbook that names more than one ingredient, or that the rules could not read. Return the ingredients it names.

- parts: one item per ingredient, in the order written. name is the ingredient alone: no quantity, unit, size, brand or preparation, but keep words that change what it is ("ground black pepper", "kosher salt", "sea salt").
- relation says how a part relates to the one before it: "first" for the first part, "and" when the cook needs both ("salt and pepper"), "or" when either will do ("butter or margarine").
- A line with a single ingredient returns one part, "first". Never invent an ingredient the line does not name. If the line says "to taste", that is not an ingredient.""",
    template="{text}",
)


def split_line(llm: LlmAdapter, version: str, raw: str, name: str) -> tuple[list[dict], Usage]:
    result = llm.structured(PROMPT_ID, version, f"line: {raw}\nrules read the name as: {name or '(nothing)'}", SPLIT_SCHEMA)
    parts = [
        {"name": part["name"].strip(), "relation": part["relation"]}
        for part in result.data["parts"]
        if part["name"].strip()
    ]
    if parts:
        parts[0]["relation"] = "first"
    return parts, result.usage
