"""Score the resolver on golden terms.

Every case runs against its own in-memory copy of the seed vocabulary. A trap case first removes the entry it is
about, so the model has to propose it again with the right es-MX word. The costly mistake is a *confident wrong*
answer: a mapping onto the wrong entry that the resolver accepted without review.
"""

import json
from dataclasses import dataclass
from pathlib import Path

from recetario_ingest import db, seed
from recetario_ingest.llm.adapter import LlmAdapter
from recetario_ingest.resolver import T_AUTO, Resolver
from recetario_ingest.terms import normalize_term
from recetario_ingest.text import normalize
from recetario_ingest.vocabulary import find_proposal

THRESHOLDS = (0.7, 0.75, 0.8, 0.85, 0.9, 0.95)


@dataclass(frozen=True)
class ResolutionCase:
    id: str
    kind: str
    term: str
    expect_map: str | None  # name_en the term should map to
    expect_labels: list[str] | None  # es-MX names accepted for a new entry (empty: any); None when mapping is expected
    holdout: list[str]
    trap: bool


@dataclass
class ResolutionScore:
    id: str
    trap: bool
    verdict: str  # correct_auto | correct_review | wrong_review | wrong_label | confident_wrong
    method: str
    action: str  # what the model answered: map_to | new_entry | exact
    entry: str | None
    label: str | None
    confidence: float | None
    answer_correct: bool  # the model's answer alone, ignoring thresholds
    cost_usd: float = 0.0
    web_searches: int = 0


def load_cases(directory: Path) -> list[ResolutionCase]:
    cases = []
    for path in sorted(directory.glob("*.json")):
        for item in json.loads(path.read_text(encoding="utf-8")):
            expect = item["expect"]
            cases.append(
                ResolutionCase(
                    id=item["id"], kind=item["kind"], term=item["term"], expect_map=expect.get("map_to"),
                    expect_labels=None if "map_to" in expect else expect["new_entry"], holdout=item.get("holdout", []),
                    trap=bool(item.get("trap")),
                )
            )
    return cases


def run_cases(llm: LlmAdapter, cases: list[ResolutionCase], version: str, t_auto: float = T_AUTO) -> list[ResolutionScore]:
    return [_run_case(llm, case, version, t_auto) for case in cases]


def _run_case(llm: LlmAdapter, case: ResolutionCase, version: str, t_auto: float) -> ResolutionScore:
    conn = db.connect(":memory:")
    try:
        db.migrate(conn)
        seed.load_vocabulary(conn)
        with conn:
            for name in case.holdout:
                conn.execute("DELETE FROM vocab_entries WHERE kind = ? AND name_en = ?", (case.kind, name))
        term = normalize_term(case.kind, case.term)
        resolution = Resolver(conn, llm, version, t_auto=t_auto).resolve(case.kind, term, [])
        proposal = find_proposal(conn, case.kind, term) if resolution.outcome == "proposed" else None
        label = (proposal.new_entry or {}).get("name_es_mx") if proposal else None
        action = "exact" if resolution.method == "exact" else ("new_entry" if proposal and proposal.action == "new_entry" else "map_to")
        entry = resolution.entry
        correct = _answer_correct(case, action, entry, label)
        return ResolutionScore(
            id=case.id, trap=case.trap, verdict=_verdict(case, resolution.outcome, action, correct), method=resolution.method,
            action=action, entry=entry, label=label, confidence=resolution.confidence, answer_correct=correct,
            cost_usd=resolution.usage.cost_usd, web_searches=resolution.usage.web_searches,
        )
    finally:
        conn.close()


def _answer_correct(case: ResolutionCase, action: str, entry: str | None, label: str | None) -> bool:
    if case.expect_map is not None:
        return action in ("map_to", "exact") and entry == case.expect_map
    if action != "new_entry":
        return False
    return not case.expect_labels or normalize(label or "") in {normalize(expected) for expected in case.expect_labels}


def _verdict(case: ResolutionCase, outcome: str, action: str, correct: bool) -> str:
    if outcome == "resolved":  # accepted without review
        return "correct_auto" if correct else "confident_wrong"
    if correct:
        return "correct_review"
    return "wrong_label" if action == "new_entry" else "wrong_review"


def summarize(scores: list[ResolutionScore]) -> dict:
    total = len(scores) or 1
    traps = [score for score in scores if score.trap]
    return {
        "cases": len(scores),
        "correct": sum(s.verdict.startswith("correct") for s in scores) / total,
        "confident_wrong": sum(s.verdict == "confident_wrong" for s in scores),
        "auto": sum(s.verdict in ("correct_auto", "confident_wrong") for s in scores) / total,
        "to_review": sum(s.method != "exact" and s.verdict not in ("correct_auto", "confident_wrong") for s in scores) / total,
        "traps_correct": sum(s.answer_correct for s in traps),
        "traps": len(traps),
        "web_searches": sum(s.web_searches for s in scores),
        "cost_usd": sum(s.cost_usd for s in scores),
    }


def threshold_sweep(scores: list[ResolutionScore]) -> list[dict]:
    """What would T_AUTO = t have accepted? Only mappings the model made can be accepted, so this is a replay of its answers."""
    rows = []
    for t in THRESHOLDS:
        accepted = [s for s in scores if s.action == "map_to" and s.entry and (s.confidence or 0) >= t]
        rows.append(
            {"t": t, "auto_share": len(accepted) / (len(scores) or 1), "wrong": sum(not s.answer_correct for s in accepted), "accepted": len(accepted)}
        )
    return rows


def format_report(scores: list[ResolutionScore], summary: dict, sweep: list[dict], version: str, model: str) -> str:
    lines = [f"{'case':<34}{'verdict':<17}{'answer':<38}conf"]
    for s in scores:
        answer = s.label or s.entry or "-"
        lines.append(f"{s.id:<34}{s.verdict:<17}{s.action + ': ' + answer:<38}{'' if s.confidence is None else format(s.confidence, '.2f')}")
    lines.append(
        f"\n{summary['cases']} cases: correct {summary['correct']:.0%}, confident wrong {summary['confident_wrong']}, "
        f"auto-accepted {summary['auto']:.0%}, to review {summary['to_review']:.0%}, traps {summary['traps_correct']}/{summary['traps']}, "
        f"web searches {summary['web_searches']}, cost ${summary['cost_usd']:.4f}"
    )
    lines.append("\nthreshold replay (mappings the model made):")
    lines += [f"  T={row['t']:.2f}: auto {row['auto_share']:.0%} ({row['accepted']} accepted, {row['wrong']} wrong)" for row in sweep]
    lines.append(f"\nprompt {version}, model {model}")
    return "\n".join(lines)

