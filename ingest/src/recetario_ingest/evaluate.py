"""Score extraction against a golden set: hand-labeled recipes, one JSON file each.

A golden file is {"id", "source", "text", "expected", "reviewed"}, where `expected` has the shape of the extraction
schema. Scores are per field and averaged over the set; they are tracked per prompt version, not pass/fail.
"""

import json
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from recetario_ingest.segment import reconcile_title
from recetario_ingest.extraction import extract_recipe
from recetario_ingest.llm.adapter import LlmAdapter
from recetario_ingest.validate import squash


@dataclass(frozen=True)
class GoldenCase:
    id: str
    text: str
    expected: dict
    reviewed: bool
    printed_title: str = ""  # the title the book prints (a PDF's contents page); the pipeline trusts it over the model's


@dataclass(frozen=True)
class CaseScore:
    id: str
    title: float
    ingredients_p: float
    ingredients_r: float
    steps: float
    meta: float
    equipment_p: float
    equipment_r: float
    extras_r: float
    issues: int
    errors: int
    attempts: int
    cost_usd: float
    tokens_in: int
    tokens_out: int
    seconds: float


def load_golden(*directories: Path) -> list[GoldenCase]:
    cases = []
    for directory in directories:
        for path in sorted(directory.glob("*.json")) if directory.is_dir() else []:
            data = json.loads(path.read_text(encoding="utf-8"))
            cases.append(GoldenCase(data["id"], data["text"], data["expected"], data.get("reviewed", False), data.get("printed_title", "")))
    return cases


def precision_recall(expected: list, got: list) -> tuple[float, float]:
    """Multiset precision and recall. Nothing expected means nothing missed (recall 1.0); an empty answer to a
    non-empty expectation scores precision 0."""
    expected_counts, got_counts = Counter(expected), Counter(got)
    hits = sum((expected_counts & got_counts).values())
    precision = hits / len(got) if got else float(not expected)
    recall = hits / len(expected) if expected else 1.0
    return precision, recall


def _pairs(entries: list[dict]) -> list[tuple[str, str]]:
    return [(squash(e.get("key_text", "")), squash(e.get("value_text", ""))) for e in entries]


def _equipment(entries: list[dict]) -> list[tuple[str, object]]:
    return [(squash(e.get("text", "")), e.get("step_index")) for e in entries]


def equipment_precision_recall(expected: list[dict], got: list[dict]) -> tuple[float, float]:
    """Mentions match when they sit in the same step and one name contains the other ("steamer" ~ "steamer basket")."""
    wanted, offered = _equipment(expected), _equipment(got)
    unmatched, hits = list(offered), 0
    for name, step in wanted:
        match = next((i for i, (other, other_step) in enumerate(unmatched) if other_step == step and (name in other or other in name)), None)
        if match is not None:
            hits += 1
            del unmatched[match]
    precision = hits / len(offered) if offered else float(not wanted)
    recall = hits / len(wanted) if wanted else 1.0
    return precision, recall


def score_fields(expected: dict, got: dict | None) -> dict[str, float]:
    got = got or {}
    ingredients_p, ingredients_r = precision_recall(
        [squash(x) for x in expected["ingredient_lines"]], [squash(x) for x in got.get("ingredient_lines", [])]
    )
    equipment_p, equipment_r = equipment_precision_recall(expected["equipment_mentions"], got.get("equipment_mentions", []))
    expected_steps, got_steps = [squash(x) for x in expected["steps"]], [squash(x) for x in got.get("steps", [])]
    step_hits = sum(a == b for a, b in zip(expected_steps, got_steps))
    expected_meta, got_meta = _pairs(expected["meta"]), _pairs(got.get("meta", []))
    extras_recall = precision_recall([key for key, _ in _pairs(expected["extras"])], [key for key, _ in _pairs(got.get("extras", []))])[1]
    return {
        "title": float(squash(expected["title"]) == squash(got.get("title", ""))),
        "ingredients_p": ingredients_p,
        "ingredients_r": ingredients_r,
        "steps": step_hits / max(len(expected_steps), len(got_steps), 1),  # right count and order, step by step
        "meta": sum(a == b for a, b in zip(expected_meta, got_meta)) / max(len(expected_meta), len(got_meta), 1),
        "equipment_p": equipment_p,
        "equipment_r": equipment_r,
        "extras_r": extras_recall,
    }


