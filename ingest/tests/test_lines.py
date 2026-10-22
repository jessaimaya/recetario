import pytest

from recetario_ingest.line_split import SPLIT_V1, split_line
from recetario_ingest.lines import parse_line
from recetario_ingest.llm.adapter import FakeLlm
from recetario_ingest.llm.prompts import PromptRegistry


@pytest.mark.parametrize(
    "raw, qty, unit, name, preparation",
    [
        ("1/2 cup margarine, cut into pieces", 0.5, "cup", "margarine", "cut into pieces"),
        ("1 1/2 lbs. new potatoes, cut into 1-inch pieces", 1.5, "lb", "new potatoes", "cut into 1-inch pieces"),
        ("2 tbsps. minced garlic", 2, "tbsp", "garlic", "minced"),
        ("2 1/2 lbs. apples - peeled, cored, and chopped", 2.5, "lb", "apples", "peeled, cored, and chopped"),
        ("1 large jalapeno pepper, minced", 1, None, "jalapeno pepper", "minced"),
        ("1/4 cup finely chopped fresh basil, packed", 0.25, "cup", "fresh basil", "finely chopped, packed"),
        ("1/2 tsp. ground black pepper", 0.5, "tsp", "ground black pepper", None),
        ("½ cup water", 0.5, "cup", "water", None),
        ("1 ½ cups flour", 1.5, "cup", "flour", None),
        ("2 cloves garlic", 2, "clove", "garlic", None),
        ("2 cups of milk", 2, "cup", "milk", None),
    ],
)
def test_reads_quantity_unit_name_and_preparation(raw, qty, unit, name, preparation):
    parsed = parse_line(raw)
    assert (parsed.qty, parsed.unit, parsed.name, parsed.preparation) == (qty, unit, name, preparation)
    assert parsed.needs_model is None


def test_a_size_word_before_the_unit_does_not_hide_it():
    parsed = parse_line("1 large head cauliflower, cut into florets")
    assert (parsed.qty, parsed.unit, parsed.name) == (1, "head", "cauliflower")


def test_a_range_keeps_both_ends():
    parsed = parse_line("2 to 3 tbsps. butter")
    assert (parsed.qty, parsed.qty_max, parsed.name) == (2, 3, "butter")


def test_container_size_in_brackets_is_kept_apart_from_the_unit():
    parsed = parse_line("1 (12 oz. ) package frozen French-cut green beans")
    assert (parsed.qty, parsed.unit, parsed.container, parsed.name, parsed.variant) == (
        1, "package", "12 oz", "French-cut green beans", "frozen"
    )


def test_brands_and_asides_are_notes_not_part_of_the_name():
    parsed = parse_line("1 sweet onion (such as Vidalia®), chopped")
    assert (parsed.name, parsed.notes, parsed.preparation) == ("sweet onion", ["such as Vidalia"], "chopped")


@pytest.mark.parametrize(
    "raw",
    ["3 dashes hot sauce, or to taste (optional)", "1 tbsp. butter (optional)", "2 tbsps. nuts, chopped, optional"],
)
def test_optional_is_recognised_in_brackets_and_after_a_comma(raw):
    assert parse_line(raw).optional


@pytest.mark.parametrize(
    "raw, name",
    [("salt to taste", "salt"), ("4 quarts water, or more as needed", "water"), ("water to cover", "water"), ("olive oil, or as needed", "olive oil")],
)
def test_to_taste_and_as_needed_are_flags_not_names(raw, name):
    parsed = parse_line(raw)
    assert parsed.to_taste and parsed.name == name


def test_a_line_without_a_quantity_still_has_a_name():
    parsed = parse_line("nonstick cooking spray")
    assert (parsed.qty, parsed.unit, parsed.name) == (None, None, "nonstick cooking spray")


@pytest.mark.parametrize("raw", ["salt and pepper to taste", "1/4 cup butter or margarine", "1 (12 fluid oz. ) can or bottle beer", "1/4 cup 1/2-inch long vermicelli"])
def test_lines_with_alternatives_or_combinations_go_to_the_model(raw):
    assert parse_line(raw).needs_model


def test_and_inside_a_preparation_does_not_need_the_model():
    assert parse_line("2 onions, peeled and diced").needs_model is None


def _llm(parts):
    prompts = PromptRegistry()
    prompts.register(SPLIT_V1)
    return FakeLlm(prompts, {"split_line": {"parts": parts}})


def test_split_line_returns_parts_and_marks_the_first():
    llm = _llm([{"name": "salt", "relation": "and"}, {"name": "pepper", "relation": "and"}, {"name": " ", "relation": "and"}])
    parts, _ = split_line(llm, "v1", "salt and pepper to taste", "salt and pepper")
    assert parts == [{"name": "salt", "relation": "first"}, {"name": "pepper", "relation": "and"}]


def test_a_footnote_mark_is_not_part_of_the_name():
    parsed = parse_line("1 (3 oz. ) package bacon bits*")
    assert parsed.name == "bacon bits" and parsed.unit == "package"


def test_normalize_term_drops_footnote_marks():
    from recetario_ingest.terms import normalize_term

    assert normalize_term("ingredient", "bacon bits*") == normalize_term("ingredient", "bacon bits")


@pytest.mark.parametrize(
    "raw, name, preparation, variant",
    [
        ("1 (14 oz) can canned diced tomatoes", "tomatoes", "diced", "canned"),
        ("1 can canned diced tomatoes", "tomatoes", "diced", "canned"),
        ("2 cups boiling water", "water", None, "boiling"),
        ("1 cup hot water", "water", None, "hot"),
        ("1 tbsp hot sauce", "hot sauce", None, None),
        ("1 package frozen chopped spinach, thawed", "spinach", "chopped, thawed", "frozen"),
        ("1 can dried beans", "dried beans", None, None),
        ("1/4 cup finely chopped fresh basil", "fresh basil", "finely chopped", None),
    ],
)
def test_form_words_in_front_of_the_name_become_the_variant(raw, name, preparation, variant):
    parsed = parse_line(raw)
    assert (parsed.name, parsed.preparation, parsed.variant) == (name, preparation, variant)
