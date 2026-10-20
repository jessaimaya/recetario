"""The only way stages talk to a model. A real provider and the fake satisfy the same protocol."""

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from recetario_ingest.cost import estimate_tokens
from recetario_ingest.llm.prompts import PromptRegistry


@dataclass(frozen=True)
class Usage:
    tokens_in: int = 0
    tokens_out: int = 0
    web_searches: int = 0
    cost_usd: float = 0.0


@dataclass(frozen=True)
class LlmResult:
    data: Any
    usage: Usage
    cached: bool = False


class LlmAdapter(Protocol):
    model: str

    def structured(
        self, prompt_id: str, version: str, input: str, schema: dict, tools: Sequence[str] = ()
    ) -> LlmResult: ...


class BudgetExceeded(Exception):
    pass


class FakeLlm:
    """Answers from a script and records every call, so tests never touch the network.

    `responses` maps a prompt id to the data to return, or to a function of the input text.
    """

    model = "fake"

    def __init__(self, prompts: PromptRegistry, responses: dict[str, Any], cost_per_call: float = 0.0) -> None:
        self._prompts = prompts
        self._responses = responses
        self._cost_per_call = cost_per_call
        self.calls: list[tuple[str, str, str]] = []  # (prompt id, version, input)

    def structured(self, prompt_id, version, input, schema, tools=()):
        self._prompts.get(prompt_id, version)  # fail like the real adapter would on an unknown prompt
        self.calls.append((prompt_id, version, input))
        response = self._responses[prompt_id]
        data = response(input) if isinstance(response, Callable) else response
        usage = Usage(
            tokens_in=estimate_tokens(input), tokens_out=estimate_tokens(json.dumps(data)), cost_usd=self._cost_per_call
        )
        return LlmResult(data=data, usage=usage)


class MeteredLlm:
    """Adds up the cost of the calls it forwards and refuses new ones once the spending cap is reached.

    The cap is checked before each call, so the last call can overshoot it by its own cost.
    """

    def __init__(self, inner: LlmAdapter, max_cost_usd: float | None) -> None:
        self.model = inner.model
        self._inner = inner
        self._max_cost_usd = max_cost_usd
        self.spent_usd = 0.0

    def structured(self, prompt_id, version, input, schema, tools=()):
        if self._max_cost_usd is not None and self.spent_usd >= self._max_cost_usd:
            raise BudgetExceeded(f"spending cap of ${self._max_cost_usd:.2f} per run reached (spent ${self.spent_usd:.2f})")
        result = self._inner.structured(prompt_id, version, input, schema, tools)
        self.spent_usd += result.usage.cost_usd
        return result
