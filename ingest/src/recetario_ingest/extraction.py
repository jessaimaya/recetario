"""Extraction prompt v1, the output schema and the extract-validate-retry loop for one recipe segment."""

import json
from dataclasses import dataclass, field

from recetario_ingest.llm.adapter import LlmAdapter, Usage
from recetario_ingest.llm.prompts import Prompt, PromptRegistry
from recetario_ingest.validate import Issue, validate

PROMPT_ID = "extract"

_PAIR = {
    "type": "object",
    "properties": {"key_text": {"type": "string"}, "value_text": {"type": "string"}},
    "required": ["key_text", "value_text"],
    "additionalProperties": False,
}
EXTRACTED_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "description": {"type": "string"},
        "meta": {"type": "array", "items": _PAIR},
        "ingredient_lines": {"type": "array", "items": {"type": "string"}},
        "steps": {"type": "array", "items": {"type": "string"}},
        "equipment_mentions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "step_index": {"type": "integer"},
                    "listed": {"type": "boolean"},
                },
                "required": ["text", "step_index", "listed"],
                "additionalProperties": False,
            },
        },
        "extras": {"type": "array", "items": _PAIR},
    },
    "required": ["title", "description", "meta", "ingredient_lines", "steps", "equipment_mentions", "extras"],
    "additionalProperties": False,
}

EXTRACT_V1 = Prompt(
    id=PROMPT_ID,
    version="v1",
    system="""You extract one recipe from the text of a cookbook. Copy what the book printed; never improve it.

- title: the recipe's title, without its number.
- description: the blurb or introduction, without quotation marks. Empty string if there is none.
- meta: facts printed before the ingredients, such as servings and times. key_text and value_text are copied as written ("Prep", "5m").
- ingredient_lines: one entry per ingredient line, exactly as written with only the bullet or number marker removed. Do not translate, reword, split or merge lines, and keep notes like "to taste" and "(optional)".
- steps: one entry per step, exactly as written with only the bullet or number marker removed.
- extras: other labelled facts, such as each nutrition value, with key_text and value_text copied as written.
- equipment_mentions: every appliance, pot, pan, piece of bakeware, bowl or tool the cook must own, named in a step or in an equipment list. text is the name as written in the step; step_index is the zero-based index into steps; listed is true only when it appears in an equipment list rather than in a step. Skip incidental utensils (fork, spoon, knife, spatula, tongs, whisk) and consumables such as foil. Do not infer equipment the text doesn't name. Alternatives such as "grill or the campfire" are separate mentions with the same step_index.

Add nothing that is not in the text.""",
    template="{text}",
)


EXTRACT_V2 = Prompt(
    id=PROMPT_ID,
    version="v2",
    system="""You extract one recipe from the text of a cookbook. Copy what the book printed; never improve it.

- title: the full title as printed, without its number. Keep any parenthetical and the punctuation, but write HTML entities such as &quot; as the characters they stand for.
- description: the blurb or introduction, without quotation marks. Empty string if there is none.
- meta: facts printed before the ingredients, such as servings and times. key_text and value_text are copied as written ("Prep", "5m").
- ingredient_lines: one entry per ingredient line, exactly as written with only the bullet or number marker removed. Do not translate, reword, split or merge lines, and keep notes like "to taste" and "(optional)".
- steps: one entry per step, exactly as written with only the bullet or number marker removed.
- extras: other labelled facts, such as each nutrition value, with key_text and value_text copied as written.
- equipment_mentions: the equipment each step names, one entry per piece of equipment per step.
  Equipment is an appliance (oven, microwave, air fryer, slow cooker, pressure cooker, blender, food processor), cookware (pot, pan, skillet, wok), bakeware (baking sheet, baking dish, loaf pan), a bowl, a grill or campfire, or a tool the cook must own (box grater, potato masher, steamer basket).
  Add an entry for every step that names it, even when an earlier step already did, and include the oven whenever a step says oven. Alternatives such as "grill or the campfire" are separate entries with the same step_index.
  text is the name as written in the step. step_index is the zero-based position in steps, so the first step is 0. listed is true only when the equipment appears in an equipment list rather than in a step.
  Leave out incidental utensils (fork, spoon, knife, spatula, tongs, whisk, pot holder), serving dishes, plates and platters, cutting or chopping boards, bags, foil and wrap, trivets, and parts of an appliance (an air fryer's basket, an oven rack, a broiler). Do not infer equipment the text doesn't name.

Add nothing that is not in the text.""",
    template="{text}",
)


def default_prompts() -> PromptRegistry:
    registry = PromptRegistry()
    registry.register(EXTRACT_V1)
    registry.register(EXTRACT_V2)
    return registry


@dataclass
class Extraction:
    extracted: dict | None
    issues: list[Issue]
    attempts: int
    usage: Usage = field(default_factory=Usage)

    @property
    def status(self) -> str:
        return "needs_review" if self.extracted is None or any(i.severity == "error" for i in self.issues) else "ok"


def extract_recipe(llm: LlmAdapter, text: str, version: str) -> Extraction:
    """Ask for the recipe, check it against the text, and on errors ask once more with the problems listed."""
    result = llm.structured(PROMPT_ID, version, text, EXTRACTED_SCHEMA)
    issues = validate(result.data, text)
    usage, attempts = result.usage, 1

    errors = [issue for issue in issues if issue.severity == "error"]
    if errors:
        feedback = "\n".join(f"- {issue.rule}: {issue.detail}" for issue in errors)
        retry_input = (
            f"{text}\n\n---\nYour previous answer was:\n{json.dumps(result.data, ensure_ascii=False)}\n\n"
            f"It had these problems. Answer again with them fixed:\n{feedback}"
        )
        retry = llm.structured(PROMPT_ID, version, retry_input, EXTRACTED_SCHEMA)
        usage = Usage(
            usage.tokens_in + retry.usage.tokens_in,
            usage.tokens_out + retry.usage.tokens_out,
            usage.web_searches + retry.usage.web_searches,
            usage.cost_usd + retry.usage.cost_usd,
        )
        result, issues, attempts = retry, validate(retry.data, text), 2

    return Extraction(result.data if isinstance(result.data, dict) else None, issues, attempts, usage)
