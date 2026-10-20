from recetario_ingest import db
from recetario_ingest.evaluate_resolve import ResolutionCase, load_cases, run_cases, summarize, threshold_sweep
from recetario_ingest.llm.adapter import FakeLlm
from recetario_ingest.registry import default_prompts

from fakes import map_answer, new_answer


class Script:
    """Answers by term, so the order of the cases doesn't matter."""

    model = "scripted"

    def __init__(self, answers):
        self.answers = answers

    def structured(self, prompt_id, version, input, schema, tools=()):
        from recetario_ingest.llm.adapter import LlmResult, Usage

        term = next(line for line in input.splitlines() if line.startswith("term: ")).removeprefix("term: ")
        return LlmResult(self.answers[term], Usage(cost_usd=0.001))


def case(term, map_to=None, labels=None, holdout=(), trap=False):
    return ResolutionCase(term, "ingredient", term, map_to, labels, list(holdout), trap)


def test_the_shipped_cases_load_and_hold_out_real_entries():
    cases = load_cases(db.REPO_ROOT / "evals" / "resolution")
    assert len(cases) >= 50 and sum(c.trap for c in cases) == 22
    assert all(c.expect_map or c.expect_labels is not None for c in cases)


def test_verdicts_cover_every_outcome():
    llm = Script(
        {
            "plum tomato": map_answer("tomato", 0.95),
            "key lime": map_answer("lemon", 0.95),  # wrong, and accepted without review
            "red onion": map_answer("onion", 0.8),
            "granulated sugar": new_answer("sugar", "azúcar", 0.8),
            "cornmeal": new_answer("cornmeal", "harina de maíz", 0.8),
            "chive": map_answer("green onion", 0.8),
        }
    )
    cases = [
        case("plum tomato", map_to="tomato"), case("key lime", map_to="lime"), case("red onion", map_to="onion"),
        case("granulated sugar", labels=["azúcar"]), case("cornmeal", labels=["harina de maíz de otra forma"]), case("chives", labels=[]),
    ]
    scores = {s.id: s for s in run_cases(llm, cases, "v1")}

    assert scores["plum tomato"].verdict == "correct_auto"
    assert scores["key lime"].verdict == "confident_wrong"
    assert scores["red onion"].verdict == "correct_review"
    assert scores["granulated sugar"].verdict == "correct_review"
    assert scores["cornmeal"].verdict == "wrong_label"
    assert scores["chives"].verdict == "wrong_review"


def test_a_trap_case_removes_its_entry_so_it_must_be_proposed():
    llm = Script({"tomato": new_answer("tomato", "jitomate", 0.9)})
    (score,) = run_cases(llm, [case("tomato", labels=["jitomate"], holdout=["tomato"], trap=True)], "v1")
    assert score.verdict == "correct_review" and score.label == "jitomate" and score.answer_correct


def test_summary_and_threshold_replay():
    llm = Script({"plum tomato": map_answer("tomato", 0.95), "key lime": map_answer("lemon", 0.8)})
    scores = run_cases(llm, [case("plum tomato", map_to="tomato"), case("key lime", map_to="lime")], "v1")
    summary = summarize(scores)
    assert summary["cases"] == 2 and summary["auto"] == 0.5 and summary["confident_wrong"] == 0
    sweep = {row["t"]: row for row in threshold_sweep(scores)}
    assert sweep[0.9]["accepted"] == 1 and sweep[0.85]["wrong"] == 0
    assert sweep[0.8]["accepted"] == 2 and sweep[0.8]["wrong"] == 1  # lowering T would have let the wrong one in
