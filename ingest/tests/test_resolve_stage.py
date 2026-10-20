import itertools

import pytest
from fakes import ResolveScript, map_answer, new_answer
from test_terms import extraction

from recetario_ingest import db, seed
from recetario_ingest.artifacts import ArtifactStore
from recetario_ingest.pipeline import Context, run_stages
from recetario_ingest.registry import default_prompts
from recetario_ingest.sources import Source
from recetario_ingest.stages import ResolveStage
from recetario_ingest.vocabulary import find_proposal, Vocabulary


@pytest.fixture
def setup(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    db.migrate(conn)
    seed.load_vocabulary(conn)
    conn.execute("INSERT INTO sources (id, raw_title, file_hash, format) VALUES (1, 'T', 'h', 'epub')")
    store = ArtifactStore(tmp_path / "art")
    items = [
        extraction("R1", ["Heat the big skillet.", "Use a wok."], [("big skillet", 0), ("wok", 1)], meta=[("Prep", "5m")]),
        extraction("R2", ["Use a cast iron skillet."], [("cast iron skillet", 0), ("Skillet", 0)]),
    ]
    for number, item in enumerate(items):
        store.put("extract", f"r{number}", {}, {"extracted": item["extracted"]})
    ticks = itertools.count()
    llm = ResolveScript(map_answer("skillet", 0.95), new_answer("wok", "wok", 0.9))
    ctx = Context(Source(1, "h", "T", "in_progress"), store, default_prompts(), llm, clock=lambda: next(ticks) / 1000, conn=conn)
    return conn, ctx, llm


def test_each_unique_term_is_one_unit_and_known_terms_cost_nothing(setup):
    conn, ctx, llm = setup

    run = run_stages(conn, ctx, [ResolveStage()])

    assert run.failures == [] and run.stages["resolve"].ok == 4  # prep, skillet, wok, cast iron skillet
    assert len(llm.inputs) == 2  # only the wok and the cast iron skillet needed the model
    assert run.cost_usd == pytest.approx(0.002)
    assert Vocabulary(conn, "equipment").exact("cast iron skillet").name_en == "skillet"  # auto-accepted
    assert find_proposal(conn, "equipment", "wok").status == "open"


def test_the_artifacts_record_what_happened_and_a_rerun_does_nothing(setup):
    conn, ctx, llm = setup
    run_stages(conn, ctx, [ResolveStage()])

    outputs = {o["term"]: (o["method"], o["outcome"]) for o in ctx.store.read_all("resolve")}
    rerun = run_stages(conn, ctx, [ResolveStage()])

    assert outputs == {
        "prep": ("exact", "resolved"), "skillet": ("exact", "resolved"),
        "wok": ("llm", "proposed"), "cast iron skillet": ("llm", "resolved"),
    }
    assert rerun.units_run == 0 and rerun.units_cached == 4 and len(llm.inputs) == 2


def test_decisions_made_in_review_survive_a_forced_rerun(setup):
    conn, ctx, llm = setup
    run_stages(conn, ctx, [ResolveStage()])
    conn.execute("UPDATE proposals SET status = 'rejected'")
    ctx.force_from = "resolve"

    run_stages(conn, ctx, [ResolveStage()])

    assert len(llm.inputs) == 2 and find_proposal(conn, "equipment", "wok").status == "rejected"
