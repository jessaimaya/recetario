import json
import shutil
import zipfile

import pytest
from epub_fixtures import make_epub, sample_book

from recetario_ingest import db, ingest, stages
from recetario_ingest.cli import main
from recetario_ingest.config import Config


class Killed(BaseException):
    pass


@pytest.fixture
def config(tmp_path):
    (tmp_path / "books").mkdir()
    return Config(books_dir=tmp_path / "books", db_path=tmp_path / "out" / "r.db", artifacts_dir=tmp_path / "out" / "artifacts")


def snapshot(config):
    """Every file the run leaves behind, with its contents, plus the stage_runs count."""
    files = {str(p.relative_to(config.artifacts_dir)): p.read_bytes() for p in config.artifacts_dir.rglob("*") if p.is_file()}
    conn = db.connect(config.db_path)
    try:
        return files, conn.execute("SELECT count(*) FROM stage_runs").fetchone()[0]
    finally:
        conn.close()


def test_a_book_is_registered_and_segmented(config):
    sample_book(config.books_dir / "b.epub", recipes=5)

    report = ingest.ingest(config)

    (book,) = report.books
    assert book.outcome == "ingested"
    assert book.kinds == {"recipe": 5, "other": 2, "ambiguous": 1}  # Introduction, Conclusion; Tips has only a label
    conn = db.connect(config.db_path)
    row = conn.execute("SELECT raw_title, author, format, status, ingested_at FROM sources").fetchone()
    assert tuple(row[:4]) == ("A Synthetic Cookbook", "Test Author", "epub", "done") and row["ingested_at"]


def test_recipe_artifacts_carry_title_locator_and_text(config):
    sample_book(config.books_dir / "b.epub", recipes=3)
    ingest.ingest(config)

    segments = {s["title"]: s for s in ArtifactsOf(config).segments()}

    recipe = segments["Recipe 2"]
    assert recipe["kind"] == "recipe" and recipe["locator"] == "part2.html#r2" and recipe["method"] == "toc"
    assert "Ingredients" in recipe["text"] and "Recipe 3" not in recipe["text"]


def test_a_second_run_does_nothing(config):
    sample_book(config.books_dir / "b.epub")
    ingest.ingest(config)
    before = snapshot(config)

    report = ingest.ingest(config)

    assert [b.outcome for b in report.books] == ["already done"]
    assert snapshot(config) == before


def test_killing_the_run_halfway_and_rerunning_finishes_only_the_missing_recipes(config, monkeypatch):
    sample_book(config.books_dir / "b.epub", recipes=5)
    original = stages.SegmentStage.run
    calls = []

    def die_on_the_fifth(self, ctx, unit):
        calls.append(unit.key)
        if len(calls) == 5:
            raise Killed
        return original(self, ctx, unit)

    monkeypatch.setattr(stages.SegmentStage, "run", die_on_the_fifth)
    with pytest.raises(Killed):
        ingest.ingest(config)
    monkeypatch.setattr(stages.SegmentStage, "run", original)

    conn = db.connect(config.db_path)
    assert conn.execute("SELECT status FROM sources").fetchone()[0] == "in_progress"
    conn.close()

    report = ingest.ingest(config)

    (book,) = report.books
    assert book.outcome == "ingested" and book.kinds["recipe"] == 5
    assert book.units_run == 8 - 4 and book.units_cached == 1 + 4  # 8 entries; the text stage and 4 segments were kept
    assert [b.outcome for b in ingest.ingest(config).books] == ["already done"]


def test_same_file_under_another_name_is_the_same_book(config):
    sample_book(config.books_dir / "a.epub")
    ingest.ingest(config)
    shutil.copy(config.books_dir / "a.epub", config.books_dir / "copy.epub")

    outcomes = {b.file: b.outcome for b in ingest.ingest(config).books}

    assert outcomes == {"a.epub": "already done", "copy.epub": "already done"}


def test_same_asin_in_a_different_file_is_left_for_a_decision(config):
    asin = '<dc:identifier opf:scheme="ASIN">B000TEST01</dc:identifier>'
    sample_book(config.books_dir / "a.epub", identifiers=asin)
    sample_book(config.books_dir / "b.epub", recipes=4, identifiers=asin)

    books = {b.file: b for b in ingest.ingest(config).books}

    assert books["a.epub"].outcome == "ingested"
    assert books["b.epub"].outcome == "skipped" and "same ISBN/ASIN as source" in books["b.epub"].detail


