"""Stage outputs on disk: <root>/<stage>/<unit>.json, each tagged with the cache key it was made under.

A file counts as a cache hit only when its stored key matches, so changing the input, the stage version, the prompt
version or the model recomputes it. Files are written atomically, so a killed run never leaves a half-written one.
"""

import json
import os
import re
from pathlib import Path
from typing import Any

BOOK_UNIT = "book"  # file name for stages that run once per book


class ArtifactStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def path(self, stage: str, unit: str | None) -> Path:
        name = re.sub(r"[^A-Za-z0-9._-]", "_", unit) if unit is not None else BOOK_UNIT
        return self.root / stage / f"{name}.json"

    def get(self, stage: str, unit: str | None, cache_key: dict) -> Any | None:
        """The stored output if it was made under `cache_key`, else None."""
        stored = self._load(self.path(stage, unit))
        if stored is None or stored.get("cache_key") != cache_key:
            return None
        return stored["output"]

    def read(self, stage: str, unit: str | None) -> Any:
        stored = self._load(self.path(stage, unit))
        if stored is None:
            raise FileNotFoundError(f"no {stage} artifact for {unit or BOOK_UNIT}")
        return stored["output"]

    def read_all(self, stage: str) -> list[Any]:
        return [self._load(path)["output"] for path in sorted((self.root / stage).glob("*.json"))]

    def put(self, stage: str, unit: str | None, cache_key: dict, output: Any) -> None:
        path = self.path(stage, unit)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps({"cache_key": cache_key, "output": output}, ensure_ascii=False), encoding="utf-8")
        os.replace(temporary, path)

    @staticmethod
    def _load(path: Path) -> dict | None:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return None
