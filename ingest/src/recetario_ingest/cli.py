"""Command line entry point: `recetario-ingest db init`."""

import argparse
import json
import sys
from dataclasses import asdict, replace
from datetime import date
from pathlib import Path

import anthropic

from recetario_ingest import config as config_module
from recetario_ingest import db, evaluate, evaluate_resolve, ingest, review, seed
from recetario_ingest.cost import cost_usd, estimate_tokens
from recetario_ingest.registry import default_prompts
from recetario_ingest.llm.adapter import BudgetExceeded
from recetario_ingest.llm.factory import build_llm


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="recetario-ingest")
    commands = parser.add_subparsers(dest="command", required=True)

    db_parser = commands.add_parser("db", help="manage the recetario database")
    db_commands = db_parser.add_subparsers(dest="db_command", required=True)
    init = db_commands.add_parser("init", help="create or migrate the database and load the vocabulary seed")
    init.add_argument("--db", type=Path, default=db.DEFAULT_DB_PATH, help="database file")
    init.add_argument("--fresh", action="store_true", help="delete the database file first")
    init.add_argument("--fixtures", action="store_true", help="also load the hand-written test recipes")

    run = commands.add_parser("ingest", help="ingest every book in the books folder; safe to re-run")
    run.add_argument("--config", type=Path, help="config file (default: recetario.toml in the repo, if it exists)")
    run.add_argument("--dry-run", action="store_true", help="count segments and estimate tokens and cost; write nothing, call nothing")
    run.add_argument("--force-stage", choices=ingest.STAGE_NAMES, help="re-run this stage and the ones after it")
    run.add_argument("--one-by-one", action="store_true", help="size up each book locally, ask before it is sent to the model, and show what it used afterwards")

    run.add_argument("--book", help="only books whose file name contains this text (with --one-by-one)")

    load_parser = commands.add_parser("load", help="write every finished book's recipes into the database again (after a review pass); calls no model")
    load_parser.add_argument("--config", type=Path)

    ev = commands.add_parser("eval", help="score a prompt against the golden set")
    ev_commands = ev.add_subparsers(dest="eval_command", required=True)
    ev_extract = ev_commands.add_parser("extract", help="score extraction; calls the model unless --dry-run")
    ev_extract.add_argument("--config", type=Path)
    ev_extract.add_argument("--golden", type=Path, action="append", help="golden folder, repeatable (default: local/evals/extraction and evals/extraction)")
    ev_extract.add_argument("--model", help="model to use instead of the one in the config")
    ev_extract.add_argument("--provider", choices=("anthropic", "claude-code"), help="provider to use instead of the one in the config")
    ev_extract.add_argument("--prompt", help="prompt version (default: the latest)")
    ev_extract.add_argument("--dry-run", action="store_true", help="count recipes and estimate input cost; call nothing")

    ev_resolve = ev_commands.add_parser("resolve", help="score the resolver on golden terms (the NAMING.md trap list and more)")
    ev_resolve.add_argument("--config", type=Path)
    ev_resolve.add_argument("--cases", type=Path, help="folder of case files (default: evals/resolution)")
    ev_resolve.add_argument("--model", help="model to use instead of the one in the config")
    ev_resolve.add_argument("--provider", choices=("anthropic", "claude-code"), help="provider to use instead of the one in the config")
    ev_resolve.add_argument("--prompt", help="prompt version (default: the latest)")

    review_parser = commands.add_parser("review", help="decide open vocabulary proposals, or spot-check auto-accepted aliases")
    review_parser.add_argument("--config", type=Path)
    review_parser.add_argument("--kind", choices=("ingredient", "equipment", "attribute_key"))
    review_parser.add_argument("--auto", action="store_true", help="spot-check the aliases accepted automatically instead")
    review_parser.add_argument("--duplicates", action="store_true", help="review recipes that look like the same dish instead")

    args = parser.parse_args(argv)
    config_module.load_dotenv()
    if args.command == "db" and args.db_command == "init":
        return _db_init(args.db, fresh=args.fresh, fixtures=args.fixtures)
    if args.command == "ingest":
        return _ingest(args.config, dry_run=args.dry_run, force_stage=args.force_stage, one_by_one=args.one_by_one, only=args.book)
    if args.command == "load":
        return _load(args.config)
    if args.command == "review":
        return _review(args.config, args.kind, args.auto, args.duplicates)
    if args.command == "eval" and args.eval_command == "resolve":
        return _eval_resolve(args.config, args.cases, args.prompt, args.model, args.provider)
    if args.command == "eval":
        return _eval_extract(args.config, args.golden, args.prompt, args.dry_run, args.model, args.provider)
    return 1


