"""Command line entry point: `recetario-ingest db init`."""

import argparse
import sys
from pathlib import Path

from recetario_ingest import db, seed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="recetario-ingest")
    commands = parser.add_subparsers(dest="command", required=True)

    db_parser = commands.add_parser("db", help="manage the recetario database")
    db_commands = db_parser.add_subparsers(dest="db_command", required=True)
    init = db_commands.add_parser("init", help="create or migrate the database and load the vocabulary seed")
    init.add_argument("--db", type=Path, default=db.DEFAULT_DB_PATH, help="database file")
    init.add_argument("--fresh", action="store_true", help="delete the database file first")
    init.add_argument("--fixtures", action="store_true", help="also load the hand-written test recipes")

    args = parser.parse_args(argv)
    if args.command == "db" and args.db_command == "init":
        return _db_init(args.db, fresh=args.fresh, fixtures=args.fixtures)
    return 1


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
