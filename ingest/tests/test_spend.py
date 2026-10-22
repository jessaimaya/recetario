import pytest

from recetario_ingest import db, spend


def _conn(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    db.migrate(conn)
    conn.execute("INSERT INTO sources (id, raw_title, file_hash, format) VALUES (1, 'Soup Book', 'a', 'epub'), (2, 'Cake Book', 'b', 'epub')")
    rows = [
        (1, "extract", "claude-haiku-5-5", 100, 50, 0.10, "ok"),
        (1, "extract", "claude-haiku-5-5", 100, 50, 0.10, "ok"),  # a second run of the same book adds up
        (1, "resolve", "claude-haiku-5-5", 10, 5, 0.01, "ok"),
        (1, "extract", "claude-haiku-5-5", 0, 0, 0.0, "cached"),
        (1, "segment", "", 0, 0, 0.0, "ok"),  # stages that call no model are not spend
        (2, "extract", "claude-code:haiku", 40, 20, 0.05, "ok"),
    ]
    for source, stage, model, tin, tout, cost, status in rows:
        conn.execute(
            "INSERT INTO stage_runs (source_id, stage, input_hash, model, tokens_in, tokens_out, cost_usd, status) VALUES (?, ?, 'h', ?, ?, ?, ?, ?)",
            (source, stage, model, tin, tout, cost, status),
        )
    return conn


def test_a_books_cost_adds_up_over_runs_and_splits_by_stage_and_model(tmp_path):
    books = spend.book_spend(_conn(tmp_path))

    assert [book.title for book in books] == ["Soup Book", "Cake Book"]
    soup = books[0]
    assert soup.total.cost_usd == pytest.approx(0.21) and soup.total.calls == 3
    assert (soup.total.tokens_in, soup.total.tokens_out) == (210, 105)
    assert soup.by_stage["extract"].cost_usd == pytest.approx(0.20) and set(soup.by_model) == {"claude-haiku-5-5"}


def test_the_title_filter_and_the_report(tmp_path):
    books = spend.book_spend(_conn(tmp_path), only="cake")
    report = spend.format_spend(books, by_stage=True)

    assert [book.title for book in books] == ["Cake Book"]
    assert "$  0.0500  Cake Book" in report and "extract" in report and "list price" in report
    assert spend.format_spend([]) == "nothing spent yet: no book has called a model"