def _review(config_path: Path | None, kind: str | None, auto: bool, duplicate_pairs: bool = False) -> int:
    try:
        config = config_module.load_config(config_path)
    except config_module.ConfigError as error:
        print(error, file=sys.stderr)
        return 2
    conn = db.connect(config.db_path)
    try:
        if duplicate_pairs:
            counts = review.review_duplicates(conn)
        else:
            counts = (review.spot_check_auto if auto else review.review_proposals)(conn, kind)
    except (EOFError, KeyboardInterrupt):
        print("\nstopped; decisions made so far are saved")
        return 0
    finally:
        conn.close()
    print("done: " + ", ".join(f"{count} {name}" for name, count in counts.items()))
    return 0


def _eval_extract(config_path: Path | None, golden: list[Path] | None, version: str | None, dry_run: bool, model: str | None, provider: str | None = None) -> int:
    try:
        config = config_module.load_config(config_path)
        if model:
            config = replace(config, model=model)
        if provider:
            config = replace(config, provider=provider)
        prompts = default_prompts()
        version = version or prompts.get("extract").version
        folders = golden or [config.evals_dir / "extraction", db.REPO_ROOT / "evals" / "extraction"]
        cases = evaluate.load_golden(*folders)
        if not cases:
            print("no golden recipes found in: " + ", ".join(str(f) for f in folders), file=sys.stderr)
            return 2
        unreviewed = sum(not case.reviewed for case in cases)
        if dry_run:
            tokens = sum(estimate_tokens(case.text) + estimate_tokens(prompts.get("extract", version).system) for case in cases)
            print(f"{len(cases)} golden recipes ({unreviewed} unreviewed), prompt {version}: ~{tokens:,} input tokens")
            if config.model in config.pricing:
                print(f"estimated input cost: ${cost_usd(tokens, 0, config.pricing[config.model]):.4f} (output not included)")
            return 0
        llm = build_llm(config, prompts)
        scores = evaluate.run_eval(llm, cases, version)
    except config_module.ConfigError as error:
        print(error, file=sys.stderr)
        return 2
    except (BudgetExceeded, anthropic.AnthropicError) as error:
        print(f"eval stopped: {error}", file=sys.stderr)
        return 1

    summary = evaluate.summarize(scores)
    row = evaluate.results_row(summary, date.today().isoformat(), version, config.model)
    print(evaluate.format_eval(scores, summary, row, unreviewed))
    config.evals_dir.mkdir(parents=True, exist_ok=True)
    with (config.evals_dir / "results.jsonl").open("a", encoding="utf-8") as results:
        entry = {"date": date.today().isoformat(), "prompt": version, "model": config.model, "summary": summary, "cases": [asdict(s) for s in scores]}
        results.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return 0


def _eval_resolve(config_path: Path | None, cases_dir: Path | None, version: str | None, model: str | None, provider: str | None = None) -> int:
    try:
        config = config_module.load_config(config_path)
        if model:
            config = replace(config, model=model)
        if provider:
            config = replace(config, provider=provider)
        prompts = default_prompts()
        version = version or prompts.get("resolve").version
        folder = cases_dir or db.REPO_ROOT / "evals" / "resolution"
        cases = evaluate_resolve.load_cases(folder)
        if not cases:
            print(f"no resolution cases found in {folder}", file=sys.stderr)
            return 2
        llm = build_llm(config, prompts)
        scores = evaluate_resolve.run_cases(llm, cases, version)
    except config_module.ConfigError as error:
        print(error, file=sys.stderr)
        return 2
    except (BudgetExceeded, anthropic.AnthropicError) as error:
        print(f"eval stopped: {error}", file=sys.stderr)
        return 1

    summary = evaluate_resolve.summarize(scores)
    sweep = evaluate_resolve.threshold_sweep(scores)
    print(evaluate_resolve.format_report(scores, summary, sweep, version, config.model))
    config.evals_dir.mkdir(parents=True, exist_ok=True)
    with (config.evals_dir / "results.jsonl").open("a", encoding="utf-8") as results:
        entry = {"eval": "resolve", "date": date.today().isoformat(), "prompt": version, "model": config.model, "summary": summary, "sweep": sweep, "cases": [asdict(s) for s in scores]}
        results.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return 0


