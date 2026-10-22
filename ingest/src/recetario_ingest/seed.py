"""Load the starter vocabulary and the hand-written fixture recipes from TOML."""

import re
import sqlite3
import tomllib
from pathlib import Path

from recetario_ingest.db import SCHEMA_DIR
from recetario_ingest.text import normalize

VOCABULARY_PATH = SCHEMA_DIR / "seed" / "vocabulary.toml"
FIXTURE_RECIPES_PATH = SCHEMA_DIR / "fixtures" / "recipes.toml"

KINDS = ("ingredient", "equipment", "attribute_key")
_LOCALE_KEY = re.compile(r"^[a-z]{2}-[A-Z]{2}$")


def entry_id(conn: sqlite3.Connection, kind: str, name_en: str) -> int:
    row = conn.execute(
        "SELECT id FROM vocab_entries WHERE kind = ? AND name_en = ?", (kind, name_en)
    ).fetchone()
    if row is None:
        raise KeyError(f"no {kind} named {name_en!r} in the vocabulary")
    return row["id"]


def load_vocabulary(conn: sqlite3.Connection, path: Path = VOCABULARY_PATH) -> dict[str, int]:
    """Insert or update every seed entry. Safe to run again; personal choices are never touched."""
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    counts = {}
    with conn:
        for kind in KINDS:
            items = data.get(kind, [])
            for item in items:
                _upsert_entry(conn, kind, item)
            # Parents can be declared after their children, so link them in a second pass.
            for item in items:
                parent = item.get("parent")
                parent_id = entry_id(conn, kind, parent) if parent else None
                conn.execute(
                    "UPDATE vocab_entries SET parent_id = ? WHERE id = ?",
                    (parent_id, entry_id(conn, kind, item["en"])),
                )
            counts[kind] = len(items)
        counts["substitute"] = _apply_substitute_groups(conn, data.get("substitute_group", []))
    return counts


def _apply_substitute_groups(conn: sqlite3.Connection, groups: list[dict]) -> int:
    """Make the seed the only source of groups: clear them, then set each member that exists. Returns how many were set."""
    seen: dict[str, str] = {}
    for group in groups:
        for name in group["members"]:
            if name in seen:
                raise ValueError(f"ingredient {name!r} is in two substitute groups: {seen[name]!r} and {group['name']!r}")
            seen[name] = group["name"]
    conn.execute("UPDATE vocab_entries SET substitute_group = NULL WHERE kind = 'ingredient'")
    assigned = 0
    for name, group_name in seen.items():
        assigned += conn.execute(
            "UPDATE vocab_entries SET substitute_group = ? WHERE kind = 'ingredient' AND name_en = ? AND merged_into_id IS NULL",
            (group_name, name),
        ).rowcount
    return assigned


def _upsert_entry(conn: sqlite3.Connection, kind: str, item: dict) -> None:
    (new_id,) = conn.execute(
        """
        INSERT INTO vocab_entries (kind, name_en, description) VALUES (?, ?, ?)
        ON CONFLICT (kind, name_en) DO UPDATE SET description = excluded.description
        RETURNING id
        """,
        (kind, item["en"], item.get("description")),
    ).fetchone()

    labels = {key: value for key, value in item.items() if _LOCALE_KEY.match(key)}
    for locale, name in labels.items():
        conn.execute(
            """
            INSERT INTO vocab_labels (entry_id, locale, name) VALUES (?, ?, ?)
            ON CONFLICT (entry_id, locale) DO UPDATE SET name = excluded.name
            """,
            (new_id, locale, name),
        )

    alias_texts = [("en", item["en"])] + list(labels.items())
    for lang, texts in item.get("aliases", {}).items():
        alias_texts += [(lang, text) for text in texts]
    for lang, text in alias_texts:
        _add_alias(conn, kind, lang, text, new_id)

    if kind == "ingredient":
        conn.execute(
            """
            INSERT INTO ingredient_info (entry_id, is_staple) VALUES (?, ?)
            ON CONFLICT (entry_id) DO UPDATE SET is_staple = excluded.is_staple
            """,
            (new_id, int(item.get("staple", False))),
        )
    elif kind == "equipment":
        conn.execute(
            """
            INSERT INTO equipment_info (entry_id, is_basic) VALUES (?, ?)
            ON CONFLICT (entry_id) DO UPDATE SET is_basic = excluded.is_basic
            """,
            (new_id, int(item.get("basic", False))),
        )
    else:
        conn.execute(
            """
            INSERT INTO key_info (entry_id, value_type, unit, column_name) VALUES (?, ?, ?, ?)
            ON CONFLICT (entry_id) DO UPDATE SET
                value_type = excluded.value_type, unit = excluded.unit, column_name = excluded.column_name
            """,
            (new_id, item["type"], item.get("unit"), item.get("column")),
        )


