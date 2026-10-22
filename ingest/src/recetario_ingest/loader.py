"""Write a book's extracted recipes into the database, merging by provenance.

What load writes is derived from artifacts and the vocabulary, so a re-run rebuilds it: recipe fields, raw lines,
recipe lines, equipment, and book/computed attributes and translations. What a person decided is never overwritten:
a manual attribute or translation stays, and a recipe marked ok stays ok. Terms still waiting in review load as
unresolved lines (ingredient_id NULL); running load again after review fills them in.
"""

import re
import sqlite3
from dataclasses import dataclass

from recetario_ingest import lines
from recetario_ingest.localize import strip_hint_echo
from recetario_ingest.terms import normalize_term
from recetario_ingest.validate import parse_duration_minutes
from recetario_ingest.vocabulary import Vocabulary

LOCALE = "es-MX"
_NUMBER = re.compile(r"\d+(?:\.\d+)?")


@dataclass
class LoadReport:
    recipes: int = 0
    lines: int = 0
    unresolved_lines: int = 0
    equipment: int = 0
    attributes: int = 0
    skipped_values: int = 0
    translations: int = 0


def load_recipes(
    conn: sqlite3.Connection,
    source_id: int,
    extractions: list[dict],
    splits: dict[str, list[dict]] | None = None,
    localized: dict[str, dict] | None = None,
    courses: dict[str, str] | None = None,
) -> LoadReport:
    """`extractions` are extract artifacts, `splits` maps a raw line to its parts (from the lines stage),
    `localized` maps a recipe key to its translation artifact and `courses` a recipe key to its course."""
    report = LoadReport()
    vocabulary = {kind: Vocabulary(conn, kind) for kind in ("ingredient", "equipment", "attribute_key")}
    with conn:
        for item in extractions:
            if not item.get("extracted"):
                continue
            recipe_id = _upsert_recipe(conn, source_id, item, (courses or {}).get(item["recipe_key"]))
            report.recipes += 1
            raw_ids = _raw_lines(conn, recipe_id, item["extracted"])
            _recipe_lines(conn, raw_ids["ingredients"], item["extracted"]["ingredient_lines"], splits or {}, vocabulary["ingredient"], report)
            _equipment(conn, recipe_id, item["extracted"], vocabulary["equipment"], report)
            _attributes(conn, recipe_id, item["extracted"], vocabulary["attribute_key"], report)
            if localized and item["recipe_key"] in localized:
                _translations(conn, recipe_id, raw_ids, localized[item["recipe_key"]], item["extracted"]["ingredient_lines"], report)
    return report


def _upsert_recipe(conn: sqlite3.Connection, source_id: int, item: dict, course: str | None) -> int:
    data = item["extracted"]
    status = "ok" if item["status"] == "ok" else "needs_review"
    (recipe_id,) = conn.execute(
        """
        INSERT INTO recipes (source_id, locator, title_original, description_original, status, course)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT (source_id, locator) DO UPDATE SET
            title_original = excluded.title_original,
            description_original = excluded.description_original,
            course = COALESCE(excluded.course, recipes.course),
            status = CASE WHEN recipes.status = 'ok' THEN 'ok' ELSE excluded.status END
        RETURNING id
        """,
        (source_id, item["locator"], data["title"], data.get("description") or None, status, course),
    ).fetchone()
    return recipe_id


def _raw_lines(conn: sqlite3.Connection, recipe_id: int, data: dict) -> dict[str, list[int]]:
    """Upsert the recipe's raw lines by position and drop the ones past the new end; returns ids by section."""
    ids = {}
    for section, texts in (("ingredients", data["ingredient_lines"]), ("steps", data["steps"])):
        ids[section] = []
        for position, text in enumerate(texts):
            (raw_id,) = conn.execute(
                """
                INSERT INTO raw_lines (recipe_id, section, position, text_original) VALUES (?, ?, ?, ?)
                ON CONFLICT (recipe_id, section, position) DO UPDATE SET text_original = excluded.text_original
                RETURNING id
                """,
                (recipe_id, section, position, text),
            ).fetchone()
            ids[section].append(raw_id)
        gone = [row["id"] for row in conn.execute("SELECT id FROM raw_lines WHERE recipe_id = ? AND section = ? AND position >= ?", (recipe_id, section, len(texts)))]
        for raw_id in gone:
            conn.execute("DELETE FROM localized_texts WHERE owner_kind = 'raw_line' AND owner_id = ?", (raw_id,))
            conn.execute("DELETE FROM raw_lines WHERE id = ?", (raw_id,))
    return ids


