import pytest

from recetario_ingest.llm.adapter import BudgetExceeded, FakeLlm, MeteredLlm
from recetario_ingest.llm.prompts import Prompt, PromptRegistry


def registry():
    prompts = PromptRegistry()
    prompts.register(Prompt("extract", "v1", "system", "Recipe: {text}"))
    prompts.register(Prompt("extract", "v2", "system", "Better recipe: {text}"))
    prompts.register(Prompt("extract", "v10", "system", "Best recipe: {text}"))
    return prompts


def test_latest_version_compares_numbers_not_text():
    assert registry().get("extract").version == "v10"
    assert registry().get("extract", "v1").template == "Recipe: {text}"


def test_unknown_prompt_or_version_is_an_error():
    with pytest.raises(KeyError, match="no prompt nope"):
        registry().get("nope")
    with pytest.raises(KeyError, match="no prompt extract v9"):
        registry().get("extract", "v9")


def test_a_registered_version_cannot_change_its_text():
    prompts = registry()
    prompts.register(Prompt("extract", "v1", "system", "Recipe: {text}"))  # identical is fine

    with pytest.raises(ValueError, match="add a new version"):
        prompts.register(Prompt("extract", "v1", "system", "Edited"))


def test_fake_returns_scripted_data_and_records_calls():
    fake = FakeLlm(registry(), {"extract": lambda text: {"echo": text}})

    result = fake.structured("extract", "v1", "soup", {"type": "object"})

    assert result.data == {"echo": "soup"}
    assert result.usage.tokens_in > 0 and result.usage.tokens_out > 0
    assert fake.calls == [("extract", "v1", "soup")]


def test_fake_rejects_a_prompt_that_was_never_registered():
    fake = FakeLlm(registry(), {"extract": {}})

    with pytest.raises(KeyError):
        fake.structured("extract", "v9", "x", {})


def test_spending_cap_stops_new_calls_once_reached():
    metered = MeteredLlm(FakeLlm(registry(), {"extract": {}}, cost_per_call=0.4), max_cost_usd=1.0)

    for _ in range(3):  # the third call starts at $0.80 and ends at $1.20
        metered.structured("extract", "v1", "x", {})
    with pytest.raises(BudgetExceeded, match=r"\$1.00"):
        metered.structured("extract", "v1", "x", {})

    assert metered.spent_usd == pytest.approx(1.2)


def test_no_cap_means_no_limit():
    metered = MeteredLlm(FakeLlm(registry(), {"extract": {}}, cost_per_call=100), max_cost_usd=None)

    for _ in range(5):
        metered.structured("extract", "v1", "x", {})
