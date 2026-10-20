import pytest

from recetario_ingest.terms import TermExample, collect_terms, normalize_term, singular


@pytest.mark.parametrize(
    ("word", "expected"),
    [("potatoes", "potato"), ("cherries", "cherry"), ("sheets", "sheet"), ("dishes", "dish"), ("boxes", "box"), ("glass", "glass"),
     ("asparagus", "asparagus"), ("couscous", "couscous"), ("citrus", "citrus"), ("oz", "oz"), ("peas", "pea"), ("is", "is"), ("leaves", "leaf"), ("loaves", "loaf")],
)
def test_singular(word, expected):
    assert singular(word) == expected


@pytest.mark.parametrize(
    ("kind", "text", "expected"),
    [
        ("equipment", "big skillet", "skillet"),
        ("equipment", "13”x9” baking dish", "baking dish"),
        ("equipment", "2-qt. baking dish", "baking dish"),
        ("equipment", "Preheated Oven", "oven"),
        ("equipment", "baking sheets", "baking sheet"),
        ("equipment", "cast-iron skillet", "cast iron skillet"),
        ("equipment", "Instant Pot®", "instant pot"),
        ("equipment", "big", "big"),  # never empty a term
        ("attribute_key", "Total Carbohydrate", "total carbohydrate"),
        ("attribute_key", "Ready  in", "ready in"),
        ("ingredient", "Tomatoes (ripe)", "tomato"),
        ("ingredient", "Jalapeño Peppers", "jalapeno pepper"),
        ("ingredient", "stone-ground grits", "stone ground grits"),
        ("ingredient", "fresh sage leaves", "sage leaf"),
        ("ingredient", "frozen cauliflower", "cauliflower"),
        ("ingredient", "dried cranberries", "dried cranberry"),  # drying changes what you buy
        ("ingredient", "fresh", "fresh"),
    ],
)
def test_normalize_term(kind, text, expected):
    assert normalize_term(kind, text) == expected


def extraction(title, steps, equipment, meta=(), extras=()):
    return {
        "extracted": {
            "title": title, "steps": steps, "ingredient_lines": [],
            "equipment_mentions": [{"text": t, "step_index": i, "listed": False} for t, i in equipment],
            "meta": [{"key_text": k, "value_text": v} for k, v in meta],
            "extras": [{"key_text": k, "value_text": v} for k, v in extras],
        }
    }


def test_collect_terms_merges_spellings_and_keeps_up_to_three_examples():
    items = [extraction(f"R{n}", ["Heat the big skillet."], [("big skillet", 0)]) for n in range(5)]
    items.append(extraction("R9", ["Use a Skillet."], [("Skillets", 0)]))

    found = collect_terms(items)

    examples = found[("equipment", "skillet")]
    assert len(examples) == 3 and examples[0] == TermExample("R0", "Heat the big skillet.")


def test_collect_terms_reads_meta_and_extras_keys_with_their_values():
    found = collect_terms([extraction("R", ["x"], [], meta=[("Prep", "5m")], extras=[("Total Fat", "7 g")])])

    assert found[("attribute_key", "prep")] == [TermExample("R", "Prep: 5m")]
    assert found[("attribute_key", "total fat")] == [TermExample("R", "Total Fat: 7 g")]


def test_collect_terms_survives_a_bad_step_index_and_a_failed_extraction():
    found = collect_terms([{"extracted": None}, extraction("R", ["a"], [("oven", 9)])])

    assert found == {("equipment", "oven"): [TermExample("R", "")]}


def test_collect_terms_is_sorted_for_stable_units():
    found = collect_terms([extraction("R", ["a"], [("wok", 0), ("bowl", 0)], meta=[("Prep", "1m")])])

    assert list(found) == [("attribute_key", "prep"), ("equipment", "bowl"), ("equipment", "wok")]
