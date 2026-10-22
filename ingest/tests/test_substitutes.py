import pytest

from recetario_ingest import db, seed


@pytest.fixture
def conn():
    connection = db.connect(":memory:")
    db.migrate(connection)
    yield connection
    connection.close()


def groups(conn):
    return dict(conn.execute("SELECT name_en, substitute_group FROM vocab_entries WHERE substitute_group IS NOT NULL").fetchall())


def seed_file(tmp_path, body):
    path = tmp_path / "vocabulary.toml"
    path.write_text(
        '[[ingredient]]\nen = "olive oil"\n[[ingredient]]\nen = "canola oil"\n[[ingredient]]\nen = "sesame oil"\n' + body,
        encoding="utf-8",
    )
    return path


def test_members_that_exist_get_the_group_and_missing_ones_are_skipped(conn, tmp_path):
    path = seed_file(tmp_path, '[[substitute_group]]\nname = "cooking oil"\nmembers = ["olive oil", "canola oil", "palm oil"]\n')

    counts = seed.load_vocabulary(conn, path)

    assert groups(conn) == {"olive oil": "cooking oil", "canola oil": "cooking oil"}
    assert counts["substitute"] == 2


def test_the_seed_is_the_only_source_so_a_removed_member_loses_its_group(conn, tmp_path):
    seed.load_vocabulary(conn, seed_file(tmp_path, '[[substitute_group]]\nname = "cooking oil"\nmembers = ["olive oil", "canola oil"]\n'))

    seed.load_vocabulary(conn, seed_file(tmp_path, '[[substitute_group]]\nname = "cooking oil"\nmembers = ["olive oil"]\n'))

    assert groups(conn) == {"olive oil": "cooking oil"}


def test_an_ingredient_in_two_groups_is_refused(conn, tmp_path):
    path = seed_file(
        tmp_path,
        '[[substitute_group]]\nname = "a"\nmembers = ["olive oil"]\n[[substitute_group]]\nname = "b"\nmembers = ["olive oil"]\n',
    )

    with pytest.raises(ValueError, match="two substitute groups"):
        seed.load_vocabulary(conn, path)


def test_the_real_seed_loads_and_keeps_flavour_oils_out(conn):
    seed.load_vocabulary(conn)

    found = groups(conn)
    assert found["olive oil"] == found["vegetable oil"] == "cooking oil"
    assert "sesame oil" not in found and "truffle oil" not in found
