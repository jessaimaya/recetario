import pytest

from recetario_ingest import db, seed
from recetario_ingest.merge import MergeError, label_clashes, merge_ingredients


@pytest.fixture
def conn():
    connection = db.connect(":memory:")
    db.migrate(connection)
    seed.load_vocabulary(connection)
    for name, label in (("black bean", "frijol negro"), ("black beans", "frijol negro"), ("chili bean", "frijol chile")):
        (entry,) = connection.execute("INSERT INTO vocab_entries (kind, name_en) VALUES ('ingredient', ?) RETURNING id", (name,)).fetchone()
        connection.execute("INSERT INTO vocab_labels (entry_id, locale, name) VALUES (?, 'es-MX', ?)", (entry, label))
        connection.execute("INSERT INTO ingredient_info (entry_id) VALUES (?)", (entry,))
        connection.execute("INSERT INTO aliases (kind, lang, text_normalized, entry_id, source) VALUES ('ingredient', 'en', ?, ?, 'review')", (name, entry))
    connection.commit()
    yield connection
    connection.close()


def entry(conn, name):
    return conn.execute("SELECT * FROM vocab_entries WHERE kind = 'ingredient' AND name_en = ?", (name,)).fetchone()


def line(conn, name):
    recipe = conn.execute("INSERT INTO recipes (locator, title_original) VALUES ('x', 'Soup') RETURNING id").fetchone()[0]
    raw = conn.execute("INSERT INTO raw_lines (recipe_id, section, position, text_original) VALUES (?, 'ingredients', 0, 'beans') RETURNING id", (recipe,)).fetchone()[0]
    conn.execute("INSERT INTO recipe_lines (raw_line_id, ingredient_id) VALUES (?, ?)", (raw, entry(conn, name)["id"]))


def test_a_dry_run_counts_and_changes_nothing(conn):
    line(conn, "black beans")

    report = merge_ingredients(conn, "black beans", "black bean", apply=False)

    assert (report.lines, report.aliases) == (1, 1)
    assert entry(conn, "black beans")["merged_into_id"] is None


def test_merging_moves_lines_and_aliases_to_the_target(conn):
    line(conn, "black beans")

    merge_ingredients(conn, "black beans", "black bean", apply=True)

    target = entry(conn, "black bean")["id"]
    assert entry(conn, "black beans")["merged_into_id"] == target
    assert conn.execute("SELECT ingredient_id FROM recipe_lines").fetchone()[0] == target
    assert conn.execute("SELECT entry_id FROM aliases WHERE text_normalized = 'black beans'").fetchone()[0] == target


def test_the_buying_answer_carries_over_only_when_the_target_has_none(conn):
    conn.execute("UPDATE ingredient_info SET availability = 'obtainable' WHERE entry_id = ?", (entry(conn, "black beans")["id"],))

    assert merge_ingredients(conn, "black beans", "black bean", apply=True).availability_kept
    assert conn.execute("SELECT availability FROM ingredient_info WHERE entry_id = ?", (entry(conn, "black bean")["id"],)).fetchone()[0] == "obtainable"


def test_an_existing_answer_on_the_target_wins(conn):
    conn.execute("UPDATE ingredient_info SET availability = 'unobtainable' WHERE entry_id = ?", (entry(conn, "black bean")["id"],))
    conn.execute("UPDATE ingredient_info SET availability = 'obtainable' WHERE entry_id = ?", (entry(conn, "black beans")["id"],))

    assert not merge_ingredients(conn, "black beans", "black bean", apply=True).availability_kept
    assert conn.execute("SELECT availability FROM ingredient_info WHERE entry_id = ?", (entry(conn, "black bean")["id"],)).fetchone()[0] == "unobtainable"


@pytest.mark.parametrize(("source", "target", "message"), [("nope", "black bean", "no ingredient"), ("black bean", "black bean", "itself")])
def test_bad_requests_are_refused(conn, source, target, message):
    with pytest.raises(MergeError, match=message):
        merge_ingredients(conn, source, target, apply=True)


def test_merging_twice_is_refused_and_a_merged_entry_is_no_longer_a_clash(conn):
    assert label_clashes(conn) == [("frijol negro", ["black bean", "black beans"])]

    merge_ingredients(conn, "black beans", "black bean", apply=True)

    assert label_clashes(conn) == []
    with pytest.raises(MergeError, match="already merged"):
        merge_ingredients(conn, "black beans", "black bean", apply=True)