def test_pdfs_and_broken_epubs_are_reported_not_fatal(config):
    (config.books_dir / "scan.pdf").write_bytes(b"%PDF")
    (config.books_dir / "broken.epub").write_text("nope")
    (config.books_dir / "notes.md").write_text("ignored")
    sample_book(config.books_dir / "good.epub")

    outcomes = {b.file: b.outcome for b in ingest.ingest(config).books}

    assert outcomes == {"broken.epub": "failed", "good.epub": "ingested", "scan.pdf": "skipped"}


def test_a_book_without_a_table_of_contents_fails_with_a_clear_message(config):
    make_epub(config.books_dir / "b.epub", {"a.html": "<p>Ingredients</p>"}, [])

    (book,) = ingest.ingest(config).books

    assert book.outcome == "failed" and "no table of contents" in book.failures[0]


def test_an_unresolvable_entry_is_counted_not_dropped(config):
    make_epub(config.books_dir / "b.epub", {"a.html": "<p>Hi</p>"}, [("Ghost", "a.html#nope"), ("Hi", "a.html")])

    (book,) = ingest.ingest(config).books

    assert book.kinds == {"unresolved": 1, "other": 1}


def test_dry_run_counts_and_estimates_but_leaves_nothing_behind(config, tmp_path):
    sample_book(config.books_dir / "b.epub", recipes=4)

    report = ingest.ingest(config, dry_run=True)

    (book,) = report.books
    assert book.kinds["recipe"] == 4 and book.extraction_tokens > 0
    assert not config.db_path.exists() and not config.artifacts_dir.exists()
    assert report.estimated_cost_usd is None


def test_dry_run_prices_the_input_when_the_config_has_a_price(config):
    from dataclasses import replace

    from recetario_ingest.cost import Pricing

    sample_book(config.books_dir / "b.epub")
    priced = replace(config, model="m", pricing={"m": Pricing(1_000_000.0, 0.0)})

    report = ingest.ingest(priced, dry_run=True)

    assert report.estimated_cost_usd == pytest.approx(report.books[0].extraction_tokens)


def test_force_stage_reruns_from_that_stage_even_for_a_done_book(config):
    sample_book(config.books_dir / "b.epub", recipes=3)
    ingest.ingest(config)

    (book,) = ingest.ingest(config, force_stage="segment").books

    assert book.outcome == "ingested" and (book.units_run, book.units_cached) == (3 + 3, 1)  # 6 entries segment; text kept


def test_unknown_force_stage_is_rejected(config):
    with pytest.raises(ValueError, match="unknown stage 'bogus'"):
        ingest.ingest(config, force_stage="bogus")


def test_forcing_extract_without_a_model_is_rejected(config):
    with pytest.raises(ValueError, match="needs a model"):
        ingest.ingest(config, force_stage="extract")


def test_a_real_run_saves_a_report_but_a_do_nothing_run_does_not(config):
    sample_book(config.books_dir / "b.epub")

    ingest.ingest(config)
    ingest.ingest(config)

    (saved,) = (config.artifacts_dir / "runs").glob("*.json")
    assert json.loads(saved.read_text())["books"][0]["file"] == "b.epub"


def test_cli_dry_run_prints_the_report(config, tmp_path, capsys):
    sample_book(config.books_dir / "b.epub", recipes=4)
    settings = tmp_path / "recetario.toml"
    settings.write_text(f'books_dir = "{config.books_dir}"\ndb_path = "{config.db_path}"\nartifacts_dir = "{config.artifacts_dir}"\n')

    assert main(["ingest", "--config", str(settings), "--dry-run"]) == 0

    output = capsys.readouterr().out
    assert "dry run" in output and "segments: 4 recipes, 1 ambiguous, 2 other" in output and "would ingest" in output


def test_cli_exit_code_is_1_when_a_book_fails(config, tmp_path):
    (config.books_dir / "broken.epub").write_text("nope")
    settings = tmp_path / "recetario.toml"
    settings.write_text(f'books_dir = "{config.books_dir}"\ndb_path = "{config.db_path}"\nartifacts_dir = "{config.artifacts_dir}"\n')

    assert main(["ingest", "--config", str(settings)]) == 1
    assert main(["ingest", "--config", str(tmp_path / "missing.toml")]) == 2


