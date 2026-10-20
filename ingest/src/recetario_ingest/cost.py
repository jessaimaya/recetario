"""Token and cost estimates. Prices come from the config file, never from code, because they change."""

import math
from dataclasses import dataclass

CHARS_PER_TOKEN = 4  # rough average for English text; good enough for a dry run


@dataclass(frozen=True)
class Pricing:
    """USD per million tokens."""

    input_per_mtok: float
    output_per_mtok: float


def estimate_tokens(text: str) -> int:
    return math.ceil(len(text) / CHARS_PER_TOKEN)


def cost_usd(tokens_in: int, tokens_out: int, pricing: Pricing) -> float:
    return (tokens_in * pricing.input_per_mtok + tokens_out * pricing.output_per_mtok) / 1_000_000
