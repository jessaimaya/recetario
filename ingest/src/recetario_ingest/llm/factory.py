"""Build the configured model adapter, refusing to run without what it needs: a price for the API, the CLI for the plan."""

import shutil

from recetario_ingest.config import Config, ConfigError
from recetario_ingest.llm.adapter import LlmAdapter, MeteredLlm
from recetario_ingest.llm.claude import ClaudeLlm
from recetario_ingest.llm.claude_code import ClaudeCodeLlm
from recetario_ingest.llm.openrouter import OpenRouterLlm
from recetario_ingest.llm.prompts import PromptRegistry


def build_llm(config: Config, prompts: PromptRegistry) -> MeteredLlm:
    if not config.model:
        raise ConfigError('no model configured: set model under [llm] in recetario.toml')
    if config.provider == "claude-code":
        if shutil.which("claude") is None:
            raise ConfigError('provider "claude-code" needs the Claude Code CLI: install it and sign in with your plan')
        return MeteredLlm(ClaudeCodeLlm(prompts, config.model, effort=config.effort), config.max_cost_usd)
    if config.provider == "openrouter":
        # OpenRouter reports what it charged, so a [pricing] entry is optional (it only feeds --dry-run estimates).
        return MeteredLlm(OpenRouterLlm(prompts, config.model, config.pricing.get(config.model), effort=config.effort), config.max_cost_usd)
    if config.provider != "anthropic":
        raise ConfigError(
            f"unsupported provider {config.provider!r}; use \"anthropic\", \"claude-code\" or \"openrouter\""
        )
    pricing = config.pricing.get(config.model)
    if pricing is None:
        raise ConfigError(
            f'no price for {config.model!r}: add [pricing."{config.model}"] with input_per_mtok and output_per_mtok, '
            "because costs and the spending cap depend on it"
        )
    inner: LlmAdapter = ClaudeLlm(prompts, config.model, pricing, effort=config.effort)
    return MeteredLlm(inner, config.max_cost_usd)
