import sqlite3

import pytest

from recetario_ingest import seed
from recetario_ingest.text import normalize


def label_for(conn, kind, lang, text):
    row = conn.execute(
        """
        SELECT l.name FROM aliases AS a
        JOIN vocab_labels AS l ON l.entry_id = a.entry_id AND l.locale = 'es-MX'
        WHERE a.kind = ? AND a.lang = ? AND a.text_normalized = ?
        """,
        (kind, lang, normalize(text)),
    ).fetchone()
    return row and row["name"]


def test_normalize():
    assert normalize("  Jalapeño   Pepper ") == "jalapeno pepper"
    assert normalize("Chícharos") == "chicharos"


def test_seed_counts(conn):
    counts = dict(conn.execute("SELECT kind, count(*) FROM vocab_entries GROUP BY kind").fetchall())
    assert counts["ingredient"] >= 50
    assert counts["equipment"] >= 15
    assert counts["attribute_key"] >= 10


def test_every_entry_has_an_es_mx_label(conn):
    missing = conn.execute(
        """
        SELECT e.name_en FROM vocab_entries AS e
        LEFT JOIN vocab_labels AS l ON l.entry_id = e.id AND l.locale = 'es-MX'
        WHERE l.name IS NULL
        """
    ).fetchall()
    assert missing == []


def test_reloading_the_seed_changes_nothing(conn):
    def snapshot():
        return [
            conn.execute(f"SELECT * FROM {table} ORDER BY 1, 2").fetchall()
            for table in ("vocab_entries", "vocab_labels", "aliases", "ingredient_info", "equipment_info")
        ]

    before = [[tuple(row) for row in rows] for rows in snapshot()]
    seed.load_vocabulary(conn)
    after = [[tuple(row) for row in rows] for rows in snapshot()]
    assert before == after


def test_reloading_the_seed_keeps_personal_choices(conn):
    tomato = seed.entry_id(conn, "ingredient", "tomato")
    conn.execute("UPDATE ingredient_info SET availability = 'obtainable' WHERE entry_id = ?", (tomato,))
    seed.load_vocabulary(conn)
    row = conn.execute("SELECT availability FROM ingredient_info WHERE entry_id = ?", (tomato,)).fetchone()
    assert row["availability"] == "obtainable"


@pytest.mark.parametrize(
    ("lang", "text", "expected"),
    [
        ("en", "tomato", "jitomate"),
        ("en", "tomatillo", "tomate verde"),
        ("es-MX", "tomatillo", "tomate verde"),
        ("en", "pecans", "nuez"),
        ("en", "walnut", "nuez de Castilla"),
        ("en", "zucchini", "calabacita"),
        ("en", "green beans", "ejote"),
        ("en", "peas", "chícharo"),
        ("en", "beets", "betabel"),
        ("en", "sweet potato", "camote"),
        ("en", "corn on the cob", "elote"),
        ("en", "apricot", "chabacano"),
        ("en", "peach", "durazno"),
        ("en", "potatoes", "papa"),
        ("en", "bell pepper", "pimiento morrón"),
        ("en", "scallions", "cebolla cambray"),
        ("en", "cornstarch", "fécula de maíz"),
        ("es-MX", "Maizena", "fécula de maíz"),
        ("en", "sour cream", "crema ácida"),
        ("en", "heavy cream", "crema para batir"),
        ("en", "brown sugar", "azúcar mascabado"),
        ("en", "lime", "limón"),
        ("en", "lemon", "limón amarillo"),
        ("en", "coriander leaves", "cilantro"),
        ("en", "ground coriander", "semilla de cilantro"),
    ],
)
def test_trap_list_resolves_to_mexican_names(conn, lang, text, expected):
    assert label_for(conn, "ingredient", lang, text) == expected


def test_equipment_aliases(conn):
    assert label_for(conn, "equipment", "en", "Instant Pot") == "olla de presión eléctrica"
    assert label_for(conn, "attribute_key", "en", "cooking time") == "Tiempo de cocción"


def test_parents(conn):
    parent = conn.execute(
        """
        SELECT p.name_en FROM vocab_entries AS c JOIN vocab_entries AS p ON p.id = c.parent_id
        WHERE c.kind = 'ingredient' AND c.name_en = 'cheddar cheese'
        """
    ).fetchone()
    assert parent["name_en"] == "cheese"


def test_parent_must_be_same_kind(conn):
    oven = seed.entry_id(conn, "equipment", "oven")
    with pytest.raises(sqlite3.IntegrityError, match="same kind"):
        conn.execute(
            "INSERT INTO vocab_entries (kind, name_en, parent_id) VALUES ('ingredient', 'x', ?)", (oven,)
        )


def test_alias_kind_must_match_entry(conn):
    oven = seed.entry_id(conn, "equipment", "oven")
    with pytest.raises(sqlite3.IntegrityError, match="alias kind"):
        conn.execute(
            """
            INSERT INTO aliases (kind, lang, text_normalized, entry_id, source)
            VALUES ('ingredient', 'en', 'stove thing', ?, 'review')
            """,
            (oven,),
        )


def test_conflicting_seed_alias_is_rejected(conn, tmp_path):
    bad = tmp_path / "bad.toml"
    bad.write_text('[[ingredient]]\nen = "tomato paste"\nes-MX = "puré de tomate"\naliases.en = ["tomatoes"]\n')
    with pytest.raises(ValueError, match="already points"):
        seed.load_vocabulary(conn, bad)
