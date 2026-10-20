import json
from types import SimpleNamespace

import pytest

from recetario_ingest.config import Config, ConfigError
from recetario_ingest.cost import Pricing
from recetario_ingest.extraction import EXTRACTED_SCHEMA, default_prompts
from recetario_ingest.llm.claude import ClaudeLlm, LlmError
from recetario_ingest.llm.factory import build_llm


class FakeClient:
    """Mimics client.messages.create and records the request."""

    def __init__(self, **response):
        self.requests = []
        self.messages = SimpleNamespace(create=self._create)
        self._response = {
            "stop_reason": "end_turn",
            "stop_details": None,
            "usage": SimpleNamespace(input_tokens=1000, output_tokens=200),
            "content": [SimpleNamespace(type="text", text=json.dumps({"title": "Soup"}))],
        } | response

    def _create(self, **request):
        self.requests.append(request)
        return SimpleNamespace(**self._response)


def llm(client):
    return ClaudeLlm(default_prompts(), "m", Pricing(2.0, 10.0), effort="low", client=client)


def test_the_request_carries_the_prompt_the_schema_and_the_effort():
    client = FakeClient()

    llm(client).structured("extract", "v1", "Soup\nIngredients", EXTRACTED_SCHEMA)

    (request,) = client.requests
    assert request["model"] == "m" and "Copy what the book printed" in request["system"]
    assert request["messages"] == [{"role": "user", "content": "Soup\nIngredients"}]
    assert request["output_config"] == {"effort": "low", "format": {"type": "json_schema", "schema": EXTRACTED_SCHEMA}}


def test_the_reply_is_parsed_and_priced_from_the_config():
    result = llm(FakeClient()).structured("extract", "v1", "x", EXTRACTED_SCHEMA)

    assert result.data == {"title": "Soup"}
    assert (result.usage.tokens_in, result.usage.tokens_out) == (1000, 200)
    assert result.usage.cost_usd == pytest.approx(0.002 + 0.002)


@pytest.mark.parametrize(
    ("response", "message"),
    [
        ({"stop_reason": "refusal", "stop_details": "cyber"}, "declined"),
        ({"stop_reason": "max_tokens"}, "cut off"),
        ({"content": []}, "no text block"),
    ],
)
def test_unusable_replies_raise_a_clear_error(response, message):
    with pytest.raises(LlmError, match=message):
        llm(FakeClient(**response)).structured("extract", "v1", "x", EXTRACTED_SCHEMA)


def test_tools_are_not_supported_yet():
    with pytest.raises(NotImplementedError):
        llm(FakeClient()).structured("extract", "v1", "x", EXTRACTED_SCHEMA, tools=("web_search",))


def test_building_the_model_needs_a_model_a_price_and_a_known_provider():
    with pytest.raises(ConfigError, match="no model configured"):
        build_llm(Config(), default_prompts())
    with pytest.raises(ConfigError, match=r"no price for 'm'.*\[pricing.\"m\"\]"):
        build_llm(Config(model="m"), default_prompts())
    with pytest.raises(ConfigError, match="unsupported provider"):
        build_llm(Config(model="m", provider="other", pricing={"m": Pricing(1, 1)}), default_prompts())
