"""Command line entry point: `recetario-ingest db init`."""

import argparse
import json
import shutil
import sys
import time
from dataclasses import asdict, replace
from datetime import date
from pathlib import Path

import anthropic

from recetario_ingest import config as config_module
from recetario_ingest import db, evaluate, evaluate_resolve, ingest, review, seed, spend
from recetario_ingest import merge as merge_module
from recetario_ingest.progress import ProgressPrinter
from recetario_ingest.cost import cost_usd, estimate_tokens
from recetario_ingest.registry import default_prompts
from recetario_ingest.llm.adapter import BudgetExceeded
from recetario_ingest.llm.factory import build_llm


def _add_llm_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--profile", help="named [profiles.<name>] from the config: provider, model, effort, cap and workers together")
    parser.add_argument("--provider", choices=("anthropic", "claude-code", "openrouter"), help="provider for this run (needs --model unless it is the config's)")
    parser.add_argument("--model", help="model for this run: an API id, a Claude Code alias such as haiku, or an OpenRouter id")
    parser.add_argument("--workers", type=int, help="model calls in flight at once within a stage")


def _llm_choice(args: argparse.Namespace) -> dict:
    return {"profile": args.profile, "provider": args.provider, "model": args.model, "workers": args.workers}


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
    _add_llm_options(run)

    queue = commands.add_parser("queue", help="list the books in the queue file (local/queue.txt) and where each stands")
    queue.add_argument("--config", type=Path)
    nxt = commands.add_parser("next", help="take the first waiting book of the queue, size it up, ask, ingest it and show what it used")
    nxt.add_argument("--config", type=Path)
    _add_llm_options(nxt)

    costs = commands.add_parser("costs", help="what each book cost to ingest, over all its runs and resumes; calls no model")
    costs.add_argument("--config", type=Path)
    costs.add_argument("--book", help="only books whose title contains this text")
    costs.add_argument("--by-stage", action="store_true", help="also split each book's cost by stage")

    load_parser = commands.add_parser("load", help="write every finished book's recipes into the database again (after a review pass); calls no model")
    load_parser.add_argument("--config", type=Path)

    classify_parser = commands.add_parser("classify", help="give the recipes of every ingested book a course (main, side, dessert...); runs only that stage")
    classify_parser.add_argument("--config", type=Path)
    _add_llm_options(classify_parser)

    ev = commands.add_parser("eval", help="score a prompt against the golden set")
    ev_commands = ev.add_subparsers(dest="eval_command", required=True)
    ev_extract = ev_commands.add_parser("extract", help="score extraction; calls the model unless --dry-run")
    ev_extract.add_argument("--config", type=Path)
    ev_extract.add_argument("--golden", type=Path, action="append", help="golden folder, repeatable (default: local/evals/extraction and evals/extraction)")
    ev_extract.add_argument("--model", help="model to use instead of the one in the config")
    ev_extract.add_argument("--provider", choices=("anthropic", "claude-code", "openrouter"), help="provider to use instead of the one in the config")
    ev_extract.add_argument("--prompt", help="prompt version (default: the latest)")
    ev_extract.add_argument("--dry-run", action="store_true", help="count recipes and estimate input cost; call nothing")

    ev_resolve = ev_commands.add_parser("resolve", help="score the resolver on golden terms (the naming trap list and more)")
    ev_resolve.add_argument("--config", type=Path)
    ev_resolve.add_argument("--cases", type=Path, help="folder of case files (default: evals/resolution)")
    ev_resolve.add_argument("--model", help="model to use instead of the one in the config")
    ev_resolve.add_argument("--provider", choices=("anthropic", "claude-code", "openrouter"), help="provider to use instead of the one in the config")
    ev_resolve.add_argument("--prompt", help="prompt version (default: the latest)")

    review_parser = commands.add_parser("review", help="decide open vocabulary proposals, or spot-check auto-accepted aliases")
    review_parser.add_argument("--config", type=Path)
    review_parser.add_argument("--kind", choices=("ingredient", "equipment", "attribute_key"))
    review_parser.add_argument("--auto", action="store_true", help="spot-check the aliases accepted automatically instead")
    review_parser.add_argument("--accept-above", type=float, metavar="CONFIDENCE", help="accept every open proposal at or above this confidence (shows what it would do unless --apply)")
    review_parser.add_argument("--apply", action="store_true", help="with --accept-above: really accept them, after backing up the database")
    review_parser.add_argument("--duplicates", action="store_true", help="review recipes that look like the same dish instead")

    vocab_parser = commands.add_parser("vocab", help="tidy the ingredient vocabulary")
    vocab_commands = vocab_parser.add_subparsers(dest="vocab_command", required=True)
    clashes = vocab_commands.add_parser("clashes", help="list ingredients that share the same Spanish label (the app asks about each)")
    clashes.add_argument("--config", type=Path)
    merge = vocab_commands.add_parser("merge", help="fold an ingredient into one that means the same (shows what it would do unless --apply)")
    merge.add_argument("--config", type=Path)
    merge.add_argument("source", help="English name of the entry to fold away")
    merge.add_argument("target", help="English name of the entry to keep")
    merge.add_argument("--apply", action="store_true", help="really merge, after backing up the database")

    args = parser.parse_args(argv)
    config_module.load_dotenv()
    if args.command == "db" and args.db_command == "init":
        return _db_init(args.db, fresh=args.fresh, fixtures=args.fixtures)
    if args.command == "ingest":
        return _ingest(args.config, dry_run=args.dry_run, force_stage=args.force_stage, one_by_one=args.one_by_one, only=args.book, choice=_llm_choice(args))
    if args.command == "queue":
        return _queue(args.config)
    if args.command == "next":
        return _next(args.config, _llm_choice(args))
    if args.command == "costs":
        return _costs(args.config, args.book, args.by_stage)
    if args.command == "load":
        return _load(args.config)
    if args.command == "classify":
        return _classify(args.config, _llm_choice(args))
    if args.command == "vocab":
        return _vocab(args)
    if args.command == "review":
        return _review(args.config, args.kind, args.auto, args.duplicates, args.accept_above, args.apply)
    if args.command == "eval" and args.eval_command == "resolve":
        return _eval_resolve(args.config, args.cases, args.prompt, args.model, args.provider)
    if args.command == "eval":
        return _eval_extract(args.config, args.golden, args.prompt, args.dry_run, args.model, args.provider)
    return 1


