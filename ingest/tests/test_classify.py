import pytest

from recetario_ingest import db, seed, sources
from recetario_ingest.classify import classify_recipe, recipe_text
from recetario_ingest.epub import EpubMetadata
from recetario_ingest.llm.adapter import FakeLlm
from recetario_ingest.llm.claude import LlmError
from recetario_ingest.loader import load_recipes
from recetario_ingest.registry import default_prompts


def llm(answer):
    return FakeLlm(default_prompts(), {"classify": answer})


DATA = {"title": "Lemon Vinaigrette", "description": "Bright.", "ingredient_lines": ["1/4 cup lemon juice", "1/2 cup olive oil"]}


def test_the_model_sees_the_title_description_and_ingredients():
    text = recipe_text(DATA)

    assert text == "title: Lemon Vinaigrette\ndescription: Bright.\ningredients:\n- 1/4 cup lemon juice\n- 1/2 cup olive oil"


def test_a_valid_course_is_returned():
    assert classify_recipe(llm({"course": "aderezo"}), "v1", "x")[0] == "aderezo"


def test_an_unknown_course_is_not_kept_and_says_why():
    course, _, problem = classify_recipe(llm({"course": "brunch"}), "v1", "x")

    assert course is None and "not a course" in problem


def test_a_model_error_gives_no_course_instead_of_failing():
    def refuse(text):
        raise LlmError("the model declined")

    course, usage, problem = classify_recipe(llm(refuse), "v1", "x")

    assert (course, usage, problem) == (None, None, "the model declined")


@pytest.fixture
def conn():
    connection = db.connect(":memory:")
    db.migrate(connection)
    seed.load_vocabulary(connection)
    yield connection
    connection.close()


def artifact(key, title):
    return {
        "recipe_key": key, "locator": f"{key}.html#a", "status": "ok",
        "extracted": {"title": title, "description": "", "meta": [], "ingredient_lines": ["1 cup olive oil"], "steps": ["Mix."], "equipment_mentions": [], "extras": []},
    }


def course_of(conn, title):
    return conn.execute("SELECT course FROM recipes WHERE title_original = ?", (title,)).fetchone()[0]


def test_the_course_is_stored_with_the_recipe_and_a_later_load_without_one_keeps_it(conn):
    source = sources.register(conn, "f" * 64, "epub", EpubMetadata("Book", None, None, None, "en"))

    load_recipes(conn, source.id, [artifact("a", "Vinaigrette"), artifact("b", "Mystery")], courses={"a": "aderezo"})
    assert (course_of(conn, "Vinaigrette"), course_of(conn, "Mystery")) == ("aderezo", None)

    load_recipes(conn, source.id, [artifact("a", "Vinaigrette")])
    assert course_of(conn, "Vinaigrette") == "aderezo"


def test_the_database_refuses_a_course_that_is_not_on_the_list(conn):
    with pytest.raises(Exception, match="CHECK"):
        conn.execute("INSERT INTO recipes (locator, title_original, course) VALUES ('x', 'x', 'brunch')")


def test_classifying_finished_books_stores_courses_and_skips_what_is_already_done(tmp_path):
    from recetario_ingest import ingest, seed as seed_module
    from recetario_ingest.artifacts import ArtifactStore
    from recetario_ingest.config import Config

    config = Config(db_path=tmp_path / "r.db", artifacts_dir=tmp_path / "artifacts", model="fake")
    setup = db.connect(config.db_path)
    db.migrate(setup)
    seed_module.load_vocabulary(setup)
    source = sources.register(setup, "e" * 64, "epub", EpubMetadata("Book", None, None, None, "en"))
    sources.set_status(setup, source, "done")
    store = ArtifactStore(config.artifacts_dir / source.slug)
    for key, title in (("a", "Vinaigrette"), ("b", "Brownies")):
        store.put("extract", key, {}, artifact(key, title))
    load_recipes(setup, source.id, store.read_all("extract"))
    setup.close()
    answers = iter(["aderezo", "postre"])
    fake = FakeLlm(default_prompts(), {"classify": lambda text: {"course": next(answers)}})

    first = ingest.classify_books(config, llm=fake)
    again = ingest.classify_books(config, llm=fake)

    conn = db.connect(config.db_path)
    assert dict(conn.execute("SELECT title_original, course FROM recipes").fetchall()) == {"Vinaigrette": "aderezo", "Brownies": "postre"}
    assert first[0][1].stages["classify"].ok == 2 and again[0][1].stages["classify"].cached == 2
    assert len(fake.calls) == 2
