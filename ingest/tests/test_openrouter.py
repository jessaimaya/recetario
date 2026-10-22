import json

import pytest

from recetario_ingest.config import Config
from recetario_ingest.cost import Pricing
from recetario_ingest.extraction import EXTRACTED_SCHEMA
from recetario_ingest.llm.adapter import MeteredLlm
from recetario_ingest.llm.claude import LlmError
from recetario_ingest.llm.factory import build_llm
from recetario_ingest.llm.openrouter import OpenRouterLlm, SchemaUnsupported
from recetario_ingest.registry import default_prompts


def body(**changes):
    return {
        "choices": [{"finish_reason": "stop", "message": {"content": json.dumps({"title": "Soup"})}}],
        "usage": {"prompt_tokens": 1000, "completion_tokens": 200, "cost": 0.0007},
    } | changes


class Post:
    def __init__(self, reply):
        self.requests, self._reply = [], reply

    def __call__(self, request, api_key):
        self.requests.append((request, api_key))
        return self._reply


@pytest.fixture(autouse=True)
def key(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")


def llm(post, pricing=None):
    return OpenRouterLlm(default_prompts(), "deepseek/deepseek-chat", pricing, post=post)


def test_the_request_carries_the_prompt_the_schema_and_only_providers_that_honour_it():
    post = Post(body())

    llm(post).structured("extract", "v1", "Soup\nIngredients", EXTRACTED_SCHEMA)

    ((request, api_key),) = post.requests
    assert api_key == "sk-test" and request["model"] == "deepseek/deepseek-chat"
    assert request["messages"][0]["role"] == "system" and "Copy what the book printed" in request["messages"][0]["content"]
    assert request["messages"][1] == {"role": "user", "content": "Soup\nIngredients"}
    assert request["response_format"]["json_schema"]["schema"] == EXTRACTED_SCHEMA
    assert request["provider"] == {"require_parameters": True}


def test_the_cost_is_the_one_openrouter_charged():
    result = llm(Post(body())).structured("extract", "v1", "x", EXTRACTED_SCHEMA)

    assert result.data == {"title": "Soup"}
    assert (result.usage.tokens_in, result.usage.tokens_out, result.usage.cost_usd) == (1000, 200, 0.0007)


def test_without_a_reported_cost_the_configured_price_is_used():
    reply = body(usage={"prompt_tokens": 1000, "completion_tokens": 200})

    result = llm(Post(reply), Pricing(2.0, 10.0)).structured("extract", "v1", "x", EXTRACTED_SCHEMA)

    assert result.usage.cost_usd == pytest.approx(0.004)


@pytest.mark.parametrize(
    ("reply", "message"),
    [
        ({"error": {"code": 502, "message": "upstream"}}, "OpenRouter error"),
        (body(choices=[{"finish_reason": "length", "message": {"content": "{"}}]), "cut off"),
        (body(choices=[{"finish_reason": "stop", "message": {"content": None}}]), "no text"),
        (body(choices=[{"finish_reason": "stop", "message": {"content": "not json"}}]), "not valid JSON"),
    ],
)
def test_unusable_replies_raise(reply, message):
    with pytest.raises(LlmError, match=message):
        llm(Post(reply)).structured("extract", "v1", "x", EXTRACTED_SCHEMA)


def test_a_missing_key_is_reported_before_any_call(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY")
    post = Post(body())

    with pytest.raises(LlmError, match="OPENROUTER_API_KEY"):
        llm(post).structured("extract", "v1", "x", EXTRACTED_SCHEMA)
    assert post.requests == []


def test_the_factory_builds_it_without_needing_a_price():
    built = build_llm(Config(provider="openrouter", model="qwen/qwen3-30b-a3b"), default_prompts())

    assert isinstance(built, MeteredLlm) and built.model == "qwen/qwen3-30b-a3b"


def test_effort_none_switches_thinking_off_and_otherwise_the_request_has_no_reasoning_field():
    quiet, default = Post(body()), Post(body())

    OpenRouterLlm(default_prompts(), "m", effort="none", post=quiet).structured("extract", "v1", "x", EXTRACTED_SCHEMA)
    llm(default).structured("extract", "v1", "x", EXTRACTED_SCHEMA)

    assert quiet.requests[0][0]["reasoning"] == {"enabled": False}
    assert "reasoning" not in default.requests[0][0]


def test_a_model_without_schema_support_falls_back_to_the_schema_in_the_prompt():
    class Picky(Post):
        def __call__(self, request, api_key):
            self.requests.append((request, api_key))
            if "provider" in request:
                raise SchemaUnsupported("no endpoint")
            return self._reply

    post = Picky(body())
    model = llm(post)

    assert model.structured("extract", "v1", "x", EXTRACTED_SCHEMA).data == {"title": "Soup"}
    model.structured("extract", "v1", "y", EXTRACTED_SCHEMA)

    (first, _), (retry, _), (next_call, _) = post.requests  # the fallback is remembered: one failed try in total
    assert "response_format" in first and retry["response_format"] == {"type": "json_object"}
    assert json.dumps(EXTRACTED_SCHEMA) in retry["messages"][0]["content"] and "provider" not in retry
    assert next_call == retry | {"messages": next_call["messages"]}
