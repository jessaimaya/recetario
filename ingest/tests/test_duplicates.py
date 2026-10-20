import pytest

from recetario_ingest import db, duplicates, review, seed, sources
from recetario_ingest.epub import EpubMetadata
from recetario_ingest.loader import load_recipes

from test_loader import artifact


@pytest.fixture
def conn():
    connection = db.connect(":memory:")
    db.migrate(connection)
    seed.load_vocabulary(connection)
    yield connection
    connection.close()


def load(conn, *recipes):
    source = sources.register(conn, "f" * 64, "epub", EpubMetadata("Book", None, None, None, "en"))
    load_recipes(conn, source.id, list(recipes))


GARLIC = ["1 lb. green beans", "2 cloves garlic", "2 tbsps. butter", "salt to taste"]


def test_similar_titles_and_ingredients_are_a_pair_and_different_dishes_are_not(conn):
    load(
        conn,
        artifact("a", "Buttery Garlic Green Beans", ingredients=GARLIC),
        artifact("b", "Easy Garlic Green Beans", ingredients=GARLIC),
        artifact("c", "Fruit Salad", ingredients=["1 apple", "1 banana", "1 peach", "honey"]),
    )
    (pair,) = duplicates.candidates(conn)
    assert (pair.recipe_id, pair.other_id) == (1, 2) and pair.overlap == 1.0


def test_short_ingredient_lists_never_match(conn):
    load(conn, artifact("a", "Boiled Eggs", ingredients=["2 eggs", "water"]), artifact("b", "Boiled Eggs Again", ingredients=["2 eggs", "water"]))
    assert duplicates.candidates(conn) == []


def test_a_decision_is_remembered_and_duplicate_marks_the_later_recipe(conn):
    load(conn, artifact("a", "Garlic Green Beans", ingredients=GARLIC), artifact("b", "Easy Garlic Green Beans", ingredients=GARLIC))

    counts = review.review_duplicates(conn, input_fn=lambda prompt: "d", output=lambda text: None)

    assert counts["duplicate"] == 1
    assert conn.execute("SELECT id, duplicate_of FROM recipes ORDER BY id").fetchall()[1]["duplicate_of"] == 1
    assert duplicates.candidates(conn) == []  # decided: not asked again (and the duplicate is out of the running)


def test_not_a_duplicate_is_remembered_too(conn):
    load(conn, artifact("a", "Garlic Green Beans", ingredients=GARLIC), artifact("b", "Easy Garlic Green Beans", ingredients=GARLIC))
    review.review_duplicates(conn, input_fn=lambda prompt: "n", output=lambda text: None)
    assert duplicates.candidates(conn) == []
    assert conn.execute("SELECT decision FROM duplicate_reviews").fetchone()["decision"] == "distinct"
