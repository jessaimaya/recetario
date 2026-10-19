"""The recipe_requirements view, checked against every fixture recipe."""

import sqlite3

import pytest

from recetario_ingest import seed

I, E = "ingredient", "equipment"

EXPECTED = {
    "huevos-a-la-mexicana": [(I, {"egg"}), (I, {"tomato"}), (I, {"onion"}), (I, {"serrano pepper"})],
    "guacamole": [(I, {"avocado"}), (I, {"tomato"}), (I, {"onion"}), (I, {"lime"})],
    "calabacitas-al-horno": [
        (I, {"zucchini"}), (I, {"parmesan cheese"}), (I, {"garlic powder"}), (E, {"oven"}),
    ],
    "papas-crujientes": [(I, {"potato"}), (I, {"paprika"}), (E, {"air fryer", "oven"})],
    "quesadillas": [(I, {"corn tortilla"}), (I, {"cheese"})],
    "ejotes-salteados": [(I, {"green beans"}), (I, {"garlic"})],
    "pesto": [
        (I, {"basil"}), (I, {"walnut", "pecan"}), (I, {"parmesan cheese"}), (I, {"garlic"}),
        (E, {"blender", "food processor"}),
    ],
    "pasta-al-pesto": [
        (I, {"pasta"}), (I, {"basil"}), (I, {"walnut", "pecan"}), (I, {"parmesan cheese"}), (I, {"garlic"}),
        (E, {"blender", "food processor"}),
    ],
    "licuado-de-platano": [(I, {"banana"}), (I, {"milk"}), (I, {"oats"}), (E, {"blender"})],
    "pepino-con-chamoy": [(I, {"cucumber"}), (I, {"lime"}), (I, {None})],
}


def requirements(conn, recipe_id):
    groups: dict[tuple[str, str], set] = {}
    rows = conn.execute(
        """
        SELECT r.kind, r.req_key, e.name_en
        FROM recipe_requirements AS r LEFT JOIN vocab_entries AS e ON e.id = r.entry_id
        WHERE r.recipe_id = ?
        """,
        (recipe_id,),
    )
    for row in rows:
        groups.setdefault((row["kind"], row["req_key"]), set()).add(row["name_en"])
    return sorted(((kind, frozenset(names)) for (kind, _), names in groups.items()), key=repr)


def expected_list(pairs):
    return sorted(((kind, frozenset(names)) for kind, names in pairs), key=repr)


def expected(key):
    return expected_list(EXPECTED[key])


def test_every_fixture_has_an_expectation(recipe_ids):
    assert set(recipe_ids) == set(EXPECTED)


@pytest.mark.parametrize("key", sorted(EXPECTED))
def test_recipe_requirements(conn, recipe_ids, key):
    assert requirements(conn, recipe_ids[key]) == expected(key)


def test_marking_an_ingredient_as_staple_drops_it(conn, recipe_ids):
    garlic = seed.entry_id(conn, "ingredient", "garlic")
    conn.execute("UPDATE ingredient_info SET is_staple = 1 WHERE entry_id = ?", (garlic,))
    assert requirements(conn, recipe_ids["ejotes-salteados"]) == expected_list([(I, {"green beans"})])


def test_sub_recipe_line_cannot_be_an_alternative(conn, recipe_ids):
    (raw_line_id,) = conn.execute(
        "SELECT id FROM raw_lines WHERE recipe_id = ? AND section = 'ingredients' LIMIT 1",
        (recipe_ids["pasta-al-pesto"],),
    ).fetchone()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO recipe_lines (raw_line_id, sub_recipe_id, alt_group) VALUES (?, ?, 1)",
            (raw_line_id, recipe_ids["pesto"]),
        )


def test_recipe_line_must_reference_an_ingredient(conn, recipe_ids):
    (raw_line_id,) = conn.execute("SELECT id FROM raw_lines LIMIT 1").fetchone()
    oven = seed.entry_id(conn, "equipment", "oven")
    with pytest.raises(sqlite3.IntegrityError, match="must reference an ingredient"):
        conn.execute("INSERT INTO recipe_lines (raw_line_id, ingredient_id) VALUES (?, ?)", (raw_line_id, oven))


def test_attributes_are_stored_with_provenance(conn, recipe_ids):
    rows = conn.execute(
        """
        SELECT e.name_en, a.value, a.per, a.provenance FROM attributes AS a
        JOIN vocab_entries AS e ON e.id = a.key_id WHERE a.recipe_id = ? ORDER BY e.name_en
        """,
        (recipe_ids["calabacitas-al-horno"],),
    ).fetchall()
    assert [tuple(row) for row in rows] == [
        ("calories_kcal", "120", "serving", "manual"),
        ("protein_g", "6", "serving", "manual"),
    ]
