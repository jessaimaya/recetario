import itertools
import sqlite3

import pytest

from recetario_ingest import db
from recetario_ingest.artifacts import ArtifactStore
from recetario_ingest.llm.adapter import FakeLlm
from recetario_ingest.llm.prompts import Prompt, PromptRegistry
from recetario_ingest.pipeline import Context, Stage, StageError, StageOutput, Unit, run_stages
from recetario_ingest.sources import Source


class Killed(BaseException):
    """Stands in for a SIGKILL: nothing in the pipeline may catch it."""


class Counting(Stage):
    """Squares numbers; records which units actually ran."""

    name = "square"
    version = "1"

    def __init__(self, numbers, fail_on=(), kill_on=None):
        self.numbers, self.fail_on, self.kill_on, self.ran = numbers, set(fail_on), kill_on, []

    def units(self, ctx):
        return [Unit(key=f"n{n}", input_hash=str(n), payload=n) for n in self.numbers]

    def run(self, ctx, unit):
        if unit.payload == self.kill_on:
            raise Killed
        if unit.payload in self.fail_on:
            raise ValueError("bad number")
        self.ran.append(unit.payload)
        return StageOutput(unit.payload**2)


@pytest.fixture
def conn(tmp_path):
    connection = db.connect(tmp_path / "t.db")
    db.migrate(connection)
    connection.execute("INSERT INTO sources (id, raw_title, file_hash, format) VALUES (1, 'T', 'h' || 'x', 'epub')")
    yield connection
    connection.close()


@pytest.fixture
def ctx(tmp_path):
    ticks = itertools.count()
    return Context(Source(1, "hx", "T", "in_progress"), ArtifactStore(tmp_path / "art"), PromptRegistry(), None, clock=lambda: next(ticks) / 1000)


def rows(conn):
    return [tuple(r) for r in conn.execute("SELECT recipe_key, stage, status FROM stage_runs ORDER BY id")]


def test_units_are_cached_and_a_second_run_does_no_work(conn, ctx):
    first, second = Counting([1, 2, 3]), Counting([1, 2, 3])

    run_stages(conn, ctx, [first])
    run = run_stages(conn, ctx, [second])

    assert first.ran == [1, 2, 3] and second.ran == []
    assert (run.units_run, run.units_cached) == (0, 3)
    assert ctx.store.read("square", "n3") == 9


def test_a_killed_run_resumes_without_redoing_finished_units(conn, ctx):
    with pytest.raises(Killed):
        run_stages(conn, ctx, [Counting([1, 2, 3, 4], kill_on=3)])

    resumed = Counting([1, 2, 3, 4])
    run = run_stages(conn, ctx, [resumed])

    assert resumed.ran == [3, 4]
    assert (run.units_run, run.units_cached) == (2, 2)
    assert not list(ctx.store.root.rglob("*.tmp"))


def test_a_failed_unit_is_recorded_and_retried_later_while_the_others_finish(conn, ctx):
    run = run_stages(conn, ctx, [Counting([1, 2, 3], fail_on={2})])

    assert run.stages["square"].ok == 2 and run.stages["square"].failed == 1
    assert run.failures == ["square n2: ValueError: bad number"]
    assert [status for _, _, status in rows(conn)] == ["ok", "failed", "ok"]

    retry = Counting([1, 2, 3])
    run_stages(conn, ctx, [retry])
    assert retry.ran == [2]


def test_later_stages_do_not_run_after_a_failure(conn, ctx):
    later = Counting([1])
    later.name = "later"

    run = run_stages(conn, ctx, [Counting([1], fail_on={1}), later])

    assert later.ran == [] and "later" not in run.stages


def test_a_stage_that_cannot_list_its_units_fails_the_book(conn, ctx):
    class Refuses(Counting):
        def units(self, ctx):
            raise StageError("no table of contents")

    run = run_stages(conn, ctx, [Refuses([1])])

    assert run.failures == ["square: no table of contents"]


def test_changing_the_input_or_the_stage_version_recomputes(conn, ctx):
    run_stages(conn, ctx, [Counting([2])])

    changed_input = Counting([2])
    changed_input.units = lambda ctx: [Unit("n2", "different", 2)]
    run_stages(conn, ctx, [changed_input])
    bumped = Counting([2])
    bumped.version = "2"
    run_stages(conn, ctx, [bumped])

    assert changed_input.ran == [2] and bumped.ran == [2]


def test_force_stage_reruns_that_stage_and_everything_after_it(conn, ctx):
    def stages():
        a, b = Counting([1]), Counting([1])
        a.name, b.name = "a", "b"
        return a, b

    run_stages(conn, ctx, list(stages()))
    ctx.force_from = "b"
    a, b = stages()
    run_stages(conn, ctx, [a, b])

    assert a.ran == [] and b.ran == [1]


def test_prompt_version_and_model_are_part_of_the_cache_key(conn, ctx):
    prompts = PromptRegistry()
    prompts.register(Prompt("p", "v1", "s", "t"))
    ctx.prompts, ctx.llm = prompts, FakeLlm(prompts, {})

    class Asks(Counting):
        prompt_id = "p"

    run_stages(conn, ctx, [Asks([1])])
    same = Asks([1])
    run_stages(conn, ctx, [same])
    prompts.register(Prompt("p", "v2", "s", "t2"))
    new_prompt = Asks([1])
    run_stages(conn, ctx, [new_prompt])
    ctx.llm.model = "other-model"
    new_model = Asks([1])
    run_stages(conn, ctx, [new_model])

    assert (same.ran, new_prompt.ran, new_model.ran) == ([], [1], [1])
    assert conn.execute("SELECT prompt_version, model FROM stage_runs ORDER BY id DESC LIMIT 1").fetchone()[:] == ("v2", "other-model")


def test_stage_runs_record_status_and_unit(conn, ctx):
    run_stages(conn, ctx, [Counting([1])])
    run_stages(conn, ctx, [Counting([1])])

    assert rows(conn) == [("n1", "square", "ok"), ("n1", "square", "cached")]
