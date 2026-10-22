"""The real provider: Claude through the Anthropic SDK, answering in JSON that matches a schema."""

import json
from collections.abc import Sequence

import anthropic

from recetario_ingest.cost import Pricing, cost_usd
from recetario_ingest.llm.adapter import LlmResult, Usage
from recetario_ingest.llm.prompts import PromptRegistry


class LlmError(Exception):
    """The model answered, but not with usable JSON (refusal, truncation, empty reply)."""


class ClaudeLlm:
    def __init__(
        self,
        prompts: PromptRegistry,
        model: str,
        pricing: Pricing,
        effort: str = "low",
        max_tokens: int = 16000,
        client: anthropic.Anthropic | None = None,
    ) -> None:
        self.model = model
        self._prompts = prompts
        self._pricing = pricing
        self._effort = effort
        self._max_tokens = max_tokens
        self._client = client or anthropic.Anthropic(max_retries=6)  # credentials come from the environment; parallel workers meet 429s, which it retries with backoff

    def structured(self, prompt_id: str, version: str, input: str, schema: dict, tools: Sequence[str] = ()) -> LlmResult:
        if tools:
            raise NotImplementedError("tool use arrives with the vocabulary resolver (M5)")
        prompt = self._prompts.get(prompt_id, version)
        response = self._client.messages.create(
            model=self.model,
            max_tokens=self._max_tokens,
            system=prompt.system,
            messages=[{"role": "user", "content": prompt.template.format(text=input)}],
            output_config=({} if self._effort == "none" else {"effort": self._effort})
            | {"format": {"type": "json_schema", "schema": schema}},
        )
        usage = Usage(
            tokens_in=response.usage.input_tokens,
            tokens_out=response.usage.output_tokens,
            cost_usd=cost_usd(response.usage.input_tokens, response.usage.output_tokens, self._pricing),
        )
        if response.stop_reason == "refusal":
            raise LlmError(f"the model declined the request ({response.stop_details})")
        if response.stop_reason == "max_tokens":
            raise LlmError(f"the reply hit the {self._max_tokens} token limit and was cut off")
        text = next((block.text for block in response.content if block.type == "text"), None)
        if text is None:
            raise LlmError("the reply has no text block")
        return LlmResult(data=json.loads(text), usage=usage)
