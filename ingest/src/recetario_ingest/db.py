"""SQLite connection and schema migrations.

Migrations are the SQL files in schema/migrations, named NNNN_description.sql. The applied version is
stored in PRAGMA user_version, so the TypeScript app can check it without any extra table.
"""

import re
import sqlite3
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SCHEMA_DIR = REPO_ROOT / "schema"
MIGRATIONS_DIR = SCHEMA_DIR / "migrations"
DEFAULT_DB_PATH = REPO_ROOT / "local" / "recetario.db"

_MIGRATION_NAME = re.compile(r"^(\d{4})_[a-z0-9_]+\.sql$")


def connect(path: Path | str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, check_same_thread=False)  # ingest workers share it, behind Context.db_lock
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def migrations(directory: Path = MIGRATIONS_DIR) -> list[tuple[int, Path]]:
    found = []
    for path in sorted(directory.glob("*.sql")):
        match = _MIGRATION_NAME.match(path.name)
        if not match:
            raise ValueError(f"migration file name must look like 0001_name.sql: {path.name}")
        found.append((int(match.group(1)), path))
    versions = [version for version, _ in found]
    if versions != list(range(1, len(versions) + 1)):
        raise ValueError(f"migration versions must be 1..N without gaps, found {versions}")
    return found


def latest_version(directory: Path = MIGRATIONS_DIR) -> int:
    return len(migrations(directory))


def current_version(conn: sqlite3.Connection) -> int:
    return conn.execute("PRAGMA user_version").fetchone()[0]


def migrate(conn: sqlite3.Connection, directory: Path = MIGRATIONS_DIR) -> list[int]:
    """Apply pending migrations, each in its own transaction. Returns the versions applied."""
    applied = []
    current = current_version(conn)
    for version, path in migrations(directory):
        if version <= current:
            continue
        sql = path.read_text(encoding="utf-8")
        try:
            conn.executescript(f"BEGIN;\n{sql}\nPRAGMA user_version = {version};\nCOMMIT;")
        except sqlite3.Error:
            conn.rollback()
            raise
        applied.append(version)
    return applied
