"""The `ingest` command: find books, register them and run each through the stages. Re-running is safe."""

import json
import sqlite3
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from recetario_ingest import db, epub, loader, seed, sources, stages
from recetario_ingest.artifacts import ArtifactStore
from recetario_ingest.config import Config
from recetario_ingest.cost import cost_usd, estimate_tokens
from recetario_ingest.registry import default_prompts
from recetario_ingest.llm.adapter import LlmAdapter
from recetario_ingest.llm.factory import build_llm
from recetario_ingest.llm.prompts import PromptRegistry
from recetario_ingest.pipeline import Context, Stage, run_stages

STAGE_NAMES = ("text", "segment", "extract", "lines", "resolve", "localize")


@dataclass
class BookReport:
    file: str
    outcome: str  # ingested | already done | skipped | failed
    detail: str = ""
    source_id: int | None = None
    kinds: dict[str, int] = field(default_factory=dict)  # segment kind -> count
    units_run: int = 0
    units_cached: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    web_searches: int = 0
    cost_usd: float = 0.0
    seconds: float = 0.0
    loaded: dict[str, int] = field(default_factory=dict)  # what load wrote (see loader.LoadReport)
    terms: dict[str, dict[str, int]] = field(default_factory=dict)  # kind -> {exact, auto, review} counts of unique terms
    extraction_tokens: int = 0  # what the extract stage would send as input: recipe text plus the prompt, per recipe
    failures: list[str] = field(default_factory=list)


@dataclass
class RunReport:
    dry_run: bool
    books: list[BookReport] = field(default_factory=list)
    estimated_cost_usd: float | None = None  # dry run only; None when the config has no price for the model


def scan(books_dir: Path) -> list[Path]:
    return sorted(path for path in books_dir.iterdir() if path.suffix.lower() in (".epub", ".pdf")) if books_dir.is_dir() else []


def ingest(
    config: Config,
    *,
    dry_run: bool = False,
    force_stage: str | None = None,
    llm: LlmAdapter | None = None,
    prompts: PromptRegistry | None = None,
) -> RunReport:
    """Ingest every book in the books folder. A dry run does the free, local stages in a throwaway database and
    artifact folder, never calls the model, and leaves nothing behind."""
    if force_stage is not None and force_stage not in STAGE_NAMES:
        raise ValueError(f"unknown stage {force_stage!r}; stages are {', '.join(STAGE_NAMES)}")
    report = RunReport(dry_run=dry_run)
    prompts = prompts or default_prompts()
    if force_stage == "extract" and not config.model:
        raise ValueError("the extract stage needs a model: set model under [llm] in recetario.toml")
    if llm is None and config.model and not dry_run:
        llm = build_llm(config, prompts)

    with tempfile.TemporaryDirectory() as scratch:
        if dry_run:
            conn, artifacts_dir, llm = db.connect(":memory:"), Path(scratch), None
        else:
            config.db_path.parent.mkdir(parents=True, exist_ok=True)
            conn, artifacts_dir = db.connect(config.db_path), config.artifacts_dir
        try:
            db.migrate(conn)
            seed.load_vocabulary(conn)  # idempotent; resolution needs the starter vocabulary to match against
            for path in scan(config.books_dir):
                report.books.append(_ingest_book(conn, config, artifacts_dir, path, force_stage, llm, prompts))
        finally:
            conn.close()

    if dry_run and config.model in config.pricing:
        tokens = sum(book.extraction_tokens for book in report.books)
        report.estimated_cost_usd = cost_usd(tokens, 0, config.pricing[config.model])
    if not dry_run and any(book.outcome in ("ingested", "failed") for book in report.books):
        _save_report(config.artifacts_dir, report)
    return report


def _ingest_book(conn, config, artifacts_dir, path, force_stage, llm, prompts) -> BookReport:
    if path.suffix.lower() != ".epub":
        return BookReport(path.name, "skipped", "PDF books aren't supported yet")

    started = time.monotonic()
    try:
        metadata = epub.read_metadata(path)
    except epub.EpubError as error:
        return BookReport(path.name, "failed", str(error))

    pipeline: list[Stage] = [stages.TextStage(path), stages.SegmentStage()]
    if llm is not None:
        pipeline += [stages.ExtractStage(), stages.LinesStage(), stages.ResolveStage(), stages.LocalizeStage()]
    stage_names = [stage.name for stage in pipeline]

    file_hash = sources.file_sha256(path)
    source = sources.find_by_hash(conn, file_hash)
    if source is None:
        other = sources.find_by_identifier(conn, metadata)
        if other is not None:
            detail = f"same ISBN/ASIN as source {other.id} ({other.raw_title[:40]}), but a different file; not ingested"
            return BookReport(path.name, "skipped", detail)
        source = sources.register(conn, file_hash, "epub", metadata)
    elif source.status == "done" and force_stage is None and set(stage_names) <= sources.stages_run(conn, source):
        return BookReport(path.name, "already done", source_id=source.id)

    source = sources.set_status(conn, source, "in_progress")
    store = ArtifactStore(artifacts_dir / source.slug)
    ctx = Context(source, store, prompts, llm, clock=time.monotonic, force_from=force_stage, conn=conn)
    run = run_stages(conn, ctx, pipeline)

    book = BookReport(
        path.name,
        "failed" if run.failures else "ingested",
        source_id=source.id,
        units_run=run.units_run,
        units_cached=run.units_cached,
        tokens_in=run.tokens_in,
        tokens_out=run.tokens_out,
        web_searches=run.web_searches,
        cost_usd=run.cost_usd,
        failures=run.failures,
    )
    if "segment" in run.stages and not run.stages["segment"].failed and run.stages["segment"].ok + run.stages["segment"].cached:
        segments = store.read_all("segment")
        for segment in segments:
            book.kinds[segment["kind"]] = book.kinds.get(segment["kind"], 0) + 1
        overhead = estimate_tokens(prompts.get("extract").system)
        book.extraction_tokens = sum(estimate_tokens(s["text"]) + overhead for s in segments if s["kind"] == "recipe")
    if "resolve" in run.stages and not run.stages["resolve"].failed:
        book.terms = term_counts(store.read_all("resolve"))
    if not run.failures and "localize" in run.stages:
        book.loaded = asdict(load_book(conn, source, store))
    sources.set_status(conn, source, "failed" if run.failures else "done")
    book.seconds = time.monotonic() - started
    return book