def _load(config_path: Path | None) -> int:
    try:
        config = config_module.load_config(config_path)
    except config_module.ConfigError as error:
        print(error, file=sys.stderr)
        return 2
    results = ingest.load_all(config)
    if not results:
        print("no finished books to load; run ingest first")
    for title, report in results:
        print(
            f"{title}: {report.recipes} recipes · {report.lines} ingredient lines ({report.unresolved_lines} unresolved) · "
            f"{report.equipment} equipment · {report.attributes} attributes · {report.translations} translations"
        )
    return 0


def _ask_approval(book: "ingest.BookReport") -> str:
    print(f"\n{book.file}")
    if book.outcome == "failed":
        print(f"  cannot be read: {book.detail}")
        return "skip"
    print(f"  {book.detail}")
    while True:
        answer = input("  ingest this book? [y]es / [s]kip / [q]uit: ").strip().lower()
        if answer in ("y", "yes"):
            return "yes"
        if answer in ("s", "skip", "n", "no"):
            return "skip"
        if answer in ("q", "quit"):
            return "quit"


def _ingest(config_path: Path | None, dry_run: bool, force_stage: str | None, one_by_one: bool = False, only: str | None = None) -> int:
    try:
        config = config_module.load_config(config_path)
    except config_module.ConfigError as error:
        print(error, file=sys.stderr)
        return 2
    if one_by_one and not sys.stdin.isatty():
        print("--one-by-one asks for approval on the terminal; run it in an interactive shell", file=sys.stderr)
        return 2
    if only is not None and not one_by_one:
        print("--book works with --one-by-one", file=sys.stderr)
        return 2
    if one_by_one and dry_run:
        print("--dry-run already stops before any model call; drop --one-by-one", file=sys.stderr)
        return 2
    if one_by_one:
        return _ingest_one_by_one(config, force_stage, only)
    report = ingest.ingest(config, dry_run=dry_run, force_stage=force_stage)
    print(ingest.format_report(report, config))
    return 1 if any(book.outcome == "failed" for book in report.books) else 0


def _ingest_one_by_one(config: config_module.Config, force_stage: str | None, only: str | None) -> int:
    """Each book: sized up for free, approved by the person, ingested, then what it used and the running total."""
    totals = {"books": 0, "in": 0, "out": 0, "usd": 0.0, "seconds": 0.0}

    def show(book: "ingest.BookReport") -> None:
        print(ingest.format_report(ingest.RunReport(False, [book]), config).split("\n", 1)[1])
        if book.outcome in ("ingested", "failed"):
            totals["books"] += 1
            totals["in"] += book.tokens_in
            totals["out"] += book.tokens_out
            totals["usd"] += book.cost_usd
            totals["seconds"] += book.seconds
            price = "list price, covered by your plan" if config.provider == "claude-code" else "billed to your API key"
            print(
                f"  running total: {totals['books']} book(s) · tokens {totals['in']:,} in / {totals['out']:,} out · "
                f"${totals['usd']:.4f} ({price}) · {totals['seconds'] / 60:.1f} min"
            )

    report = ingest.ingest(config, force_stage=force_stage, approve=_ask_approval, show=show, only=only)
    if not report.books:
        print(f"no books found in {config.books_dir}" if not ingest.scan(config.books_dir) else "stopped; nothing was sent to the model")
    return 1 if any(book.outcome == "failed" for book in report.books) else 0


def _db_init(path: Path, fresh: bool, fixtures: bool) -> int:
    if fresh:
        path.unlink(missing_ok=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = db.connect(path)
    try:
        applied = db.migrate(conn)
        counts = seed.load_vocabulary(conn)
        if fixtures:
            recipe_ids = seed.load_fixture_recipes(conn)
            counts["recipe"] = len(recipe_ids)
    finally:
        conn.close()

    print(f"{path}: schema version {db.latest_version()}", end="")
    print(f" (applied {', '.join(map(str, applied))})" if applied else " (up to date)")
    print("loaded: " + ", ".join(f"{count} {kind}" for kind, count in counts.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
