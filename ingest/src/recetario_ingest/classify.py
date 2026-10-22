"""Say what kind of dish a recipe is (main, side, dessert, dressing...), from its title, description and ingredients."""

from recetario_ingest.llm.adapter import LlmAdapter, Usage
from recetario_ingest.llm.claude import LlmError
from recetario_ingest.llm.prompts import Prompt

PROMPT_ID = "classify"

# Stored as these keys; the app shows the Spanish names.
COURSES = ("plato_fuerte", "guarnicion", "postre", "aderezo", "desayuno", "bebida", "entrada", "snack")

CLASSIFY_SCHEMA = {
    "type": "object",
    "properties": {"course": {"type": "string", "enum": list(COURSES)}},
    "required": ["course"],
    "additionalProperties": False,
}

CLASSIFY_V1 = Prompt(
    id=PROMPT_ID,
    version="v1",
    system="""You say what kind of dish one recipe is, for a cook choosing what to make. Answer with exactly one course:

- plato_fuerte: the main dish of a meal (a protein or a hearty dish: roast chicken, pasta with sauce, a stew, a casserole, a burger, a main-course salad).
- guarnicion: a side dish that goes next to a main (roasted vegetables, rice, mashed potatoes, coleslaw, a side salad).
- postre: a dessert or sweet baked good (cake, cookies, pie, pudding, ice cream, brownies).
- aderezo: a dressing, sauce, dip, marinade, spread, spice mix or other condiment that is added to something else.
- desayuno: a breakfast dish (pancakes, oatmeal, egg breakfasts, granola, breakfast smoothie bowls).
- bebida: a drink (smoothie, juice, cocktail, tea, infused water).
- entrada: a starter or light first course (soup, appetizer, small salad served first).
- snack: a snack or finger food eaten between meals (energy bites, trail mix, chips, popcorn).

Decide from what the dish is mostly eaten as, not from one ingredient. When two fit, prefer the one the title suggests. A soup that is the whole meal is plato_fuerte; a small soup before a meal is entrada.""",
    template="{text}",
)


def recipe_text(data: dict) -> str:
    """What the model sees: the title, the description if any, and the ingredient lines. Steps add cost, not signal."""
    lines = [f"title: {data['title']}"]
    if data.get("description"):
        lines.append(f"description: {data['description']}")
    lines.append("ingredients:")
    lines.extend(f"- {line}" for line in data["ingredient_lines"][:30])
    return "\n".join(lines)


def classify_recipe(llm: LlmAdapter, version: str, text: str) -> tuple[str | None, Usage | None, str | None]:
    """The course, the usage, and why there is no course (None when it worked). A bad answer never fails the book."""
    try:
        result = llm.structured(PROMPT_ID, version, text, CLASSIFY_SCHEMA)
    except LlmError as error:
        return None, None, str(error)
    course = result.data.get("course") if isinstance(result.data, dict) else None
    if course not in COURSES:
        return None, result.usage, f"the model answered {course!r}, which is not a course"
    return course, result.usage, None