class ArtifactsOf:
    def __init__(self, config):
        self.config = config

    def segments(self):
        (folder,) = (p for p in self.config.artifacts_dir.iterdir() if p.name != "runs")
        return self.stage("segment")

    def stage(self, name):
        (folder,) = (p for p in self.config.artifacts_dir.iterdir() if p.name != "runs")
        return [json.loads(p.read_text())["output"] for p in sorted((folder / name).glob("*.json"))]


def sample_llm(cost_per_call=0.0, cap=None):
    from fakes import perfect_extraction

    from recetario_ingest.extraction import default_prompts
    from recetario_ingest.llm.adapter import FakeLlm, MeteredLlm

    prompts = default_prompts()
    return prompts, MeteredLlm(FakeLlm(prompts, {"extract": perfect_extraction}, cost_per_call=cost_per_call), cap)


def test_extraction_runs_per_recipe_and_is_cached_on_a_second_run(config):
    sample_book(config.books_dir / "b.epub", recipes=3)
    prompts, llm = sample_llm(cost_per_call=0.01)

    (first,) = ingest.ingest(config, llm=llm, prompts=prompts).books
    extracted = ArtifactsOf(config).stage("extract")
    (second,) = ingest.ingest(config, llm=llm, prompts=prompts).books

    assert first.outcome == "ingested" and first.cost_usd == pytest.approx(0.03) and len(extracted) == 3
    assert {e["status"] for e in extracted} == {"ok"} and extracted[0]["extracted"]["ingredient_lines"]
    assert second.outcome == "already done" and llm.spent_usd == pytest.approx(0.03)  # no new calls


def test_a_book_ingested_without_a_model_is_extracted_once_a_model_is_added(config):
    sample_book(config.books_dir / "b.epub", recipes=3)
    ingest.ingest(config)
    prompts, llm = sample_llm()

    (book,) = ingest.ingest(config, llm=llm, prompts=prompts).books

    assert book.outcome == "ingested" and book.units_run == 3 and len(ArtifactsOf(config).stage("extract")) == 3


def test_the_spending_cap_stops_extraction_and_a_rerun_with_budget_resumes_it(config):
    sample_book(config.books_dir / "b.epub", recipes=5)
    prompts, llm = sample_llm(cost_per_call=0.4, cap=1.0)

    (stopped,) = ingest.ingest(config, llm=llm, prompts=prompts).books

    assert stopped.outcome == "failed" and "spending cap" in stopped.failures[0] and len(stopped.failures) == 1
    assert len(ArtifactsOf(config).stage("extract")) == 3  # the third call started at $0.80, under the cap

    prompts, fresh = sample_llm(cost_per_call=0.4, cap=5.0)
    (resumed,) = ingest.ingest(config, llm=fresh, prompts=prompts).books

    assert resumed.outcome == "ingested" and fresh.spent_usd == pytest.approx(0.8)  # only the two missing recipes


def test_a_recipe_the_model_gets_wrong_is_kept_for_review_not_dropped(config):
    from recetario_ingest.extraction import default_prompts
    from recetario_ingest.llm.adapter import FakeLlm

    sample_book(config.books_dir / "b.epub", recipes=2)
    prompts = default_prompts()
    llm = FakeLlm(prompts, {"extract": {"title": "x", "description": "", "meta": [], "ingredient_lines": ["9 invented"], "steps": ["s"], "equipment_mentions": [], "extras": []}})

    (book,) = ingest.ingest(config, llm=llm, prompts=prompts).books

    assert book.outcome == "ingested"
    extracted = ArtifactsOf(config).stage("extract")
    assert {e["status"] for e in extracted} == {"needs_review"} and {e["attempts"] for e in extracted} == {2}
    assert extracted[0]["issues"][0]["rule"] == "ingredient_not_in_source"


def test_extraction_only_covers_recipes_not_other_entries(config):
    sample_book(config.books_dir / "b.epub", recipes=4)
    prompts, llm = sample_llm()

    ingest.ingest(config, llm=llm, prompts=prompts)

    assert len(llm._inner.calls) == 4


def test_dry_run_never_calls_the_model_even_when_one_is_given(config):
    sample_book(config.books_dir / "b.epub")
    prompts, llm = sample_llm()

    report = ingest.ingest(config, dry_run=True, llm=llm, prompts=prompts)

    assert llm._inner.calls == [] and report.books[0].extraction_tokens > 0
