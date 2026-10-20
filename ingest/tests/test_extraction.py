import json

import pytest
from fakes import perfect_extraction

from recetario_ingest.extraction import EXTRACT_V1, EXTRACTED_SCHEMA, default_prompts, extract_recipe
from recetario_ingest.llm.adapter import FakeLlm, LlmResult, Usage

TEXT = (
    "Soup\n\"Warm.\"\nServing: 2 | Prep: 5m | Ready in: 15m\nIngredients\n• 2 cups stock\n• 1 onion\n"
    "Direction\n• Simmer the stock.\n• Add the onion.\nNutrition Information\n• Calories: 50 calories;"
)


class Scripted:
    """Returns the queued answers in order and remembers the inputs."""

    model = "scripted"

    def __init__(self, *answers):
        self.answers, self.inputs = list(answers), []

    def structured(self, prompt_id, version, input, schema, tools=()):
        self.inputs.append(input)
        return LlmResult(self.answers.pop(0), Usage(tokens_in=100, tokens_out=40, cost_usd=0.01))


def corrupted():
    data = perfect_extraction(TEXT)
    data["ingredient_lines"][0] = "3 cups stock"
    return data


def test_a_clean_first_answer_is_one_call():
    llm = Scripted(perfect_extraction(TEXT))

    result = extract_recipe(llm, TEXT, "v1")

    assert (result.status, result.attempts, result.issues) == ("ok", 1, [])
    assert result.usage.cost_usd == 0.01 and llm.inputs == [TEXT]


def test_an_error_triggers_one_retry_that_shows_the_problems_and_the_bad_answer():
    llm = Scripted(corrupted(), perfect_extraction(TEXT))

    result = extract_recipe(llm, TEXT, "v1")

    assert (result.status, result.attempts) == ("ok", 2)
    assert "ingredient_not_in_source" in llm.inputs[1] and "3 cups stock" in llm.inputs[1] and llm.inputs[1].startswith(TEXT)
    assert (result.usage.tokens_in, result.usage.tokens_out, result.usage.cost_usd) == (200, 80, 0.02)


def test_still_wrong_after_the_retry_means_needs_review_with_the_issues_attached():
    llm = Scripted(corrupted(), corrupted())

    result = extract_recipe(llm, TEXT, "v1")

    assert result.status == "needs_review" and result.attempts == 2
    assert {issue.rule for issue in result.issues} == {"ingredient_not_in_source", "quantity_not_in_source"}
    assert len(llm.inputs) == 2  # never a third call


def test_warnings_alone_do_not_retry_or_block():
    data = perfect_extraction(TEXT)
    data["steps"][0] = "Gently simmer the stock."
    llm = Scripted(data)

    result = extract_recipe(llm, TEXT, "v1")

    assert result.status == "ok" and result.attempts == 1
    assert [issue.rule for issue in result.issues] == ["step_not_in_source"]


def test_an_answer_that_is_not_an_object_ends_as_needs_review():
    result = extract_recipe(Scripted("garbage", "still garbage"), TEXT, "v1")

    assert result.extracted is None and result.status == "needs_review"


def test_the_fake_model_works_through_the_registered_prompt():
    prompts = default_prompts()
    fake = FakeLlm(prompts, {"extract": perfect_extraction})

    result = extract_recipe(fake, TEXT, prompts.get("extract").version)

    assert result.status == "ok" and fake.calls[0][:2] == ("extract", "v1")


def test_the_schema_requires_every_field_and_forbids_extras():
    assert set(EXTRACTED_SCHEMA["required"]) == set(EXTRACTED_SCHEMA["properties"])
    assert EXTRACTED_SCHEMA["additionalProperties"] is False
    json.dumps(EXTRACTED_SCHEMA)
    assert "{text}" in EXTRACT_V1.template
