import pytest

from recetario_ingest import db, seed


@pytest.fixture
def conn(tmp_path):
    connection = db.connect(tmp_path / "test.db")
    db.migrate(connection)
    seed.load_vocabulary(connection)
    yield connection
    connection.close()


@pytest.fixture
def recipe_ids(conn):
    return seed.load_fixture_recipes(conn)
