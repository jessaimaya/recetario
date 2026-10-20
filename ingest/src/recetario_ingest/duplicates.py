"""Find recipes that look like the same dish: similar ingredient sets and similar titles.

A pair becomes a review item, never a deletion. Ingredient sets use the vocabulary entry when a line is resolved and the
normalized term otherwise, so the check works before a review pass has settled every term.
"""

import itertools
import sqlite3
from dataclasses import dataclass
from difflib import SequenceMatcher

from recetario_ingest import lines
from recetario_ingest.terms import normalize_term
from recetario_ingest.text import normalize
from recetario_ingest.vocabulary import Vocabulary

MIN_INGREDIENTS = 3  # tiny sets match by accident
# either the ingredients are nearly the same and the titles are related, or both agree fairly well
STRONG = (0.8, 0.4)  # (ingredient overlap, title similarity)
FAIR = (0.6, 0.7)


@dataclass(frozen=True)
class Pair:
    recipe_id: int
    other_id: int  # always the higher id
    overlap: float
    titles: float


def ingredient_keys(conn: sqlite3.Connection, recipe_id: int, vocabulary: Vocabulary) -> set[str]:
    keys = set()
    for row in conn.execute("SELECT text_original FROM raw_lines WHERE recipe_id = ? AND section = 'ingredients'", (recipe_id,)):
        parsed = lines.parse_line(row["text_original"])
        if parsed.needs_model or not parsed.name:
            continue
        term = normalize_term("ingredient", parsed.name)
        entry = vocabulary.exact(term)
        keys.add(f"id:{entry.id}" if entry else term)
    return keys


def candidates(conn: sqlite3.Connection) -> list[Pair]:
    """Pairs not yet reviewed, most alike first."""
    vocabulary = Vocabulary(conn, "ingredient")
    recipes = {row["id"]: row["title_original"] for row in conn.execute("SELECT id, title_original FROM recipes WHERE duplicate_of IS NULL")}
    sets = {recipe_id: ingredient_keys(conn, recipe_id, vocabulary) for recipe_id in recipes}
    reviewed = {(row["recipe_id"], row["other_id"]) for row in conn.execute("SELECT recipe_id, other_id FROM duplicate_reviews")}
    found = []
    for a, b in itertools.combinations(sorted(sets), 2):
        if (a, b) in reviewed or min(len(sets[a]), len(sets[b])) < MIN_INGREDIENTS:
            continue
        overlap = len(sets[a] & sets[b]) / len(sets[a] | sets[b])
        if overlap < FAIR[0]:
            continue
        titles = SequenceMatcher(None, normalize(recipes[a]), normalize(recipes[b])).ratio()
        if (overlap >= STRONG[0] and titles >= STRONG[1]) or (overlap >= FAIR[0] and titles >= FAIR[1]):
            found.append(Pair(a, b, overlap, titles))
    return sorted(found, key=lambda pair: (pair.overlap + pair.titles), reverse=True)
