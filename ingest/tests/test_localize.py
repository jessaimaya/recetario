import pytest

from recetario_ingest import db, seed
from recetario_ingest.llm.adapter import FakeLlm
from recetario_ingest.localize import build_input, check, fahrenheit_to_celsius, localize_recipe, metric_amount
from recetario_ingest.registry import default_prompts
from recetario_ingest.stages import recipe_for_translation
from recetario_ingest.vocabulary import Vocabulary


@pytest.mark.parametrize(
    ("qty", "unit", "expected"),
    [(2, "lb", "905 g"), (1.5, "lb", "680 g"), (8, "oz", "225 g"), (1, "oz", "28 g"), (1, "cup", None), (None, "lb", None)],
)
def test_metric_amount(qty, unit, expected):
    assert metric_amount(qty, unit) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [("Bake at 350°F.", "Bake at 175°C."), ("Heat to 425 degrees F", "Heat to 220°C"), ("Set to 175°C", "Set to 175°C"), ("Cook 5 minutes", "Cook 5 minutes")],
)
def test_fahrenheit_to_celsius(text, expected):
    assert fahrenheit_to_celsius(text) == expected


def recipe(**changes):
    base = {
        "title": "Roasted Potatoes", "description": "",
        "steps": ["Heat the oven to 350°F.", "Roast."],
        "ingredient_lines": [{"text": "2 lbs. potatoes", "names": ["papa"], "amount": "905 g"}, {"text": "1/4 cup olive oil", "names": ["aceite de oliva"], "amount": None}],
        "equipment": {"oven": "horno"},
    }
    return {**base, **changes}


def answer(**changes):
    return {"title": "Papas asadas", "description": "", "ingredient_lines": ["905 g de papa", "1/4 taza de aceite de oliva"], "steps": ["Calienta el horno a 350°F.", "Asa."], **changes}


def test_the_input_carries_the_hints():
    text = build_input(recipe())
    assert "1. 2 lbs. potatoes  [es-MX name: papa; amount: 905 g]" in text
    assert "equipment es-MX names: oven = horno" in text


def test_a_good_translation_has_no_issues():
    assert check(recipe(), answer()) == []


def test_a_wrong_count_is_an_error_and_a_missing_name_is_a_warning():
    assert [i.rule for i in check(recipe(), answer(steps=["only one"]))] == ["steps_count"]
    issues = check(recipe(), answer(ingredient_lines=["905 g de patata", "1/4 taza de aceite de oliva"]))
    assert [(i.severity, i.rule) for i in issues] == [("warning", "name_not_used")]


def test_a_plural_still_counts_as_the_name():
    assert check(recipe(), answer(ingredient_lines=["905 g de papas", "1/4 taza de aceite de oliva"])) == []


def test_a_quantity_dropped_from_the_translation_is_a_warning():
    issues = check(recipe(), answer(ingredient_lines=["905 g de papa", "taza de aceite de oliva"]))
    assert [i.rule for i in issues] == ["number_missing"]


def test_one_retry_with_the_problems_listed_and_leftover_fahrenheit_is_converted():
    prompts = default_prompts()
    replies = [answer(steps=["only one"]), answer()]
    llm = FakeLlm(prompts, {"localize": lambda text: replies.pop(0)})

    result = localize_recipe(llm, "v1", recipe())

    assert result.attempts == 2 and result.status == "ok"
    assert "steps_count" in llm.calls[1][2] and "Your previous answer was" in llm.calls[1][2]
    assert result.translated["steps"][0] == "Calienta el horno a 175°C."


def test_a_translation_that_stays_wrong_needs_review():
    llm = FakeLlm(default_prompts(), {"localize": lambda text: answer(steps=[])})
    assert localize_recipe(llm, "v1", recipe()).status == "needs_review"


def test_recipe_for_translation_uses_vocabulary_names_and_amounts():
    conn = db.connect(":memory:")
    db.migrate(conn)
    seed.load_vocabulary(conn)
    vocabulary = {kind: Vocabulary(conn, kind) for kind in ("ingredient", "equipment")}
    data = {
        "title": "T", "description": None, "steps": ["Heat the oven."],
        "ingredient_lines": ["2 lbs. potatoes, cubed", "1 (12 oz. ) can corn on the cob", "salt and pepper", "1 cup kohlrabi"],
        "equipment_mentions": [{"text": "oven", "step_index": 0, "listed": False}, {"text": "wok", "step_index": 0, "listed": False}],
    }
    splits = {"salt and pepper": [{"name": "salt", "relation": "first"}, {"name": "black pepper", "relation": "and"}]}

    prepared = recipe_for_translation(data, vocabulary, splits)

    assert prepared["ingredient_lines"][0] == {"text": "2 lbs. potatoes, cubed", "names": ["papa"], "amount": "905 g"}
    assert prepared["ingredient_lines"][1]["amount"] == "container 340 g"
    assert prepared["ingredient_lines"][2]["names"] == ["sal", "pimienta negra"]
    assert prepared["ingredient_lines"][3]["names"] == []  # unresolved: no hint, the model uses market words
    assert prepared["equipment"] == {"oven": "horno"}  # the wok isn't in the vocabulary yet
    assert prepared["description"] == ""


def test_converted_lengths_and_volumes_and_two_word_plurals_are_not_flagged():
    source = recipe(ingredient_lines=[
        {"text": "4 potatoes, cut into 1/2-inch cubes", "names": ["papa"], "amount": None},
        {"text": "1 (16 fl oz) bottle dressing", "names": [], "amount": None},
        {"text": "2 green onions", "names": ["cebolla cambray"], "amount": None},
    ])
    got = answer(ingredient_lines=["4 papas, en cubos de 1.3 cm", "1 botella (473 ml) de aderezo", "2 cebollas cambray"])
    assert check(source, got) == []


def test_an_echoed_hint_is_stripped_unless_the_original_had_brackets():
    from recetario_ingest.localize import strip_hint_echo

    assert strip_hint_echo("2 tazas de agua [agua]", "2 cups water") == "2 tazas de agua"
    assert strip_hint_echo("1 cda. aceite [es-MX name: aceite; amount: x]", "1 tbsp. oil") == "1 cda. aceite"
    assert strip_hint_echo("1 lata [340 g]", "1 can [12 oz]") == "1 lata [340 g]"


def test_localize_recipe_cleans_echoed_hints_in_the_answer():
    llm = FakeLlm(default_prompts(), {"localize": lambda text: answer(ingredient_lines=["905 g de papa [papa]", "1/4 taza de aceite de oliva [aceite de oliva]"])})
    result = localize_recipe(llm, "v2", recipe())
    assert result.translated["ingredient_lines"] == ["905 g de papa", "1/4 taza de aceite de oliva"]