def run_eval(llm: LlmAdapter, cases: list[GoldenCase], version: str) -> list[CaseScore]:
    scores = []
    for case in cases:
        started = time.monotonic()
        result = extract_recipe(llm, case.text, version)
        if result.extracted and case.printed_title:  # same step the extract stage applies after the model answers
            result.extracted["title"] = reconcile_title(result.extracted.get("title", ""), case.printed_title, authoritative=True)
        seconds = time.monotonic() - started
        scores.append(
            CaseScore(
                id=case.id,
                **score_fields(case.expected, result.extracted),
                issues=len(result.issues),
                errors=sum(issue.severity == "error" for issue in result.issues),
                attempts=result.attempts,
                cost_usd=result.usage.cost_usd,
                tokens_in=result.usage.tokens_in,
                tokens_out=result.usage.tokens_out,
                seconds=seconds,
            )
        )
    return scores


def _mean(values) -> float:
    values = list(values)
    return sum(values) / len(values) if values else 0.0


def summarize(scores: list[CaseScore]) -> dict[str, float]:
    return {
        "recipes": len(scores),
        "title": _mean(s.title for s in scores),
        "ingredients_p": _mean(s.ingredients_p for s in scores),
        "ingredients_r": _mean(s.ingredients_r for s in scores),
        "steps": _mean(s.steps for s in scores),
        "meta": _mean(s.meta for s in scores),
        "equipment_p": _mean(s.equipment_p for s in scores),
        "equipment_r": _mean(s.equipment_r for s in scores),
        "extras_r": _mean(s.extras_r for s in scores),
        "issues_per_recipe": _mean(s.issues for s in scores),
        "errors_per_recipe": _mean(s.errors for s in scores),
        "retry_rate": _mean(s.attempts > 1 for s in scores),
        "cost_per_recipe": _mean(s.cost_usd for s in scores),
        "tokens_in": _mean(s.tokens_in for s in scores),
        "tokens_out": _mean(s.tokens_out for s in scores),
        "seconds": _mean(s.seconds for s in scores),
    }


def results_row(summary: dict[str, float], date: str, prompt: str, model: str) -> str:
    """One line for the results table in EVALS.md."""
    return (
        f"| {date} | {prompt} | {model} | {summary['title']:.2f} | "
        f"{summary['ingredients_p']:.2f} / {summary['ingredients_r']:.2f} | {summary['steps']:.2f} | {summary['meta']:.2f} | "
        f"{summary['equipment_p']:.2f} / {summary['equipment_r']:.2f} | {summary['issues_per_recipe']:.2f} | "
        f"${summary['cost_per_recipe']:.4f} |"
    )


def format_eval(scores: list[CaseScore], summary: dict[str, float], row: str, unreviewed: int) -> str:
    lines = [f"{'recipe':<34}title  ingr P/R    steps  meta  equip P/R   issues  tries  cost"]
    for s in scores:
        lines.append(
            f"{s.id:<34}{s.title:<7.0f}{s.ingredients_p:.2f}/{s.ingredients_r:.2f}  {s.steps:<6.2f} {s.meta:<5.2f} "
            f"{s.equipment_p:.2f}/{s.equipment_r:.2f}   {s.issues:<7}{s.attempts:<7}${s.cost_usd:.4f}"
        )
    lines.append(
        f"\nmean over {summary['recipes']} recipes: retry rate {summary['retry_rate']:.0%}, errors/recipe {summary['errors_per_recipe']:.2f}, "
        f"tokens {summary['tokens_in']:.0f} in / {summary['tokens_out']:.0f} out, {summary['seconds']:.1f}s per recipe"
    )
    if unreviewed:
        lines.append(f"WARNING: {unreviewed} of {summary['recipes']} golden recipes are still unreviewed drafts, so these scores aren't trustworthy yet")
    lines.append(f"\nresults row:\n{row}")
    return "\n".join(lines)
