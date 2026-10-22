"""Fold one ingredient into another that means the same thing, and find entries that read the same to a person."""

import sqlite3
from dataclasses import dataclass


class MergeError(Exception):
    pass


@dataclass(frozen=True)
class MergeReport:
    lines: int = 0
    aliases: int = 0
    children: int = 0
    proposals: int = 0
    produced_by: int = 0
    availability_kept: bool = False


def _ingredient(conn: sqlite3.Connection, name: str) -> sqlite3.Row:
    row = conn.execute(
        "SELECT id, name_en, merged_into_id FROM vocab_entries WHERE kind = 'ingredient' AND name_en = ?", (name,)
    ).fetchone()
    if row is None:
        raise MergeError(f"no ingredient named {name!r}")
    if row["merged_into_id"] is not None:
        raise MergeError(f"{name!r} was already merged into another entry")
    return row


def merge_ingredients(conn: sqlite3.Connection, source_name: str, target_name: str, apply: bool) -> MergeReport:
    """Make `source` an alias of `target`: its recipe lines, aliases, children and open proposals move over, and what
    the person said about buying it carries over when the target has no answer. Nothing runs without `apply`."""
    source, target = _ingredient(conn, source_name), _ingredient(conn, target_name)
    if source["id"] == target["id"]:
        raise MergeError("an entry cannot be merged into itself")
    if target["id"] in _descendants(conn, source["id"]):
        raise MergeError(f"{target_name!r} is a child of {source_name!r}; merge the other way")

    count = lambda sql: conn.execute(sql, (source["id"],)).fetchone()[0]
    report = MergeReport(
        lines=count("SELECT COUNT(*) FROM recipe_lines WHERE ingredient_id = ?"),
        aliases=count("SELECT COUNT(*) FROM aliases WHERE entry_id = ?"),
        children=count("SELECT COUNT(*) FROM vocab_entries WHERE parent_id = ?"),
        proposals=count("SELECT COUNT(*) FROM proposals WHERE target_entry_id = ?"),
        produced_by=count("SELECT COUNT(*) FROM recipes WHERE produces_id = ?"),
    )
    if not apply:
        return report

    answers = lambda entry_id: conn.execute("SELECT availability FROM ingredient_info WHERE entry_id = ?", (entry_id,)).fetchone()
    theirs, mine = answers(source["id"]), answers(target["id"])
    carry = theirs is not None and theirs["availability"] != "unknown" and (mine is None or mine["availability"] == "unknown")
    with conn:
        if carry:
            conn.execute("INSERT INTO ingredient_info (entry_id) VALUES (?) ON CONFLICT (entry_id) DO NOTHING", (target["id"],))
            conn.execute(
                "UPDATE ingredient_info SET availability = ?, availability_changed_at = (SELECT availability_changed_at "
                "FROM ingredient_info WHERE entry_id = ?) WHERE entry_id = ?",
                (theirs["availability"], source["id"], target["id"]),
            )
        conn.execute("UPDATE recipe_lines SET ingredient_id = ? WHERE ingredient_id = ?", (target["id"], source["id"]))
        conn.execute("UPDATE recipes SET produces_id = ? WHERE produces_id = ?", (target["id"], source["id"]))
        conn.execute("UPDATE aliases SET entry_id = ? WHERE entry_id = ?", (target["id"], source["id"]))
        conn.execute("UPDATE vocab_entries SET parent_id = ? WHERE parent_id = ?", (target["id"], source["id"]))
        conn.execute("UPDATE proposals SET target_entry_id = ? WHERE target_entry_id = ?", (target["id"], source["id"]))
        conn.execute("UPDATE vocab_entries SET merged_into_id = ?, substitute_group = NULL WHERE id = ?", (target["id"], source["id"]))
    return MergeReport(**{**report.__dict__, "availability_kept": carry})


def _descendants(conn: sqlite3.Connection, entry_id: int) -> set[int]:
    found: set[int] = set()
    pending = [entry_id]
    while pending:
        for row in conn.execute("SELECT id FROM vocab_entries WHERE parent_id = ?", (pending.pop(),)):
            if row["id"] not in found:
                found.add(row["id"])
                pending.append(row["id"])
    return found


def label_clashes(conn: sqlite3.Connection, locale: str = "es-MX") -> list[tuple[str, list[str]]]:
    """Labels two or more live ingredients share: the app asks about each of them in the same words."""
    rows = conn.execute(
        """
        SELECT l.name, e.name_en FROM vocab_labels AS l JOIN vocab_entries AS e ON e.id = l.entry_id
        WHERE l.locale = ? AND e.kind = 'ingredient' AND e.merged_into_id IS NULL ORDER BY l.name, e.name_en
        """,
        (locale,),
    )
    by_label: dict[str, list[str]] = {}
    for row in rows:
        by_label.setdefault(row["name"], []).append(row["name_en"])
    return [(label, names) for label, names in by_label.items() if len(names) > 1]
