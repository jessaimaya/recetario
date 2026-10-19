import sqlite3

import pytest

from recetario_ingest import db
from recetario_ingest.cli import main


def test_migrate_sets_user_version_and_is_idempotent(tmp_path):
    conn = db.connect(tmp_path / "x.db")
    assert db.migrate(conn) == list(range(1, db.latest_version() + 1))
    assert db.current_version(conn) == db.latest_version()
    assert db.migrate(conn) == []


def test_foreign_keys_are_enforced(conn):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO raw_lines (recipe_id, section, position, text_original) VALUES (999, 'steps', 0, 'x')"
        )


def test_failed_migration_rolls_back(tmp_path):
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    (migrations / "0001_ok.sql").write_text("CREATE TABLE a (id INTEGER PRIMARY KEY);")
    (migrations / "0002_broken.sql").write_text("CREATE TABLE b (id INTEGER PRIMARY KEY); SELECT nope FROM missing;")
    conn = db.connect(tmp_path / "x.db")

    with pytest.raises(sqlite3.OperationalError):
        db.migrate(conn, migrations)

    assert db.current_version(conn) == 1
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert tables == {"a"}


def test_migration_names_must_be_numbered_without_gaps(tmp_path):
    (tmp_path / "0002_late.sql").write_text("")
    with pytest.raises(ValueError):
        db.migrations(tmp_path)


def test_cli_builds_database_from_scratch(tmp_path, capsys):
    path = tmp_path / "local" / "recetario.db"
    assert main(["db", "init", "--db", str(path), "--fixtures"]) == 0
    assert main(["db", "init", "--db", str(path), "--fresh", "--fixtures"]) == 0

    conn = db.connect(path)
    assert db.current_version(conn) == db.latest_version()
    assert conn.execute("SELECT count(*) FROM recipes").fetchone()[0] == 10
    assert "10 recipe" in capsys.readouterr().out