def _add_alias(conn: sqlite3.Connection, kind: str, lang: str, text: str, target_id: int) -> None:
    normalized = normalize(text)
    existing = conn.execute(
        "SELECT entry_id FROM aliases WHERE kind = ? AND lang = ? AND text_normalized = ?",
        (kind, lang, normalized),
    ).fetchone()
    if existing is None:
        conn.execute(
            """
            INSERT INTO aliases (kind, lang, text_normalized, entry_id, confirmed, source)
            VALUES (?, ?, ?, ?, 1, 'seed')
            """,
            (kind, lang, normalized, target_id),
        )
    elif existing["entry_id"] != target_id:
        raise ValueError(f"{kind} alias {text!r} ({lang}) already points at another entry")


def load_fixture_recipes(conn: sqlite3.Connection, path: Path = FIXTURE_RECIPES_PATH) -> dict[str, int]:
    """Insert the fixture recipes into a database that has the vocabulary. Returns key -> recipe id."""
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    recipes = data["recipe"]
    ids: dict[str, int] = {}
    with conn:
        # All recipes first, so sub-recipe lines can point at any of them.
        for recipe in recipes:
            (ids[recipe["key"]],) = conn.execute(
                """
                INSERT INTO recipes (locator, title_original, servings, prep_min, total_min, produces_id, status)
                VALUES (?, ?, ?, ?, ?, ?, 'ok') RETURNING id
                """,
                (
                    f"fixture:{recipe['key']}",
                    recipe["title"],
                    recipe.get("servings"),
                    recipe.get("prep_min"),
                    recipe.get("total_min"),
                    entry_id(conn, "ingredient", recipe["produces"]) if "produces" in recipe else None,
                ),
            ).fetchone()
        for recipe in recipes:
            _insert_recipe_body(conn, ids[recipe["key"]], recipe, ids)
    return ids


def _insert_recipe_body(conn: sqlite3.Connection, recipe_id: int, recipe: dict, ids: dict[str, int]) -> None:
    for position, item in enumerate(recipe.get("ingredients", [])):
        (raw_line_id,) = conn.execute(
            """
            INSERT INTO raw_lines (recipe_id, section, position, text_original)
            VALUES (?, 'ingredients', ?, ?) RETURNING id
            """,
            (recipe_id, position, item["text"]),
        ).fetchone()
        lines = item.get("lines") or [{key: value for key, value in item.items() if key != "text"}]
        for line in lines:
            conn.execute(
                """
                INSERT INTO recipe_lines (raw_line_id, ingredient_id, sub_recipe_id, qty, unit,
                                          preparation_original, is_optional, alt_group)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    raw_line_id,
                    entry_id(conn, "ingredient", line["entry"]) if "entry" in line else None,
                    ids[line["sub_recipe"]] if "sub_recipe" in line else None,
                    line.get("qty"),
                    line.get("unit"),
                    line.get("prep"),
                    int(line.get("optional", False)),
                    line.get("alt"),
                ),
            )

    for position, text in enumerate(recipe.get("steps", [])):
        conn.execute(
            "INSERT INTO raw_lines (recipe_id, section, position, text_original) VALUES (?, 'steps', ?, ?)",
            (recipe_id, position, text),
        )

    for item in recipe.get("equipment", []):
        conn.execute(
            """
            INSERT INTO recipe_equipment (recipe_id, equipment_id, is_optional, alt_group, provenance)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                recipe_id,
                entry_id(conn, "equipment", item["entry"]),
                int(item.get("optional", False)),
                item.get("alt"),
                item.get("provenance", "inferred"),
            ),
        )

    for item in recipe.get("attributes", []):
        conn.execute(
            """
            INSERT INTO attributes (recipe_id, key_id, value, per, provenance)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                recipe_id,
                entry_id(conn, "attribute_key", item["key"]),
                item["value"],
                item.get("per"),
                item["provenance"],
            ),
        )
