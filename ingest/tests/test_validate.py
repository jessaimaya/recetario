import copy

import pytest
from fakes import perfect_extraction

from recetario_ingest.validate import parse_duration_minutes, validate

TEXT = (
    "Pan Potatoes\n\"Crispy.\"\nServing: 2 | Prep: 5m | Ready in: 1h5m\nIngredients\n• 2 large russet potatoes, scrubbed\n"
    "• 1/2 tsp. coarse sea salt\nDirection\n• Heat the skillet over medium heat.\n• Fry the potatoes for 20 minutes.\n"
    "Nutrition Information\n• Calories: 344 calories;"
)


def good():
    data = perfect_extraction(TEXT)
    data["equipment_mentions"] = [{"text": "skillet", "step_index": 0, "listed": False}]
    return data


def rules(data):
    return {(issue.severity, issue.rule) for issue in validate(data, TEXT)}


def test_a_faithful_extraction_has_no_issues():
    assert validate(good(), TEXT) == []


@pytest.mark.parametrize(
    ("corrupt", "expected"),
    [
        (lambda d: d["ingredient_lines"].__setitem__(0, "3 large russet potatoes, scrubbed"), {("error", "ingredient_not_in_source"), ("error", "quantity_not_in_source")}),
        (lambda d: d["ingredient_lines"].__setitem__(1, "1/2 tsp. fine salt"), {("error", "ingredient_not_in_source")}),
        (lambda d: d["ingredient_lines"].append("1 cup invented broth"), {("error", "ingredient_not_in_source")}),
        (lambda d: d.update(ingredient_lines=[]), {("error", "no_ingredients")}),
        (lambda d: d.update(steps=[], equipment_mentions=[]), {("error", "no_steps")}),
        (lambda d: d.update(title="  "), {("error", "missing_title")}),
        (lambda d: d["meta"][1].update(value_text="soon"), {("error", "time_not_parsed"), ("warning", "meta_not_in_source")}),
        (lambda d: d["equipment_mentions"][0].update(step_index=7), {("error", "equipment_step_out_of_range")}),
        (lambda d: d["equipment_mentions"][0].update(text="wok"), {("warning", "equipment_not_in_step")}),
        (lambda d: d["steps"].__setitem__(1, "Boil the potatoes for 5 hours."), {("warning", "step_not_in_source")}),
        (lambda d: d["extras"][0].update(value_text="999 calories;"), {("warning", "extra_not_in_source")}),
    ],
)
def test_each_kind_of_corruption_is_caught(corrupt, expected):
    data = copy.deepcopy(good())
    corrupt(data)

    assert rules(data) == expected


def test_a_listed_equipment_mention_does_not_need_to_appear_in_its_step():
    data = good()
    data["equipment_mentions"] = [{"text": "wok", "step_index": 0, "listed": True}]

    assert validate(data, TEXT) == []


def test_punctuation_accents_and_case_do_not_matter():
    data = good()
    data["ingredient_lines"][1] = "1/2 TSP coarse sea SALT"

    assert validate(data, TEXT) == []


def test_output_that_is_not_an_object_is_one_error():
    assert [(i.severity, i.rule) for i in validate(["nope"], TEXT)] == [("error", "bad_shape")]


@pytest.mark.parametrize(
    ("text", "minutes"),
    [("5m", 5), ("1h5m", 65), ("25 minutes", 25), ("2 hrs", 120), ("1 hour and 5 minutes", 65), ("soon", None), ("", None), ("5", None)],
)
def test_durations(text, minutes):
    assert parse_duration_minutes(text) == minutes