def _recipe_lines(conn, raw_ids: list[int], texts: list[str], splits: dict, vocabulary: Vocabulary, report: LoadReport) -> None:
    group = 0
    for raw_id, text in zip(raw_ids, texts):
        conn.execute("DELETE FROM recipe_lines WHERE raw_line_id = ?", (raw_id,))
        parsed = lines.parse_line(text)
        parts = splits.get(text) if parsed.needs_model else [{"name": parsed.name, "relation": "first"}]
        parts = parts or [{"name": "", "relation": "first"}]  # the rules gave up and no split exists: one unresolved line
        alternatives = any(part["relation"] == "or" for part in parts)
        if alternatives:
            group += 1
        for part in parts:
            entry = vocabulary.exact(normalize_term("ingredient", part["name"])) if part["name"] else None
            conn.execute(
                """
                INSERT INTO recipe_lines (raw_line_id, ingredient_id, qty, unit, preparation_original, variant, is_optional, alt_group)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (raw_id, entry.id if entry else None, parsed.qty, parsed.unit, parsed.preparation, parsed.variant, int(parsed.optional), group if alternatives else None),
            )
            report.lines += 1
            report.unresolved_lines += entry is None


def _equipment(conn, recipe_id: int, data: dict, vocabulary: Vocabulary, report: LoadReport) -> None:
    conn.execute("DELETE FROM recipe_equipment WHERE recipe_id = ?", (recipe_id,))
    found: dict[int, str] = {}
    for mention in data["equipment_mentions"]:
        entry = vocabulary.exact(normalize_term("equipment", mention["text"]))
        if entry is None:
            continue
        found[entry.id] = "listed" if mention.get("listed") or found.get(entry.id) == "listed" else "inferred"
    for equipment_id, provenance in found.items():
        conn.execute("INSERT INTO recipe_equipment (recipe_id, equipment_id, provenance) VALUES (?, ?, ?)", (recipe_id, equipment_id, provenance))
    report.equipment += len(found)


def _attributes(conn, recipe_id: int, data: dict, vocabulary: Vocabulary, report: LoadReport) -> None:
    seen: set[int] = set()
    for group, per in (("meta", None), ("extras", "serving")):
        for pair in data[group]:
            entry = vocabulary.exact(normalize_term("attribute_key", pair["key_text"]))
            if entry is None or entry.id in seen:
                continue
            info = conn.execute("SELECT value_type, unit, column_name FROM key_info WHERE entry_id = ?", (entry.id,)).fetchone()
            value = _value(info["value_type"], pair["value_text"], info["unit"]) if info else None
            if value is None:
                report.skipped_values += 1
                continue
            seen.add(entry.id)
            manual = conn.execute("SELECT 1 FROM attributes WHERE recipe_id = ? AND key_id = ? AND provenance = 'manual'", (recipe_id, entry.id)).fetchone()
            if manual:
                continue
            if info["column_name"]:
                conn.execute(f"UPDATE recipes SET {info['column_name']} = ? WHERE id = ?", (value, recipe_id))  # column names come from key_info, not from the book
            else:
                conn.execute(
                    """
                    INSERT INTO attributes (recipe_id, key_id, value, unit, per, provenance)
                    VALUES (?, ?, ?, ?, ?, 'book')
                    ON CONFLICT (recipe_id, key_id) DO UPDATE SET
                        value = excluded.value, unit = excluded.unit, per = excluded.per, updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
                    WHERE attributes.provenance != 'manual'
                    """,
                    (recipe_id, entry.id, f"{value:g}" if isinstance(value, float) else str(value), info["unit"], per),
                )
                report.attributes += 1


_UNIT_WORDS = {"g", "mg", "mcg", "kg", "kcal", "ml", "l"}


def _value(value_type: str, text: str, unit: str | None = None) -> float | int | str | None:
    if value_type == "duration":
        return parse_duration_minutes(text)
    if value_type == "number":
        match = _NUMBER.search(text.replace(",", ""))
        if not match:
            return None
        written = text[match.end():].strip().lower().rstrip(".")
        if unit and written in _UNIT_WORDS and written != unit:  # "Sodium 361 kcal" is a typo in one book; don't store it as mg
            return None
        return float(match.group())
    return text.strip() or None


def _translations(conn, recipe_id: int, raw_ids: dict[str, list[int]], artifact: dict, originals: list[str], report: LoadReport) -> None:
    version = artifact.get("prompt_version")

    def put(owner_kind: str, owner_id: int, text: str | None) -> None:
        if not text:
            return
        conn.execute(
            """
            INSERT INTO localized_texts (owner_kind, owner_id, locale, text, provenance, prompt_version)
            VALUES (?, ?, ?, ?, 'computed', ?)
            ON CONFLICT (owner_kind, owner_id, locale) DO UPDATE SET
                text = excluded.text, provenance = 'computed', prompt_version = excluded.prompt_version
            WHERE localized_texts.provenance != 'manual'
            """,
            (owner_kind, owner_id, LOCALE, text, version),
        )
        report.translations += 1

    put("recipe_title", recipe_id, artifact.get("title"))
    put("recipe_description", recipe_id, artifact.get("description"))
    for raw_id, text, original in zip(raw_ids["ingredients"], artifact.get("ingredient_lines", []), originals):
        put("raw_line", raw_id, strip_hint_echo(text, original))  # older artifacts still carry the echoed hint
    for raw_id, text in zip(raw_ids["steps"], artifact.get("steps", [])):
        put("raw_line", raw_id, text)
