"""Run a book through its stages, one cached unit at a time.

A stage turns artifacts into artifacts, either once per book or once per recipe. Every finished unit is written to the
artifact store before the next one starts, so a run that dies halfway resumes where it stopped. Nothing here touches
the vocabulary or recipe tables; only `stage_runs` is written.
"""

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from recetario_ingest.artifacts import ArtifactStore
from recetario_ingest.llm.adapter import BudgetExceeded, LlmAdapter, Usage
from recetario_ingest.llm.prompts import PromptRegistry
from recetario_ingest.sources import Source


@dataclass(frozen=True)
class Unit:
    key: str | None  # recipe key, or None for a stage that runs once per book
    input_hash: str
    payload: Any = None


@dataclass(frozen=True)
class StageOutput:
    data: Any  # JSON-serialisable
    usage: Usage = Usage()


@dataclass
class Context:
    source: Source
    store: ArtifactStore
    prompts: PromptRegistry
    llm: LlmAdapter | None
    clock: Callable[[], float]  # monotonic seconds
    force_from: str | None = None  # re-run this stage and every later one


class Stage:
    name: str
    version: str  # bump it when the stage's rules change
    prompt_id: str | None = None  # set by stages that call the model

    def units(self, ctx: Context) -> list[Unit]:
        raise NotImplementedError

    def run(self, ctx: Context, unit: Unit) -> StageOutput:
        raise NotImplementedError


class StageError(Exception):
    """The stage can't continue for this book; the message is for the run report."""


@dataclass
class StageTotals:
    ok: int = 0
    cached: int = 0
    failed: int = 0


@dataclass
class BookRun:
    stages: dict[str, StageTotals] = field(default_factory=dict)
    failures: list[str] = field(default_factory=list)
    tokens_in: int = 0
    tokens_out: int = 0
    web_searches: int = 0
    cost_usd: float = 0.0

    @property
    def units_run(self) -> int:
        return sum(totals.ok for totals in self.stages.values())

    @property
    def units_cached(self) -> int:
        return sum(totals.cached for totals in self.stages.values())


def cache_key(stage: Stage, ctx: Context, unit: Unit) -> dict:
    prompt_version = ctx.prompts.get(stage.prompt_id).version if stage.prompt_id else None
    model = ctx.llm.model if stage.prompt_id and ctx.llm else None
    return {
        "input_hash": unit.input_hash,
        "stage_version": stage.version,
        "prompt_version": prompt_version,
        "model": model,
    }


def run_stages(conn: sqlite3.Connection, ctx: Context, stages: list[Stage]) -> BookRun:
    """Run `stages` in order. A failed unit doesn't stop its stage, but the later stages don't run."""
    run = BookRun()
    forcing = False
    for stage in stages:
        forcing = forcing or stage.name == ctx.force_from
        totals = run.stages.setdefault(stage.name, StageTotals())
        try:
            units = stage.units(ctx)
        except StageError as error:
            run.failures.append(f"{stage.name}: {error}")
            break
        try:
            for unit in units:
                _run_unit(conn, ctx, stage, unit, forcing, totals, run)
        except BudgetExceeded as error:  # every later call would be refused too, so stop; a re-run resumes
            run.failures.append(f"{stage.name}: {error}")
            break
        if totals.failed:
            break
    return run


def _run_unit(conn, ctx, stage, unit, forcing, totals, run) -> None:
    key = cache_key(stage, ctx, unit)
    started = ctx.clock()
    usage, status = Usage(), "ok"
    try:
        hit = None if forcing else ctx.store.get(stage.name, unit.key, key)
        if hit is not None:
            status = "cached"
        else:
            output = stage.run(ctx, unit)
            usage = output.usage
            ctx.store.put(stage.name, unit.key, key, output.data)
    except BudgetExceeded:
        raise
    except Exception as error:  # a bug or bad input in one unit shouldn't lose the others
        status = "failed"
        run.failures.append(f"{stage.name} {unit.key or 'book'}: {type(error).__name__}: {error}")

    setattr(totals, status, getattr(totals, status) + 1)
    run.tokens_in += usage.tokens_in
    run.tokens_out += usage.tokens_out
    run.web_searches += usage.web_searches
    run.cost_usd += usage.cost_usd
    with conn:
        conn.execute(
            """
            INSERT INTO stage_runs (source_id, recipe_key, stage, input_hash, prompt_version, model,
                                    tokens_in, tokens_out, web_searches, cost_usd, ms, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                ctx.source.id, unit.key, stage.name, unit.input_hash, key["prompt_version"], key["model"],
                usage.tokens_in, usage.tokens_out, usage.web_searches, usage.cost_usd,
                round((ctx.clock() - started) * 1000), status,
            ),
        )
