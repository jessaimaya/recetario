"""The config file (recetario.toml). Every key is optional; relative paths resolve against the file's folder."""

import os
import tomllib
from dataclasses import dataclass, field, replace
from pathlib import Path

from recetario_ingest.cost import Pricing
from recetario_ingest.db import REPO_ROOT

DEFAULT_CONFIG_PATH = REPO_ROOT / "recetario.toml"

_TOP_KEYS = {"books_dir", "db_path", "artifacts_dir", "queue_path", "evals_dir", "locale", "llm", "profiles", "pricing"}
_LLM_KEYS = {"provider", "model", "effort", "max_cost_usd", "concurrency"}


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
    concurrency: int = 1  # model calls in flight at once within a stage
    profiles: dict[str, dict] = field(default_factory=dict)  # named [llm] settings, picked per run with --profile
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
    profiles = data.get("profiles", {})
    for name, profile in profiles.items():
        _reject_unknown(path, f"[profiles.{name}] ", profile, _LLM_KEYS)
    for where, table in [("[llm] ", llm), *[(f"[profiles.{name}] ", profile) for name, profile in profiles.items()]]:
        if "concurrency" in table and not (isinstance(table["concurrency"], int) and table["concurrency"] >= 1):
            raise ConfigError(f"{path}: {where}concurrency must be a whole number of 1 or more")
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
        concurrency=llm.get("concurrency", defaults.concurrency),
        profiles=dict(profiles),
        pricing={model: _pricing(path, model, prices) for model, prices in data.get("pricing", {}).items()},
    )


def choose_llm(config: Config, profile: str | None = None, provider: str | None = None, model: str | None = None, workers: int | None = None) -> Config:
    """The config for one run: a named profile first, then any single setting given on the command line."""
    if profile is not None:
        if profile not in config.profiles:
            known = ", ".join(sorted(config.profiles)) or "none defined"
            raise ConfigError(f"unknown profile {profile!r} (profiles in the config: {known})")
        chosen = config.profiles[profile]
        config = replace(config, **{key: chosen[key] for key in _LLM_KEYS if key in chosen})
    elif provider is not None and provider != config.provider and model is None:
        raise ConfigError(f"a model id is specific to its provider: with --provider {provider} also give --model, or use --profile")
    if provider is not None:
        config = replace(config, provider=provider)
    if model is not None:
        config = replace(config, model=model)
    if workers is not None:
        if workers < 1:
            raise ConfigError("--workers must be 1 or more")
        config = replace(config, concurrency=workers)
    return config


def describe_llm(config: Config) -> str:
    return f"{config.provider} / {config.model or 'no model'} · {config.concurrency} at a time"


def _reject_unknown(path: Path, where: str, table: dict, allowed: set[str]) -> None:
    unknown = sorted(set(table) - allowed)
    if unknown:
        raise ConfigError(f"{path}: unknown {where}key {unknown[0]!r} (allowed: {', '.join(sorted(allowed))})")


def _pricing(path: Path, model: str, prices: dict) -> Pricing:
    try:
        return Pricing(float(prices["input_per_mtok"]), float(prices["output_per_mtok"]))
    except KeyError as error:
        raise ConfigError(f"{path}: [pricing.{model!r}] needs input_per_mtok and output_per_mtok") from error
