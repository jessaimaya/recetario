"""Command line entry point: `recetario-ingest db init`."""

import argparse
import json
import sys
from dataclasses import asdict, replace
from datetime import date
from pathlib import Path

import anthropic

from recetario_ingest import config as config_module
from recetario_ingest import db, evaluate, ingest, review, seed
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

    ev = commands.add_parser("eval", help="score a prompt against the golden set")
    ev_commands = ev.add_subparsers(dest="eval_command", required=True)
    ev_extract = ev_commands.add_parser("extract", help="score extraction; calls the model unless --dry-run")
    ev_extract.add_argument("--config", type=Path)
    ev_extract.add_argument("--golden", type=Path, action="append", help="golden folder, repeatable (default: local/evals/extraction and evals/extraction)")
    ev_extract.add_argument("--model", help="model to use instead of the one in the config")
    ev_extract.add_argument("--prompt", help="prompt version (default: the latest)")
    ev_extract.add_argument("--dry-run", action="store_true", help="count recipes and estimate input cost; call nothing")

    review_parser = commands.add_parser("review", help="decide open vocabulary proposals, or spot-check auto-accepted aliases")
    review_parser.add_argument("--config", type=Path)
    review_parser.add_argument("--kind", choices=("ingredient", "equipment", "attribute_key"))
    review_parser.add_argument("--auto", action="store_true", help="spot-check the aliases accepted automatically instead")

    args = parser.parse_args(argv)
    config_module.load_dotenv()
    if args.command == "db" and args.db_command == "init":
        return _db_init(args.db, fresh=args.fresh, fixtures=args.fixtures)
    if args.command == "ingest":
        return _ingest(args.config, dry_run=args.dry_run, force_stage=args.force_stage)
    if args.command == "review":
        return _review(args.config, args.kind, args.auto)
    if args.command == "eval":
        return _eval_extract(args.config, args.golden, args.prompt, args.dry_run, args.model)
    return 1


def _review(config_path: Path | None, kind: str | None, auto: bool) -> int:
    try:
        config = config_module.load_config(config_path)
    except config_module.ConfigError as error:
        print(error, file=sys.stderr)
        return 2
    conn = db.connect(config.db_path)
    try:
        counts = (review.spot_check_auto if auto else review.review_proposals)(conn, kind)
    except (EOFError, KeyboardInterrupt):
        print("\nstopped; decisions made so far are saved")
        return 0
    finally:
        conn.close()
    print("done: " + ", ".join(f"{count} {name}" for name, count in counts.items()))
    return 0


def _eval_extract(config_path: Path | None, golden: list[Path] | None, version: str | None, dry_run: bool, model: str | None) -> int:
    try:
        config = config_module.load_config(config_path)
        if model:
            config = replace(config, model=model)
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


def _ingest(config_path: Path | None, dry_run: bool, force_stage: str | None) -> int:
    try:
        config = config_module.load_config(config_path)
    except config_module.ConfigError as error:
        print(error, file=sys.stderr)
        return 2
    report = ingest.ingest(config, dry_run=dry_run, force_stage=force_stage)
    print(ingest.format_report(report, config))
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
