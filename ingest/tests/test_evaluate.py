import json

import pytest
from fakes import perfect_extraction

from recetario_ingest import evaluate
from recetario_ingest.cli import main
from recetario_ingest.llm.adapter import FakeLlm
from recetario_ingest.registry import default_prompts

TEXT = (
    "Soup\n\"Warm.\"\nServing: 2 | Prep: 5m | Ready in: 15m\nIngredients\n• 2 cups stock\n• 1 onion\n"
    "Direction\n• Simmer the stock in a pot.\n• Add the onion.\nNutrition Information\n• Calories: 50 calories;"
)


def expected():
    data = perfect_extraction(TEXT)
    data["equipment_mentions"] = [{"text": "pot", "step_index": 0, "listed": False}]
    return data


def test_a_perfect_answer_scores_one_everywhere():
    scores = evaluate.score_fields(expected(), expected())

    assert set(scores.values()) == {1.0}


def test_each_field_loses_points_independently():
    got = expected()
    got["title"] = "Other Soup"
    got["ingredient_lines"] = ["2 cups stock", "1 shallot", "extra"]
    got["steps"] = ["Simmer the stock in a pot.", "Something else."]
    got["meta"][0]["value_text"] = "3"
    got["equipment_mentions"] = [{"text": "pot", "step_index": 1, "listed": False}]
    got["extras"] = []

    scores = evaluate.score_fields(expected(), got)

    assert scores["title"] == 0
    assert scores["ingredients_p"] == pytest.approx(1 / 3) and scores["ingredients_r"] == 0.5
    assert scores["steps"] == 0.5
    assert scores["meta"] == pytest.approx(2 / 3)
    assert (scores["equipment_p"], scores["equipment_r"]) == (0, 0)  # right name, wrong step
    assert scores["extras_r"] == 0


def test_equipment_names_match_when_one_contains_the_other_in_the_same_step():
    want = [{"text": "steamer", "step_index": 1, "listed": False}]

    assert evaluate.equipment_precision_recall(want, [{"text": "steamer basket", "step_index": 1, "listed": False}]) == (1, 1)
    assert evaluate.equipment_precision_recall(want, [{"text": "steamer", "step_index": 2, "listed": False}]) == (0, 0)


def test_empty_lists_score_one_only_when_nothing_was_expected_or_invented():
    assert evaluate.precision_recall([], []) == (1.0, 1.0)
    assert evaluate.precision_recall([], ["x"]) == (0.0, 1.0)
    assert evaluate.precision_recall(["x"], []) == (0.0, 0.0)


def test_no_answer_scores_zero():
    scores = evaluate.score_fields(expected(), None)

    assert scores["title"] == 0 and scores["ingredients_r"] == 0 and scores["steps"] == 0


def golden_dir(tmp_path, reviewed=True):
    folder = tmp_path / "golden"
    folder.mkdir()
    (folder / "soup.json").write_text(json.dumps({"id": "soup", "text": TEXT, "expected": expected(), "reviewed": reviewed}))
    return folder


def test_run_eval_scores_a_perfect_model_and_reports_cost_and_the_results_row(tmp_path):
    (case,) = evaluate.load_golden(golden_dir(tmp_path))
    fake = FakeLlm(default_prompts(), {"extract": lambda text: expected()}, cost_per_call=0.01)

    scores = evaluate.run_eval(fake, [case], "v1")
    summary = evaluate.summarize(scores)
    row = evaluate.results_row(summary, "2026-10-20", "v1", "fake")

    assert summary["title"] == 1 and summary["cost_per_recipe"] == 0.01 and summary["retry_rate"] == 0
    assert row == "| 2026-10-20 | v1 | fake | 1.00 | 1.00 / 1.00 | 1.00 | 1.00 | 1.00 / 1.00 | 0.00 | $0.0100 |"


def test_run_eval_counts_retries_and_remaining_issues_for_a_bad_model(tmp_path):
    (case,) = evaluate.load_golden(golden_dir(tmp_path))
    bad = expected() | {"ingredient_lines": ["9 cups invented"]}
    fake = FakeLlm(default_prompts(), {"extract": lambda text: bad})

    summary = evaluate.summarize(evaluate.run_eval(fake, [case], "v1"))

    assert summary["retry_rate"] == 1 and summary["errors_per_recipe"] > 0 and summary["ingredients_r"] == 0


def test_the_report_warns_when_the_golden_set_is_unreviewed(tmp_path):
    (case,) = evaluate.load_golden(golden_dir(tmp_path, reviewed=False))
    scores = evaluate.run_eval(FakeLlm(default_prompts(), {"extract": lambda t: expected()}), [case], "v1")

    text = evaluate.format_eval(scores, evaluate.summarize(scores), "ROW", unreviewed=1)

    assert "still unreviewed drafts" in text and "ROW" in text


def test_cli_dry_run_counts_the_golden_set(tmp_path, capsys):
    golden_dir(tmp_path, reviewed=False)
    settings = tmp_path / "c.toml"
    settings.write_text('[llm]\nmodel = "m"\n\n[pricing."m"]\ninput_per_mtok = 1000000\noutput_per_mtok = 0\n')

    assert main(["eval", "extract", "--config", str(settings), "--golden", str(tmp_path / "golden"), "--dry-run"]) == 0

    output = capsys.readouterr().out
    assert "1 golden recipes (1 unreviewed)" in output and "estimated input cost" in output


def test_cli_eval_without_a_model_or_golden_set_explains_itself(tmp_path, capsys):
    settings = tmp_path / "c.toml"
    settings.write_text("")

    assert main(["eval", "extract", "--config", str(settings), "--golden", str(tmp_path / "none")]) == 2
    assert "no golden recipes" in capsys.readouterr().err

    golden_dir(tmp_path)
    assert main(["eval", "extract", "--config", str(settings), "--golden", str(tmp_path / "golden")]) == 2
    assert "no model configured" in capsys.readouterr().err
