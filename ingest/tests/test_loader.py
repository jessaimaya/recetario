import pytest

from recetario_ingest import db, seed, sources
from recetario_ingest.epub import EpubMetadata
from recetario_ingest.loader import load_recipes


@pytest.fixture
def conn():
    connection = db.connect(":memory:")
    db.migrate(connection)
    seed.load_vocabulary(connection)
    yield connection
    connection.close()


@pytest.fixture
def source(conn):
    return sources.register(conn, "f" * 64, "epub", EpubMetadata("Book", None, None, None, "en"))


def artifact(key="r1", title="Roasted Potatoes", status="ok", ingredients=None, steps=None, equipment=None, meta=None, extras=None):
    return {
        "recipe_key": key, "locator": f"{key}.html#a", "status": status,
        "extracted": {
            "title": title, "description": "Crispy.",
            "meta": meta if meta is not None else [{"key_text": "Serving", "value_text": "4"}, {"key_text": "Prep", "value_text": "10m"}, {"key_text": "Ready in", "value_text": "1h5m"}],
            "ingredient_lines": ingredients if ingredients is not None else ["2 lbs. potatoes, cubed", "1/4 cup olive oil", "salt to taste"],
            "steps": steps if steps is not None else ["Heat the oven.", "Roast the potatoes."],
            "equipment_mentions": equipment if equipment is not None else [{"text": "oven", "step_index": 0, "listed": False}],
            "extras": extras if extras is not None else [{"key_text": "Calories", "value_text": "84 calories"}, {"key_text": "Total Fat", "value_text": "7.1 g"}],
        },
    }


def rows(conn, sql, *args):
    return [tuple(row) for row in conn.execute(sql, args)]


def test_writes_recipe_lines_equipment_and_attributes(conn, source):
    report = load_recipes(conn, source.id, [artifact()])

    assert report.recipes == 1 and report.lines == 3 and report.unresolved_lines == 0
    assert rows(conn, "SELECT title_original, servings, prep_min, total_min, status FROM recipes") == [("Roasted Potatoes", 4.0, 10, 65, "ok")]
    assert rows(conn, "SELECT section, position, text_original FROM raw_lines ORDER BY section, position")[0] == ("ingredients", 0, "2 lbs. potatoes, cubed")
    assert rows(conn, "SELECT e.name_en, rl.qty, rl.unit, rl.preparation_original FROM recipe_lines rl JOIN vocab_entries e ON e.id = rl.ingredient_id ORDER BY rl.id") == [
        ("potato", 2.0, "lb", "cubed"), ("olive oil", 0.25, "cup", None), ("salt", None, None, None),
    ]
    assert rows(conn, "SELECT e.name_en, re.provenance FROM recipe_equipment re JOIN vocab_entries e ON e.id = re.equipment_id") == [("oven", "inferred")]
    assert rows(conn, "SELECT e.name_en, a.value, a.unit, a.per, a.provenance FROM attributes a JOIN vocab_entries e ON e.id = a.key_id ORDER BY e.name_en") == [
        ("calories_kcal", "84.0", "kcal", "serving", "book"), ("fat_g", "7.1", "g", "serving", "book"),
    ]


def test_loading_twice_changes_nothing(conn, source):
    load_recipes(conn, source.id, [artifact()])
    before = [rows(conn, f"SELECT COUNT(*) FROM {table}") for table in ("recipes", "raw_lines", "recipe_lines", "recipe_equipment", "attributes")]
    load_recipes(conn, source.id, [artifact()])
    assert [rows(conn, f"SELECT COUNT(*) FROM {table}") for table in ("recipes", "raw_lines", "recipe_lines", "recipe_equipment", "attributes")] == before


def test_a_term_still_in_review_loads_unresolved_and_resolves_on_the_next_load(conn, source):
    data = artifact(ingredients=["2 cups kohlrabi, peeled"])
    assert load_recipes(conn, source.id, [data]).unresolved_lines == 1
    assert rows(conn, "SELECT ingredient_id FROM recipe_lines") == [(None,)]

    seed_id = conn.execute("INSERT INTO vocab_entries (kind, name_en) VALUES ('ingredient', 'kohlrabi') RETURNING id").fetchone()[0]
    conn.execute("INSERT INTO ingredient_info (entry_id) VALUES (?)", (seed_id,))
    conn.commit()

    assert load_recipes(conn, source.id, [data]).unresolved_lines == 0
    assert rows(conn, "SELECT ingredient_id FROM recipe_lines") == [(seed_id,)]


