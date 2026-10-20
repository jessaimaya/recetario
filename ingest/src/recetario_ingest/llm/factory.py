"""Build the configured model adapter, refusing to run without the price that cost tracking and the spending cap need."""

from recetario_ingest.config import Config, ConfigError
from recetario_ingest.llm.adapter import LlmAdapter, MeteredLlm
from recetario_ingest.llm.claude import ClaudeLlm
from recetario_ingest.llm.prompts import PromptRegistry


def build_llm(config: Config, prompts: PromptRegistry) -> MeteredLlm:
    if not config.model:
        raise ConfigError('no model configured: set model under [llm] in recetario.toml')
    if config.provider != "anthropic":
        raise ConfigError(f"unsupported provider {config.provider!r}; only anthropic is implemented")
    pricing = config.pricing.get(config.model)
    if pricing is None:
        raise ConfigError(
            f'no price for {config.model!r}: add [pricing."{config.model}"] with input_per_mtok and output_per_mtok, '
            "because costs and the spending cap depend on it"
        )
    inner: LlmAdapter = ClaudeLlm(prompts, config.model, pricing, effort=config.effort)
    return MeteredLlm(inner, config.max_cost_usd)
