"""Resolve a term to a vocabulary entry: exact match, then the model proposes among fuzzy candidates, then the
confident ones are auto-accepted and everything else waits in `proposals` for review.

The same algorithm serves ingredients, equipment and attribute keys; only the guidance in the prompt differs.
"""

import json
import sqlite3
from dataclasses import dataclass, field

from recetario_ingest.llm.adapter import LlmAdapter, Usage
from recetario_ingest.llm.prompts import Prompt
from recetario_ingest.terms import TermExample
from recetario_ingest.vocabulary import Candidate, Entry, Vocabulary, add_alias, create_proposal, find_proposal

PROMPT_ID = "resolve"
T_AUTO = 0.9  # a mapping at or above this confidence, onto a candidate we showed, is accepted without review

RESOLVE_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["map_to", "new_entry"]},
        "target": {"type": "string"},
        "new_entry": {
            "type": "object",
            "properties": {
                "name_en": {"type": "string"},
                "name_es_mx": {"type": "string"},
                "parent_en": {"type": "string"},
                "value_type": {"type": "string"},
                "unit": {"type": "string"},
            },
            "required": ["name_en", "name_es_mx", "parent_en", "value_type", "unit"],
            "additionalProperties": False,
        },
        "confidence": {"type": "number"},
        "reason": {"type": "string"},
    },
    "required": ["action", "target", "new_entry", "confidence", "reason"],
    "additionalProperties": False,
}

RESOLVE_V1 = Prompt(
    id=PROMPT_ID,
    version="v1",
    system="""You map one term from an English cookbook to an entry in the canonical vocabulary of a recipe app whose display language is Mexican Spanish (es-MX). A person reviews whatever you are unsure about.

You get the kind of term (ingredient, equipment or attribute_key), the term, short examples of how the book used it, and the closest existing entries (candidates).

Decide:
- map_to: the term means the same thing as one candidate. Differences of wording, size, plural or state ("big skillet", "preheated oven") don't make a new thing. Set target to that candidate's name_en exactly.
- new_entry: no candidate means the same thing. Never map to a candidate that is merely similar: look-alikes are separate entries (lemon and lime), and a child entry satisfies its parent but never the reverse.

Naming rules for a new entry:
- name_es_mx is the word used at a Mexican market or supermarket: lowercase, singular, no brand, no preparation, no quantity ("jitomate", not "tomates picados"). Mexican usage beats Spain's ("jitomate", "betabel", "calabacita", "durazno", "papa", not "tomate", "remolacha", "calabacín", "melocotón", "patata").
- name_en is the generic English name, singular, no brand. Brands become their generic (Instant Pot -> electric pressure cooker).
- parent_en names a candidate it is a kind of, only when a recipe asking for the parent would be satisfied by this entry; empty otherwise.
- Equipment is an appliance, cookware, bakeware, bowl or tool. Typical es-MX names: horno, licuadora, olla, sartén, charola para hornear, freidora de aire.
- An attribute_key's name_en is English snake_case with the unit in the name (prep_min, sodium_mg, calories_kcal); name_es_mx is a short label ("Sodio (mg)"); value_type is number, text, bool or duration; unit is min, g, mg, kcal and so on, or empty. Synonyms ("ready in", "total time") map to one key.
- For map_to leave the new_entry fields empty. For new_entry leave target empty.

confidence is how sure you are, from 0 to 1. Use below 0.9 whenever the term is ambiguous, regional or you are guessing. reason is one short sentence.""",
    template="{text}",
)


@dataclass
class Resolution:
    kind: str
    term: str
    method: str  # exact | llm | existing_proposal
    outcome: str  # resolved | proposed
    entry: str | None = None  # name_en of the entry it resolved to
    proposal_id: int | None = None
    confidence: float | None = None
    usage: Usage = field(default_factory=Usage)

    def as_json(self) -> dict:
        return {
            "kind": self.kind, "term": self.term, "method": self.method, "outcome": self.outcome, "entry": self.entry,
            "proposal_id": self.proposal_id, "confidence": self.confidence,
        }


