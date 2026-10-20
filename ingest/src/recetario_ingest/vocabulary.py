"""Read and write the vocabulary: entries, labels, aliases and proposals.

`Vocabulary` is a snapshot of one kind, so build a new one after changing the tables.
"""

import json
import sqlite3
from dataclasses import dataclass
from difflib import SequenceMatcher

from recetario_ingest.terms import singular
from recetario_ingest.text import normalize


def _singular_phrase(text: str) -> str:
    words = text.split()
    return " ".join(words[:-1] + [singular(words[-1])]) if words else text


@dataclass(frozen=True)
class Entry:
    id: int
    kind: str
    name_en: str
    label_es_mx: str | None


@dataclass(frozen=True)
class Candidate:
    entry: Entry
    score: float
    matched: str  # the alias or name that matched best


class Vocabulary:
    def __init__(self, conn: sqlite3.Connection, kind: str) -> None:
        self.kind = kind
        self._entries: dict[int, Entry] = {}
        merged: dict[int, int] = {}
        for row in conn.execute("SELECT id, name_en, merged_into_id FROM vocab_entries WHERE kind = ?", (kind,)):
            self._entries[row["id"]] = Entry(row["id"], kind, row["name_en"], None)
            if row["merged_into_id"] is not None:
                merged[row["id"]] = row["merged_into_id"]
        for row in conn.execute(
            "SELECT l.entry_id, l.name FROM vocab_labels l JOIN vocab_entries e ON e.id = l.entry_id "
            "WHERE e.kind = ? AND l.locale = 'es-MX'",
            (kind,),
        ):
            entry = self._entries[row["entry_id"]]
            self._entries[entry.id] = Entry(entry.id, kind, entry.name_en, row["name"])

        self._keys: dict[str, int] = {}
        self._aliases: dict[int, list[str]] = {}
        for entry in self._entries.values():
            self._keys.setdefault(normalize(entry.name_en), entry.id)
            if entry.label_es_mx:
                self._keys.setdefault(normalize(entry.label_es_mx), entry.id)
        for row in conn.execute("SELECT text_normalized, entry_id FROM aliases WHERE kind = ?", (kind,)):
            self._keys[row["text_normalized"]] = row["entry_id"]
            self._aliases.setdefault(row["entry_id"], []).append(row["text_normalized"])

        def final(entry_id: int) -> int:
            for _ in range(10):  # follow merges
                if entry_id not in merged:
                    break
                entry_id = merged[entry_id]
            return entry_id

        for key, entry_id in list(self._keys.items()):  # "green beans" must also answer to the singular form, "non-stick" to "non stick"
            self._keys.setdefault(_singular_phrase(key), entry_id)
            self._keys.setdefault(key.replace("-", " "), entry_id)
        self._keys = {key: final(entry_id) for key, entry_id in self._keys.items()}
        self._live = {entry_id: entry for entry_id, entry in self._entries.items() if entry_id not in merged}

    def __len__(self) -> int:
        return len(self._live)

    def exact(self, term: str) -> Entry | None:
        text = normalize(term)
        entry_id = self._keys.get(text, self._keys.get(_singular_phrase(text), self._keys.get(text.replace("-", " "))))
        return self._live.get(entry_id) if entry_id is not None else None

    def by_name(self, name_en: str) -> Entry | None:
        wanted = normalize(name_en)
        return next((e for e in self._live.values() if normalize(e.name_en) == wanted), None)

    def aliases(self, entry_id: int) -> list[str]:
        return sorted(set(self._aliases.get(entry_id, [])))

    def candidates(self, term: str, k: int = 5, minimum: float = 0.35) -> list[Candidate]:
        """The k entries whose names or aliases look most like `term`."""
        term = normalize(term)
        term_words = set(term.split())
        best: dict[int, Candidate] = {}
        for key, entry_id in self._keys.items():
            entry = self._live.get(entry_id)
            if entry is None:
                continue
            score = SequenceMatcher(None, term, key).ratio()
            key_words = set(key.split())
            if key_words <= term_words and term:  # "pot" inside "heavy pot"
                score = max(score, 0.6 + 0.4 * len(key) / len(term))
            if score >= minimum and (entry_id not in best or score > best[entry_id].score):
                best[entry_id] = Candidate(entry, score, key)
        return sorted(best.values(), key=lambda c: (-c.score, c.entry.name_en))[:k]


