"""Versioned prompts. A version is immutable: changing the text means registering a new version."""

import hashlib
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Prompt:
    id: str
    version: str
    system: str
    template: str  # str.format placeholders filled by the caller

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(f"{self.system}\0{self.template}".encode()).hexdigest()[:12]


@dataclass
class PromptRegistry:
    _prompts: dict[tuple[str, str], Prompt] = field(default_factory=dict)

    def register(self, prompt: Prompt) -> None:
        existing = self._prompts.get((prompt.id, prompt.version))
        if existing is not None and existing.fingerprint != prompt.fingerprint:
            raise ValueError(f"prompt {prompt.id} {prompt.version} is already registered with different text; add a new version")
        self._prompts[(prompt.id, prompt.version)] = prompt

    def get(self, prompt_id: str, version: str | None = None) -> Prompt:
        """The given version, or the latest registered one (versions compare as v1 < v2 < v10)."""
        if version is not None:
            try:
                return self._prompts[(prompt_id, version)]
            except KeyError:
                raise KeyError(f"no prompt {prompt_id} {version}") from None
        versions = [p for (pid, _), p in self._prompts.items() if pid == prompt_id]
        if not versions:
            raise KeyError(f"no prompt {prompt_id}")
        return max(versions, key=lambda p: _version_order(p.version))


def _version_order(version: str) -> tuple[int, str]:
    digits = "".join(char for char in version if char.isdigit())
    return (int(digits) if digits else 0, version)
