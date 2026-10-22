"""OpenRouter: any model it hosts, answering in JSON that matches a schema. Lets us compare cost and quality across models."""

import json
import os
import time
import urllib.error
import urllib.request
from collections.abc import Callable

from recetario_ingest.cost import Pricing, cost_usd
from recetario_ingest.llm.adapter import LlmResult, Usage
from recetario_ingest.llm.claude import LlmError
from recetario_ingest.llm.prompts import PromptRegistry

URL = "https://openrouter.ai/api/v1/chat/completions"
_RETRY_AFTER_SECONDS = (2, 5, 15, 30)  # waits before retrying a 429 or a 5xx; parallel workers make them likely


class SchemaUnsupported(LlmError):
    """No provider of the model accepts a JSON schema, so routing found no endpoint."""


def _post(request: dict, api_key: str) -> dict:
    for wait in (*_RETRY_AFTER_SECONDS, None):
        try:
            return _post_once(request, api_key)
        except _Retryable as error:
            if wait is None:
                raise LlmError(str(error)) from error
            time.sleep(wait)


class _Retryable(Exception):
    pass


def _post_once(request: dict, api_key: str) -> dict:
    http_request = urllib.request.Request(
        URL,
        data=json.dumps(request).encode(),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(http_request, timeout=300) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        detail = error.read().decode(errors="replace")
        if error.code == 404 and "Filter by Parameters" in detail:
            raise SchemaUnsupported(f"no provider of this model accepts a JSON schema: {detail[:300]}") from error
        if error.code == 429 or error.code >= 500:
            raise _Retryable(f"OpenRouter answered {error.code}: {detail[:500]}") from error
        raise LlmError(f"OpenRouter answered {error.code}: {detail[:500]}") from error


class OpenRouterLlm:
    def __init__(
        self,
        prompts: PromptRegistry,
        model: str,
        pricing: Pricing | None = None,
        effort: str = "low",
        max_tokens: int = 16000,
        post: Callable[[dict, str], dict] = _post,
    ) -> None:
        self.model = model
        self._prompts = prompts
        self._pricing = pricing  # only a fallback: OpenRouter reports the cost it charged
        self._think = effort != "none"
        self._schema_in_prompt = False  # set once no provider of the model takes a schema
        self._max_tokens = max_tokens
        self._post = post

    def structured(self, prompt_id, version, input, schema, tools=()):
        if tools:
            raise NotImplementedError("tool use arrives with the vocabulary resolver (M5)")
        api_key = os.environ.get("OPENROUTER_API_KEY")
        if not api_key:
            raise LlmError("OPENROUTER_API_KEY is not set")
        prompt = self._prompts.get(prompt_id, version)
        system, user = prompt.system, prompt.template.format(text=input)
        request = {
            "model": self.model,
            "max_tokens": self._max_tokens,
            "usage": {"include": True},
        }
        if not self._think:
            # Hybrid models such as Qwen3 think by default and can burn the whole token budget doing it. Opt-in only,
            # because require_parameters would drop providers of models that have no reasoning switch.
            request["reasoning"] = {"enabled": False}

        def build() -> dict:
            if self._schema_in_prompt:
                # Fallback for models without schema support: ask for JSON and put the schema in the prompt.
                text = f"{system}\n\nAnswer with a single JSON object that matches this JSON schema:\n{json.dumps(schema)}"
                return request | {
                    "messages": [{"role": "system", "content": text}, {"role": "user", "content": user}],
                    "response_format": {"type": "json_object"},
                }
            return request | {
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {"name": prompt_id, "strict": True, "schema": schema},
                },
                # Only route to providers that honour the schema instead of silently ignoring it.
                "provider": {"require_parameters": True},
            }

        try:
            body = self._post(build(), api_key)
        except SchemaUnsupported:
            self._schema_in_prompt = True
            body = self._post(build(), api_key)
        if "error" in body:
            raise LlmError(f"OpenRouter error: {body['error']}")
        choice = body["choices"][0]
        if choice.get("finish_reason") == "length":
            raise LlmError(f"the reply hit the {self._max_tokens} token limit and was cut off")
        text = choice["message"].get("content")
        if not text:
            raise LlmError("the reply has no text")
        usage = body.get("usage", {})
        tokens_in, tokens_out = usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0)
        cost = usage.get("cost")
        if cost is None:
            cost = cost_usd(tokens_in, tokens_out, self._pricing) if self._pricing else 0.0
        try:
            data = json.loads(text)
        except json.JSONDecodeError as error:
            raise LlmError(f"the reply is not valid JSON ({error})") from error
        return LlmResult(data=data, usage=Usage(tokens_in=tokens_in, tokens_out=tokens_out, cost_usd=cost))
