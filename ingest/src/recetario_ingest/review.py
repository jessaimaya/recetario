"""Review: the only place where vocabulary decisions are made.

Proposals come from the resolver; accepting one writes a confirmed alias (and, for a new entry, the entry itself).
Auto-accepted aliases can be spot-checked here too. Everything goes to the database, never to the artifacts.
"""

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass

from recetario_ingest.vocabulary import Entry, Proposal, Vocabulary, add_alias, create_entry, open_proposals
from recetario_ingest.text import normalize


@dataclass
class Outcome:
    message: str


def _decide(conn: sqlite3.Connection, proposal: Proposal, status: str) -> None:
    conn.execute(
        "UPDATE proposals SET status = ?, decided_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now') WHERE id = ?", (status, proposal.id)
    )


def _alias_term(conn: sqlite3.Connection, proposal: Proposal, entry_id: int) -> None:
    # A confirmed alias replaces an auto one for the same text.
    conn.execute(
        "DELETE FROM aliases WHERE kind = ? AND lang = 'en' AND text_normalized = ? AND source = 'auto'", (proposal.kind, proposal.term)
    )
    add_alias(conn, proposal.kind, "en", proposal.term, entry_id, source="review", confirmed=True)


def map_to_entry(conn: sqlite3.Connection, proposal: Proposal, entry: Entry, status: str = "accepted") -> Outcome:
    with conn:
        _alias_term(conn, proposal, entry.id)
        _decide(conn, proposal, status)
    return Outcome(f'"{proposal.term}" is now an alias of {entry.name_en}')


def create_from_proposal(
    conn: sqlite3.Connection, proposal: Proposal, name_en: str | None = None, label: str | None = None,
    parent_en: str | None = None, status: str = "accepted",
) -> Outcome:
    """Create the proposed entry (or reuse one with the same name) and point the term at it. Other open proposals that
    suggest the same new entry join it, so one concept reviewed once doesn't come back under each spelling."""
    spec = proposal.new_entry or {}
    name_en = (name_en or spec.get("name_en") or "").strip()
    label = (label or spec.get("name_es_mx") or "").strip()
    if not name_en:
        raise ValueError("a new entry needs an English name")
    vocabulary = Vocabulary(conn, proposal.kind)
    existing = vocabulary.by_name(name_en)
    if existing is not None:
        return map_to_entry(conn, proposal, existing, status)

    parent_name = parent_en if parent_en is not None else spec.get("parent_en")
    parent = vocabulary.by_name(parent_name) if parent_name else None
    with conn:
        entry_id = create_entry(
            conn, proposal.kind, name_en, {"es-MX": label} if label else {}, parent.id if parent else None,
            spec.get("value_type"), spec.get("unit"),
        )
        _alias_term(conn, proposal, entry_id)
        _decide(conn, proposal, status)
        joined = []
        for other in open_proposals(conn, proposal.kind):
            if other.action == "new_entry" and normalize((other.new_entry or {}).get("name_en", "")) == normalize(name_en):
                _alias_term(conn, other, entry_id)
                _decide(conn, other, "accepted")
                joined.append(other.term)
    message = f'created {proposal.kind} "{name_en}"' + (f' ({label})' if label else "") + f' with the alias "{proposal.term}"'
    if joined:
        message += "; also resolved " + ", ".join(f'"{term}"' for term in joined)
    return Outcome(message)


def accept(conn: sqlite3.Connection, proposal: Proposal) -> Outcome:
    if proposal.action == "map_to" and proposal.target_entry_id is not None:
        entry = next(e for e in _entries(conn, proposal.kind) if e.id == proposal.target_entry_id)
        return map_to_entry(conn, proposal, entry)
    if proposal.action == "new_entry":
        return create_from_proposal(conn, proposal)
    raise ValueError(f'proposal {proposal.id} ("{proposal.term}") has no target; use edit to pick an entry')


def reject(conn: sqlite3.Connection, proposal: Proposal) -> Outcome:
    with conn:
        _decide(conn, proposal, "rejected")
    return Outcome(f'rejected "{proposal.term}"')


def _entries(conn: sqlite3.Connection, kind: str) -> list[Entry]:
    vocabulary = Vocabulary(conn, kind)
    return sorted(vocabulary._live.values(), key=lambda e: e.name_en)


def _label(entry: Entry) -> str:
    return f"{entry.name_en} ({entry.label_es_mx})" if entry.label_es_mx else entry.name_en


