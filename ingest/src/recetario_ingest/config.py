"""The config file (recetario.toml). Every key is optional; relative paths resolve against the file's folder."""

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from recetario_ingest.cost import Pricing
from recetario_ingest.db import REPO_ROOT

DEFAULT_CONFIG_PATH = REPO_ROOT / "recetario.toml"

_TOP_KEYS = {"books_dir", "db_path", "artifacts_dir", "queue_path", "evals_dir", "locale", "llm", "pricing"}
_LLM_KEYS = {"provider", "model", "effort", "max_cost_usd"}


class ConfigError(Exception):
    pass


def load_dotenv(path: Path = REPO_ROOT / ".env") -> None:
    """Put KEY=value lines of .env into the environment, without overriding what is already set."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return
    for line in lines:
        key, separator, value = line.strip().partition("=")
        if separator and key and not key.startswith("#") and value.strip():
            os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


@dataclass(frozen=True)
class Config:
    books_dir: Path = REPO_ROOT / "local" / "books"
    db_path: Path = REPO_ROOT / "local" / "recetario.db"
    artifacts_dir: Path = REPO_ROOT / "local" / "artifacts"
    evals_dir: Path = REPO_ROOT / "local" / "evals"
    queue_path: Path = REPO_ROOT / "local" / "queue.txt"  # books to ingest, in order, one file name per line
    locale: str = "es-MX"
    provider: str = "anthropic"
    model: str | None = None  # no model means the stages that call one are skipped
    effort: str = "low"  # output_config.effort: low | medium | high | xhigh | max
    max_cost_usd: float | None = None  # spending cap per run
    pricing: dict[str, Pricing] = field(default_factory=dict)  # by model id


def load_config(path: Path | None = None) -> Config:
    """Read `path`, or the default recetario.toml when there is one, or fall back to the defaults."""
    if path is None:
        if not DEFAULT_CONFIG_PATH.exists():
            return Config()
        path = DEFAULT_CONFIG_PATH
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise ConfigError(f"config file not found: {path}") from error
    except tomllib.TOMLDecodeError as error:
        raise ConfigError(f"{path}: {error}") from error

    _reject_unknown(path, "", data, _TOP_KEYS)
    llm = data.get("llm", {})
    _reject_unknown(path, "[llm] ", llm, _LLM_KEYS)
    base = path.resolve().parent
    defaults = Config()
    return Config(
        books_dir=base / data["books_dir"] if "books_dir" in data else defaults.books_dir,
        db_path=base / data["db_path"] if "db_path" in data else defaults.db_path,
        artifacts_dir=base / data["artifacts_dir"] if "artifacts_dir" in data else defaults.artifacts_dir,
        evals_dir=base / data["evals_dir"] if "evals_dir" in data else defaults.evals_dir,
        queue_path=base / data["queue_path"] if "queue_path" in data else defaults.queue_path,
        locale=data.get("locale", defaults.locale),
        provider=llm.get("provider", defaults.provider),
        model=llm.get("model"),
        effort=llm.get("effort", defaults.effort),
        max_cost_usd=llm.get("max_cost_usd"),
        pricing={model: _pricing(path, model, prices) for model, prices in data.get("pricing", {}).items()},
    )


def _reject_unknown(path: Path, where: str, table: dict, allowed: set[str]) -> None:
    unknown = sorted(set(table) - allowed)
    if unknown:
        raise ConfigError(f"{path}: unknown {where}key {unknown[0]!r} (allowed: {', '.join(sorted(allowed))})")


def _pricing(path: Path, model: str, prices: dict) -> Pricing:
    try:
        return Pricing(float(prices["input_per_mtok"]), float(prices["output_per_mtok"]))
    except KeyError as error:
        raise ConfigError(f"{path}: [pricing.{model!r}] needs input_per_mtok and output_per_mtok") from error