def _costs(config_path: Path | None, only: str | None, by_stage: bool) -> int:
    config = _read_config(config_path)
    if config is None:
        return 2
    if not config.db_path.exists():
        print(f"no database at {config.db_path}; nothing spent yet")
        return 0
    conn = db.connect(config.db_path)
    try:
        print(spend.format_spend(spend.book_spend(conn, only), by_stage))
    finally:
        conn.close()
    return 0


def _classify(config_path: Path | None, choice: dict) -> int:
    config = _read_config(config_path, choice)
    if config is None:
        return 2
    print(f"model: {config_module.describe_llm(config)}", file=sys.stderr)

    def show(title: str, run) -> None:
        totals = run.stages.get("classify")
        print(f"{title[:60]}: {totals.ok} classified, {totals.cached} already done, {totals.failed} failed · ${run.cost_usd:.4f}")
        for failure in run.failures:
            print(f"  {failure}", file=sys.stderr)

    try:
        results = ingest.classify_books(config, show, progress=ProgressPrinter(sys.stderr))
    except (config_module.ConfigError, ValueError) as error:
        print(error, file=sys.stderr)
        return 2
    conn = db.connect(config.db_path)
    try:
        counts = conn.execute("SELECT COALESCE(course, '(sin curso)'), COUNT(*) FROM recipes GROUP BY 1 ORDER BY 2 DESC").fetchall()
    finally:
        conn.close()
    print("\n" + (", ".join(f"{name} {count}" for name, count in counts) if results else "no finished books to classify"))
    return 0


def _vocab(args: argparse.Namespace) -> int:
    config = _read_config(args.config)
    if config is None:
        return 2
    if args.vocab_command == "merge" and args.apply:
        backup = config.db_path.with_name(f"{config.db_path.name}.bak-{time.strftime('%Y%m%dT%H%M%S')}")
        shutil.copy2(config.db_path, backup)
        print(f"backup: {backup}")
    conn = db.connect(config.db_path)
    try:
        if args.vocab_command == "clashes":
            clash_list = merge_module.label_clashes(conn, config.locale)
            for label, names in clash_list:
                print(f"{label}: {', '.join(names)}")
            print(f"\n{len(clash_list)} shared labels" if clash_list else "no shared labels")
            return 0
        try:
            report = merge_module.merge_ingredients(conn, args.source, args.target, args.apply)
        except merge_module.MergeError as error:
            print(error, file=sys.stderr)
            return 2
    finally:
        conn.close()
    verb = "merged" if args.apply else "would merge"
    print(
        f"{verb} {args.source!r} into {args.target!r}: {report.lines} recipe lines, {report.aliases} aliases, "
        f"{report.children} children, {report.proposals} proposals{', kept its buying answer' if report.availability_kept else ''}"
    )
    if not args.apply:
        print("nothing was changed; add --apply to do it (a backup of the database is made first)")
    return 0