def build_input(kind: str, term: str, examples: list[TermExample], candidates: list[Candidate], vocabulary: Vocabulary) -> str:
    lines = [f"kind: {kind}", f"term: {term}", "examples:"]
    lines += [f'- recipe "{e.recipe}": {e.text}' for e in examples] or ["- (none)"]
    lines.append("candidates:")
    for candidate in candidates:
        entry = candidate.entry
        aliases = ", ".join(a for a in vocabulary.aliases(entry.id)[:8])
        lines.append(f"- {entry.name_en} (es-MX: {entry.label_es_mx or '?'}; aliases: {aliases or 'none'})")
    if not candidates:
        lines.append("- (none)")
    return "\n".join(lines)


class Resolver:
    def __init__(self, conn: sqlite3.Connection, llm: LlmAdapter, version: str, source_id: int | None = None, t_auto: float = T_AUTO) -> None:
        self._conn = conn
        self._llm = llm
        self._version = version
        self._source_id = source_id
        self._t_auto = t_auto

    def resolve(self, kind: str, term: str, examples: list[TermExample]) -> Resolution:
        """`term` is already normalized (see terms.normalize_term)."""
        vocabulary = Vocabulary(self._conn, kind)
        known = vocabulary.exact(term)
        if known is not None:
            return Resolution(kind, term, "exact", "resolved", entry=known.name_en, confidence=1.0)

        existing = find_proposal(self._conn, kind, term)
        if existing is not None:  # decided or waiting: never ask (or pay) twice
            return Resolution(kind, term, "existing_proposal", "proposed", proposal_id=existing.id, confidence=existing.confidence)

        candidates = vocabulary.candidates(term)
        result = self._llm.structured(PROMPT_ID, self._version, build_input(kind, term, examples, candidates, vocabulary), RESOLVE_SCHEMA)
        return self._decide(kind, term, examples, candidates, vocabulary, result.data, result.usage)

    def _decide(self, kind, term, examples, candidates, vocabulary, answer, usage) -> Resolution:
        confidence = float(answer["confidence"])
        evidence = [{"source": "llm", "ref": self._llm.model, "snippet": answer["reason"]}]
        context = {"examples": [{"recipe": e.recipe, "text": e.text} for e in examples], "candidates": [c.entry.name_en for c in candidates]}
        shown = {c.entry.name_en for c in candidates}

        if answer["action"] == "map_to":
            target = vocabulary.by_name(answer["target"])
            if target is not None and answer["target"] in shown and confidence >= self._t_auto:
                with self._conn:
                    add_alias(self._conn, kind, "en", term, target.id, source="auto", confirmed=False)
                return Resolution(kind, term, "llm", "resolved", entry=target.name_en, confidence=confidence, usage=usage)
            with self._conn:  # unsure, or the model named something we never showed it
                proposal_id = create_proposal(self._conn, kind, term, context, "map_to", target.id if target else None, None, confidence, evidence, self._source_id)
            return Resolution(kind, term, "llm", "proposed", entry=target.name_en if target else None, proposal_id=proposal_id, confidence=confidence, usage=usage)

        kept = ("name_en", "name_es_mx", "parent_en") + (("value_type", "unit") if kind == "attribute_key" else ())
        new_entry = {key: value for key, value in answer["new_entry"].items() if value and key in kept}
        clash = vocabulary.exact(new_entry.get("name_en", ""))
        if clash is not None:  # "new" entry that already exists: send it to review as a mapping instead
            with self._conn:
                proposal_id = create_proposal(self._conn, kind, term, context, "map_to", clash.id, None, min(confidence, self._t_auto - 0.01), evidence, self._source_id)
            return Resolution(kind, term, "llm", "proposed", entry=clash.name_en, proposal_id=proposal_id, confidence=confidence, usage=usage)
        with self._conn:  # the model never creates entries on its own
            proposal_id = create_proposal(self._conn, kind, term, context, "new_entry", None, new_entry, confidence, evidence, self._source_id)
        return Resolution(kind, term, "llm", "proposed", proposal_id=proposal_id, confidence=confidence, usage=usage)
