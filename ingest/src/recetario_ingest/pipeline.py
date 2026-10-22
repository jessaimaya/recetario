"""Run a book through its stages, one cached unit at a time.

A stage turns artifacts into artifacts, either once per book or once per recipe. Every finished unit is written to the
artifact store as it completes, so a run that dies halfway resumes where it stopped. With `ctx.workers` above 1 the
units of a stage run in a thread pool: a worker only runs the unit and stores its artifact, and the calling thread
alone adds up the totals and writes `stage_runs`. Nothing here touches
the vocabulary or recipe tables; only `stage_runs` is written.
"""

import sqlite3
import threading
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
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
    conn: sqlite3.Connection | None = None  # for the stages that read or write the vocabulary
    progress: Any = None  # a progress.ProgressPrinter, or anything with begin/step/end
    workers: int = 1  # units of one stage run at once; their model calls overlap
    db_lock: Any = field(default_factory=threading.RLock)  # whoever uses `conn` while workers run holds this


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
        if not units:  # nothing to do is still done, and the "already done" check looks for a row
            _record_empty(conn, ctx, stage)
        if ctx.progress is not None:
            ctx.progress.begin(ctx.source.raw_title, stage.name, len(units))
        try:
            _run_units(conn, ctx, stage, units, forcing, totals, run)
        except BudgetExceeded as error:  # every later call would be refused too, so stop; a re-run resumes
            run.failures.append(f"{stage.name}: {error}")
            if ctx.progress is not None:
                ctx.progress.end()
            break
        if ctx.progress is not None:
            ctx.progress.end()
        if totals.failed:
            break
    return run


def _record_empty(conn, ctx, stage) -> None:
    with ctx.db_lock, conn:
        conn.execute(
            """
            INSERT INTO stage_runs (source_id, recipe_key, stage, input_hash, prompt_version, model,
                                    tokens_in, tokens_out, web_searches, cost_usd, ms, status)
            VALUES (?, NULL, ?, '', '', '', 0, 0, 0, 0, 0, 'ok')
            """,
            (ctx.source.id, stage.name),
        )


@dataclass
class _Outcome:
    unit: Unit
    key: dict
    status: str
    usage: Usage
    ms: int
    failure: str | None = None


def _run_units(conn, ctx, stage, units, forcing, totals, run) -> None:
    if ctx.workers <= 1 or len(units) <= 1:
        for unit in units:
            _record(conn, ctx, stage, _execute(ctx, stage, unit, forcing), totals, run)
        return

    pending = []
    for unit in units:  # cached units are settled here, at no cost, so the workers only get real work
        key = cache_key(stage, ctx, unit)
        if forcing or ctx.store.get(stage.name, unit.key, key) is None:
            pending.append(unit)
        else:
            _record(conn, ctx, stage, _Outcome(unit, key, "cached", Usage(), 0), totals, run)

    pool = ThreadPoolExecutor(max_workers=min(ctx.workers, len(pending) or 1), thread_name_prefix=stage.name)
    futures: dict[Future, Unit] = {pool.submit(_execute, ctx, stage, unit, forcing): unit for unit in pending}
    recorded: set[Future] = set()
    try:
        for future in as_completed(futures):
            _record(conn, ctx, stage, future.result(), totals, run)
            recorded.add(future)
    except BudgetExceeded:
        pool.shutdown(wait=True, cancel_futures=True)  # calls already in flight finish and keep their artifacts
        for future in futures:
            if future not in recorded and future.done() and not future.cancelled() and future.exception() is None:
                _record(conn, ctx, stage, future.result(), totals, run)
        raise
    finally:
        pool.shutdown(wait=True, cancel_futures=True)


def _execute(ctx, stage, unit, forcing) -> _Outcome:
    """The work of one unit, safe to run on a worker thread: no database writes, no shared counters."""
    key = cache_key(stage, ctx, unit)
    started = ctx.clock()
    usage, status, failure = Usage(), "ok", None
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
        failure = f"{stage.name} {unit.key or 'book'}: {type(error).__name__}: {error}"
    return _Outcome(unit, key, status, usage, round((ctx.clock() - started) * 1000), failure)


def _record(conn, ctx, stage, outcome: _Outcome, totals, run) -> None:
    """Add a finished unit to the totals and to `stage_runs`; always on the thread that runs `run_stages`."""
    usage, unit, key = outcome.usage, outcome.unit, outcome.key
    if outcome.failure:
        run.failures.append(outcome.failure)
    setattr(totals, outcome.status, getattr(totals, outcome.status) + 1)
    run.tokens_in += usage.tokens_in
    run.tokens_out += usage.tokens_out
    run.web_searches += usage.web_searches
    run.cost_usd += usage.cost_usd
    with ctx.db_lock, conn:
        conn.execute(
            """
            INSERT INTO stage_runs (source_id, recipe_key, stage, input_hash, prompt_version, model,
                                    tokens_in, tokens_out, web_searches, cost_usd, ms, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                ctx.source.id, unit.key, stage.name, unit.input_hash, key["prompt_version"], key["model"],
                usage.tokens_in, usage.tokens_out, usage.web_searches, usage.cost_usd, outcome.ms, outcome.status,
            ),
        )
    if ctx.progress is not None:
        ctx.progress.step(outcome.status, usage.cost_usd, usage.tokens_in + usage.tokens_out)