def review_proposals(
    conn: sqlite3.Connection, kind: str | None = None, input_fn: Callable[[str], str] = input, output: Callable[[str], None] = print
) -> dict[str, int]:
    """Walk through the open proposals. Returns how many were accepted, edited, rejected and skipped."""
    counts = {"accepted": 0, "edited": 0, "rejected": 0, "skipped": 0}
    pending = open_proposals(conn, kind)
    output(f"{len(pending)} open proposals")
    for position, listed in enumerate(pending, 1):
        current = next((p for p in open_proposals(conn, listed.kind) if p.id == listed.id), None)
        if current is None:  # already resolved together with an earlier one
            continue
        _show(conn, current, position, len(pending), output)
        while True:
            choice = input_fn("[a]ccept  [e]dit  [r]eject  [s]kip  [q]uit > ").strip().lower()[:1]
            if choice == "q":
                return counts
            if choice == "s" or choice == "":
                counts["skipped"] += 1
            elif choice == "r":
                output(reject(conn, current).message)
                counts["rejected"] += 1
            elif choice == "a":
                try:
                    output(accept(conn, current).message)
                except ValueError as error:
                    output(str(error))
                    continue
                counts["accepted"] += 1
            elif choice == "e":
                outcome = _edit(conn, current, input_fn, output)
                if outcome is None:
                    continue
                output(outcome.message)
                counts["edited"] += 1
            else:
                continue
            break
    return counts


def _show(conn, proposal: Proposal, position: int, total: int, output) -> None:
    confidence = f"{proposal.confidence:.2f}" if proposal.confidence is not None else "?"
    output(f'\n[{position}/{total}] {proposal.kind}: "{proposal.term}"  (confidence {confidence})')
    if proposal.action == "new_entry":
        spec = proposal.new_entry or {}
        parent = f", kind of {spec['parent_en']}" if spec.get("parent_en") else ""
        output(f'  suggestion: NEW "{spec.get("name_en")}" / es-MX "{spec.get("name_es_mx")}"{parent}')
    else:
        target = next((e for e in _entries(conn, proposal.kind) if e.id == proposal.target_entry_id), None)
        output(f"  suggestion: map to {_label(target) if target else '(nothing valid)'}")
    for item in proposal.evidence:
        output(f"  why ({item['source']}): {item['snippet']}")
    for example in proposal.context.get("examples", []):
        output(f'  seen in "{example["recipe"]}": {example["text"][:110]}')
    if proposal.context.get("candidates"):
        output("  candidates: " + ", ".join(proposal.context["candidates"]))


def _edit(conn, proposal: Proposal, input_fn, output) -> Outcome | None:
    answer = input_fn("map to an existing entry (its English name), or 'new', or empty to cancel > ").strip()
    if not answer:
        return None
    if answer.lower() == "new":
        spec = proposal.new_entry or {}
        name_en = input_fn(f"English name [{spec.get('name_en', '')}] > ").strip() or None
        label = input_fn(f"es-MX label [{spec.get('name_es_mx', '')}] > ").strip() or None
        parent = input_fn(f"parent entry [{spec.get('parent_en', '')}] (- for none) > ").strip()
        try:
            return create_from_proposal(conn, proposal, name_en, label, None if parent == "-" else (parent or None), status="edited")
        except ValueError as error:
            output(str(error))
            return None
    entry = Vocabulary(conn, proposal.kind).by_name(answer)
    if entry is None:
        output(f'no {proposal.kind} named "{answer}"')
        return None
    return map_to_entry(conn, proposal, entry, status="edited")


def spot_check_auto(
    conn: sqlite3.Connection, kind: str | None = None, input_fn: Callable[[str], str] = input, output: Callable[[str], None] = print
) -> dict[str, int]:
    """Go through the aliases the resolver accepted on its own: keep (confirm) them or point them elsewhere."""
    rows = conn.execute(
        """
        SELECT a.kind, a.lang, a.text_normalized, a.entry_id, e.name_en FROM aliases a
        JOIN vocab_entries e ON e.id = a.entry_id
        WHERE a.source = 'auto' AND a.confirmed = 0 AND (? IS NULL OR a.kind = ?) ORDER BY a.kind, a.text_normalized
        """,
        (kind, kind),
    ).fetchall()
    counts = {"kept": 0, "fixed": 0, "skipped": 0}
    output(f"{len(rows)} auto-accepted aliases")
    for position, row in enumerate(rows, 1):
        output(f'\n[{position}/{len(rows)}] {row["kind"]}: "{row["text_normalized"]}" -> {row["name_en"]}')
        while True:
            choice = input_fn("[k]eep  [f]ix  [s]kip  [q]uit > ").strip().lower()[:1]
            if choice == "q":
                return counts
            if choice in ("s", ""):
                counts["skipped"] += 1
            elif choice == "k":
                with conn:
                    conn.execute(
                        "UPDATE aliases SET confirmed = 1 WHERE kind = ? AND lang = ? AND text_normalized = ?",
                        (row["kind"], row["lang"], row["text_normalized"]),
                    )
                counts["kept"] += 1
            elif choice == "f":
                name = input_fn("it should map to (English name of an existing entry) > ").strip()
                entry = Vocabulary(conn, row["kind"]).by_name(name)
                if entry is None:
                    output(f'no {row["kind"]} named "{name}"')
                    continue
                with conn:
                    conn.execute(
                        "UPDATE aliases SET entry_id = ?, confirmed = 1, source = 'review' WHERE kind = ? AND lang = ? AND text_normalized = ?",
                        (entry.id, row["kind"], row["lang"], row["text_normalized"]),
                    )
                counts["fixed"] += 1
            else:
                continue
            break
    return counts
