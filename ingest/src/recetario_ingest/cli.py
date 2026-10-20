"""Command line entry point: `recetario-ingest db init`."""

import argparse
import sys
from pathlib import Path

from recetario_ingest import config as config_module
from recetario_ingest import db, ingest, seed


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

    args = parser.parse_args(argv)
    if args.command == "db" and args.db_command == "init":
        return _db_init(args.db, fresh=args.fresh, fixtures=args.fixtures)
    if args.command == "ingest":
        return _ingest(args.config, dry_run=args.dry_run, force_stage=args.force_stage)
    return 1


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