def add_alias(conn: sqlite3.Connection, kind: str, lang: str, text: str, entry_id: int, source: str, confirmed: bool) -> bool:
    """Insert an alias unless the text already has one. Returns whether a row was added."""
    cursor = conn.execute(
        "INSERT OR IGNORE INTO aliases (kind, lang, text_normalized, entry_id, confirmed, source) VALUES (?, ?, ?, ?, ?, ?)",
        (kind, lang, normalize(text), entry_id, int(confirmed), source),
    )
    return cursor.rowcount == 1


def create_entry(
    conn: sqlite3.Connection,
    kind: str,
    name_en: str,
    labels: dict[str, str],
    parent_id: int | None = None,
    value_type: str | None = None,
    unit: str | None = None,
) -> int:
    """Insert an entry with its labels, its kind-specific info row and an alias for each name."""
    (entry_id,) = conn.execute(
        "INSERT INTO vocab_entries (kind, name_en, parent_id) VALUES (?, ?, ?) RETURNING id", (kind, name_en, parent_id)
    ).fetchone()
    for locale, name in labels.items():
        conn.execute("INSERT INTO vocab_labels (entry_id, locale, name) VALUES (?, ?, ?)", (entry_id, locale, name))
    add_alias(conn, kind, "en", name_en, entry_id, source="review", confirmed=True)
    for locale, name in labels.items():
        add_alias(conn, kind, locale.split("-")[0], name, entry_id, source="review", confirmed=True)
    if kind == "ingredient":
        conn.execute("INSERT INTO ingredient_info (entry_id) VALUES (?)", (entry_id,))
    elif kind == "equipment":
        conn.execute("INSERT INTO equipment_info (entry_id) VALUES (?)", (entry_id,))
    else:
        conn.execute("INSERT INTO key_info (entry_id, value_type, unit) VALUES (?, ?, ?)", (entry_id, value_type or "text", unit))
    return entry_id


@dataclass(frozen=True)
class Proposal:
    id: int
    kind: str
    term: str
    context: dict
    action: str
    target_entry_id: int | None
    new_entry: dict | None
    confidence: float | None
    evidence: list[dict]
    status: str


def _proposal(row: sqlite3.Row) -> Proposal:
    return Proposal(
        row["id"], row["kind"], row["term"], json.loads(row["context"] or "{}"), row["action"], row["target_entry_id"],
        json.loads(row["new_entry"]) if row["new_entry"] else None, row["confidence"], json.loads(row["evidence"] or "[]"), row["status"],
    )


def find_proposal(conn: sqlite3.Connection, kind: str, term: str) -> Proposal | None:
    row = conn.execute("SELECT * FROM proposals WHERE kind = ? AND term = ? ORDER BY id DESC LIMIT 1", (kind, term)).fetchone()
    return _proposal(row) if row else None


def create_proposal(
    conn: sqlite3.Connection,
    kind: str,
    term: str,
    context: dict,
    action: str,
    target_entry_id: int | None,
    new_entry: dict | None,
    confidence: float | None,
    evidence: list[dict],
    source_id: int | None,
) -> int:
    (proposal_id,) = conn.execute(
        """
        INSERT INTO proposals (kind, term, context, action, target_entry_id, new_entry, confidence, evidence, source_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) RETURNING id
        """,
        (
            kind, term, json.dumps(context, ensure_ascii=False), action, target_entry_id,
            json.dumps(new_entry, ensure_ascii=False) if new_entry else None, confidence,
            json.dumps(evidence, ensure_ascii=False), source_id,
        ),
    ).fetchone()
    return proposal_id


def open_proposals(conn: sqlite3.Connection, kind: str | None = None) -> list[Proposal]:
    rows = conn.execute(
        "SELECT * FROM proposals WHERE status = 'open' AND (? IS NULL OR kind = ?) ORDER BY kind, term", (kind, kind)
    )
    return [_proposal(row) for row in rows]