def test_alternatives_share_a_group_and_combinations_do_not(conn, source):
    splits = {
        "1/4 cup butter or olive oil": [{"name": "butter", "relation": "first"}, {"name": "olive oil", "relation": "or"}],
        "salt and pepper": [{"name": "salt", "relation": "first"}, {"name": "black pepper", "relation": "and"}],
    }
    load_recipes(conn, source.id, [artifact(ingredients=["1/4 cup butter or olive oil", "salt and pepper"])], splits)
    assert rows(conn, "SELECT e.name_en, rl.alt_group FROM recipe_lines rl JOIN vocab_entries e ON e.id = rl.ingredient_id ORDER BY rl.id") == [
        ("butter", 1), ("olive oil", 1), ("salt", None), ("black pepper", None),
    ]


def test_a_shorter_recipe_drops_the_lines_past_its_end(conn, source):
    load_recipes(conn, source.id, [artifact(steps=["a", "b", "c"])])
    load_recipes(conn, source.id, [artifact(steps=["a"])])
    assert rows(conn, "SELECT COUNT(*) FROM raw_lines WHERE section = 'steps'") == [(1,)]


def test_recipes_marked_ok_by_hand_stay_ok(conn, source):
    load_recipes(conn, source.id, [artifact(status="needs_review")])
    conn.execute("UPDATE recipes SET status = 'ok'")
    conn.commit()
    load_recipes(conn, source.id, [artifact(status="needs_review")])
    assert rows(conn, "SELECT status FROM recipes") == [("ok",)]


def test_manual_attributes_and_columns_win(conn, source):
    load_recipes(conn, source.id, [artifact()])
    fat = conn.execute("SELECT id FROM vocab_entries WHERE name_en = 'fat_g'").fetchone()[0]
    prep = conn.execute("SELECT id FROM vocab_entries WHERE name_en = 'prep_min'").fetchone()[0]
    conn.execute("UPDATE attributes SET value = '9', provenance = 'manual' WHERE key_id = ?", (fat,))
    conn.execute("INSERT INTO attributes (recipe_id, key_id, value, provenance) VALUES (1, ?, '15', 'manual')", (prep,))
    conn.execute("UPDATE recipes SET prep_min = 15")
    conn.commit()

    load_recipes(conn, source.id, [artifact()])

    assert rows(conn, "SELECT value FROM attributes WHERE key_id = ?", fat) == [("9",)]
    assert rows(conn, "SELECT prep_min FROM recipes") == [(15,)]


def translation(version, title="Papas asadas", step="Calienta el horno."):
    return {"prompt_version": version, "title": title, "description": "Crujientes.", "ingredient_lines": ["2 libras de papa"] * 3, "steps": [step, "Asa las papas."]}


def test_a_manual_edit_survives_re_ingesting_with_a_new_prompt_version(conn, source):
    load_recipes(conn, source.id, [artifact()], localized={"r1": translation("v1")})
    conn.execute("UPDATE localized_texts SET text = 'Papas doradas al horno', provenance = 'manual' WHERE owner_kind = 'recipe_title'")
    conn.commit()

    report = load_recipes(conn, source.id, [artifact()], localized={"r1": translation("v2", title="Papas rostizadas", step="Precalienta el horno.")})

    assert rows(conn, "SELECT text, provenance, prompt_version FROM localized_texts WHERE owner_kind = 'recipe_title'") == [("Papas doradas al horno", "manual", "v1")]
    steps = rows(conn, "SELECT l.text, l.provenance, l.prompt_version FROM localized_texts l JOIN raw_lines r ON r.id = l.owner_id WHERE l.owner_kind = 'raw_line' AND r.section = 'steps' AND r.position = 0")
    assert steps == [("Precalienta el horno.", "computed", "v2")]
    assert report.translations >= 5


def test_a_value_with_the_wrong_unit_is_not_stored(conn, source):
    extras = [{"key_text": "Sodium", "value_text": "361 kcal"}, {"key_text": "Total Fat", "value_text": "7.1 g"}]
    report = load_recipes(conn, source.id, [artifact(extras=extras)])
    assert report.skipped_values == 1 and report.attributes == 1
    assert rows(conn, "SELECT e.name_en FROM attributes a JOIN vocab_entries e ON e.id = a.key_id") == [("fat_g",)]