def _review(config_path: Path | None, kind: str | None, auto: bool, duplicate_pairs: bool = False, accept_above: float | None = None, apply: bool = False) -> int:
    try:
        config = config_module.load_config(config_path)
    except config_module.ConfigError as error:
        print(error, file=sys.stderr)
        return 2
    if apply and accept_above is None:
        print("--apply goes with --accept-above", file=sys.stderr)
        return 2
    if apply:
        backup = config.db_path.with_name(f"{config.db_path.name}.bak-{time.strftime('%Y%m%dT%H%M%S')}")
        shutil.copy2(config.db_path, backup)
        print(f"backup: {backup}")
    conn = db.connect(config.db_path)
    try:
        if accept_above is not None:
            counts = review.accept_above(conn, accept_above, kind, apply)
        elif duplicate_pairs:
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


def _ingest(config_path: Path | None, dry_run: bool, force_stage: str | None, one_by_one: bool = False, only: str | None = None, choice: dict | None = None) -> int:
    config = _read_config(config_path, choice)
    if config is None:
        return 2
    if not dry_run and config.model:
        print(f"model: {config_module.describe_llm(config)}", file=sys.stderr)
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
        picked = None if only is None else [path for path in ingest.scan(config.books_dir) if only.lower() in path.name.lower()]
        return _ingest_one_by_one(config, force_stage, picked)
    report = ingest.ingest(config, dry_run=dry_run, force_stage=force_stage, progress=None if dry_run else ProgressPrinter(sys.stderr))
    print(ingest.format_report(report, config))
    return 1 if any(book.outcome == "failed" for book in report.books) else 0


def _read_config(config_path: Path | None, choice: dict | None = None) -> config_module.Config | None:
    try:
        config = config_module.load_config(config_path)
        return config_module.choose_llm(config, **choice) if choice else config
    except config_module.ConfigError as error:
        print(error, file=sys.stderr)
        return None


def _queue(config_path: Path | None) -> int:
    config = _read_config(config_path)
    if config is None:
        return 2
    rows = ingest.queue_status(config)
    if not rows:
        print(f"the queue is empty: add file names, one per line, to {config.queue_path}")
        return 0
    first = next((name for name, state in rows if state == "waiting"), None)
    for number, (name, state) in enumerate(rows, 1):
        print(f"{number:3}. {state:8} {name}" + ("   <- next" if name == first else ""))
    return 0


def _next(config_path: Path | None, choice: dict | None = None) -> int:
    """The first waiting book of the queue. A book you skip, or one with no recipes, passes to the one after it."""
    config = _read_config(config_path, choice)
    if config is None:
        return 2
    print(f"model: {config_module.describe_llm(config)}", file=sys.stderr)
    if not sys.stdin.isatty():
        print("next asks for approval on the terminal; run it in an interactive shell", file=sys.stderr)
        return 2
    rows = ingest.queue_status(config)
    if not rows:
        print(f"the queue is empty: add file names, one per line, to {config.queue_path}")
        return 0
    for name, state in rows:
        if state == "missing":
            print(f"not in {config.books_dir}, passed over: {name}", file=sys.stderr)
    waiting = [config.books_dir / name for name, state in rows if state == "waiting"]
    if not waiting:
        print("every book in the queue is done")
        return 0
    return _ingest_one_by_one(config, None, waiting, stop_after=1)


def _ingest_one_by_one(config: config_module.Config, force_stage: str | None, books: list[Path] | None = None, stop_after: int | None = None) -> int:
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

    report = ingest.ingest(config, force_stage=force_stage, approve=_ask_approval, show=show, books=books, stop_after=stop_after, progress=ProgressPrinter(sys.stderr))
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
