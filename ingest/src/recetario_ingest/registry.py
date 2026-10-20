"""Every prompt the pipeline can use, registered once."""

from recetario_ingest.extraction import EXTRACT_V1, EXTRACT_V2
from recetario_ingest.line_split import SPLIT_V1
from recetario_ingest.llm.prompts import PromptRegistry
from recetario_ingest.resolver import RESOLVE_V1


def default_prompts() -> PromptRegistry:
    registry = PromptRegistry()
    for prompt in (EXTRACT_V1, EXTRACT_V2, SPLIT_V1, RESOLVE_V1):
        registry.register(prompt)
    return registry
