"""What each book cost to ingest, added up from `stage_runs` over every run and resume of that book."""

import sqlite3
from dataclasses import dataclass, field


@dataclass
class Spend:
    cost_usd: float = 0.0
    tokens_in: int = 0
    tokens_out: int = 0
    calls: int = 0  # units that called the model (cached ones, and terms the vocabulary already knew, cost nothing)


@dataclass
class BookSpend:
    title: str
    total: Spend = field(default_factory=Spend)
    by_stage: dict[str, Spend] = field(default_factory=dict)
    by_model: dict[str, Spend] = field(default_factory=dict)  # "claude-code:haiku" is list price, covered by the plan


def book_spend(conn: sqlite3.Connection, only: str | None = None) -> list[BookSpend]:
    """One entry per book that has spent anything, most expensive first; `only` filters on the title."""
    rows = conn.execute(
        """
        SELECT s.id, s.raw_title, r.stage, COALESCE(NULLIF(r.model, ''), '(no model)') AS model,
               COUNT(*) AS calls, SUM(r.cost_usd) AS cost, SUM(r.tokens_in) AS tin, SUM(r.tokens_out) AS tout
        FROM stage_runs r JOIN sources s ON s.id = r.source_id
        WHERE r.status = 'ok' AND r.model != '' AND r.tokens_in > 0 AND (? IS NULL OR s.raw_title LIKE '%' || ? || '%')
        GROUP BY s.id, r.stage, model
        """,
        (only, only),
    ).fetchall()
    books: dict[int, BookSpend] = {}
    for row in rows:
        book = books.setdefault(row["id"], BookSpend(row["raw_title"]))
        for spend in (book.total, book.by_stage.setdefault(row["stage"], Spend()), book.by_model.setdefault(row["model"], Spend())):
            spend.cost_usd += row["cost"] or 0.0
            spend.tokens_in += row["tin"] or 0
            spend.tokens_out += row["tout"] or 0
            spend.calls += row["calls"]
    return sorted(books.values(), key=lambda book: -book.total.cost_usd)


def format_spend(books: list[BookSpend], by_stage: bool = False) -> str:
    if not books:
        return "nothing spent yet: no book has called a model"
    lines = []
    for book in books:
        models = ", ".join(f"{model} ${spend.cost_usd:.4f}" for model, spend in sorted(book.by_model.items()))
        lines.append(
            f"${book.total.cost_usd:8.4f}  {book.title[:70]}\n"
            f"           {book.total.calls} calls · tokens {book.total.tokens_in:,} in / {book.total.tokens_out:,} out · {models}"
        )
        if by_stage:
            lines += [f"             {stage:<9} ${spend.cost_usd:.4f} · {spend.calls} calls" for stage, spend in book.by_stage.items()]
    total = sum(book.total.cost_usd for book in books)
    lines.append(f"\n${total:.4f} across {len(books)} book(s); claude-code figures are list price, covered by your plan")
    return "\n".join(lines)


def source_cost(conn: sqlite3.Connection, source_id: int) -> float:
    """Everything the model has cost for this book, over all its runs and resumes."""
    return conn.execute("SELECT COALESCE(SUM(cost_usd), 0) FROM stage_runs WHERE source_id = ? AND status = 'ok'", (source_id,)).fetchone()[0]