def _save_report(artifacts_dir: Path, report: RunReport) -> None:
    runs = artifacts_dir / "runs"
    runs.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    (runs / f"{stamp}.json").write_text(json.dumps(asdict(report), indent=2, ensure_ascii=False), encoding="utf-8")


def load_book(conn, source: sources.Source, store: ArtifactStore) -> loader.LoadReport:
    """Write the book's recipes into the database from its artifacts and the current vocabulary."""
    localized = {item["recipe_key"]: item for item in store.read_all("localize") if item["status"] == "ok"}
    return loader.load_recipes(conn, source.id, store.read_all("extract"), stages.split_parts(store), localized)


def load_all(config: Config) -> list[tuple[str, loader.LoadReport]]:
    """Load every finished book again, e.g. after a review pass resolved more terms. Calls no model."""
    conn = db.connect(config.db_path)
    try:
        db.migrate(conn)
        done = [sources.Source(row["id"], row["file_hash"], row["raw_title"], row["status"]) for row in conn.execute("SELECT * FROM sources WHERE status = 'done'")]
        return [(source.raw_title, load_book(conn, source, ArtifactStore(config.artifacts_dir / source.slug))) for source in done]
    finally:
        conn.close()


def term_counts(resolutions: list[dict]) -> dict[str, dict[str, int]]:
    """Per kind, how the book's unique terms were settled: matched exactly, accepted automatically, or left to review."""
    counts: dict[str, dict[str, int]] = {}
    for item in resolutions:
        row = counts.setdefault(item["kind"], {"exact": 0, "auto": 0, "review": 0})
        row["exact" if item["method"] == "exact" else "auto" if item["outcome"] == "resolved" else "review"] += 1
    return counts


def format_report(report: RunReport, config: Config) -> str:
    lines = ["dry run: nothing is written and no model is called" if report.dry_run else "ingest run"]
    if not report.books:
        lines.append(f"no books found in {config.books_dir}")
    for book in report.books:
        outcome = "would ingest" if report.dry_run and book.outcome == "ingested" else book.outcome
        lines.append(f"\n{book.file}: {outcome}" + (f" ({book.detail})" if book.detail else ""))
        if book.kinds:
            recipes = book.kinds.get("recipe", 0)
            others = ", ".join(f"{count} {kind}" for kind, count in sorted(book.kinds.items()) if kind != "recipe")
            lines.append(f"  segments: {recipes} recipes" + (f", {others}" if others else ""))
        if report.dry_run and book.extraction_tokens:
            lines.append(f"  extraction input: ~{book.extraction_tokens:,} tokens (recipe text plus the prompt, per recipe)")
        if book.outcome in ("ingested", "failed") and not report.dry_run:
            lines.append(
                f"  units: {book.units_run} run, {book.units_cached} cached · "
                f"tokens {book.tokens_in:,} in / {book.tokens_out:,} out · "
                f"web searches {book.web_searches} · cost ${book.cost_usd:.4f} · {book.seconds:.1f}s"
            )
        if book.loaded:
            lines.append(
                f"  loaded: {book.loaded['recipes']} recipes · {book.loaded['lines']} ingredient lines "
                f"({book.loaded['unresolved_lines']} unresolved) · {book.loaded['equipment']} equipment · {book.loaded['attributes']} attributes"
            )
        for kind, row in sorted(book.terms.items()):
            total = sum(row.values())
            lines.append(
                f"  {kind} terms: {total} unique · {row['exact']} known ({row['exact'] / total:.0%}) · "
                f"{row['auto']} auto-accepted ({row['auto'] / total:.0%}) · {row['review']} to review ({row['review'] / total:.0%})"
            )
        lines += [f"  FAILED {failure}" for failure in book.failures]
    if report.dry_run and report.books:
        if report.estimated_cost_usd is not None:
            lines.append(f"\nestimated input cost for extraction: ${report.estimated_cost_usd:.2f} (output not included)")
        else:
            lines.append("\nno price for the configured model, so no cost estimate; add [pricing.\"<model>\"] to recetario.toml")
    if report.books and not config.model:
        lines.append("\nextraction skipped: no model configured (set model under [llm] in recetario.toml)")
    return "\n".join(lines)
