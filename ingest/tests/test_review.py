import itertools

import pytest

from recetario_ingest import review
from recetario_ingest.vocabulary import Vocabulary, create_proposal, find_proposal, open_proposals


def propose_new(conn, term, name_en, label, parent="", confidence=0.8):
    spec = {"name_en": name_en, "name_es_mx": label} | ({"parent_en": parent} if parent else {})
    return create_proposal(conn, "equipment", term, {"examples": [{"recipe": "R", "text": "Use it."}], "candidates": ["skillet"]},
                           "new_entry", None, spec, confidence, [{"source": "llm", "ref": "m", "snippet": "because"}], None)


def propose_map(conn, term, target, confidence=0.8):
    target_id = Vocabulary(conn, "equipment").by_name(target).id
    return create_proposal(conn, "equipment", term, {}, "map_to", target_id, None, confidence, [], None)


def inputs(*answers):
    queue = iter(answers)
    return lambda prompt: next(queue)


def test_accepting_a_mapping_writes_a_confirmed_review_alias(conn):
    propose_map(conn, "saute pan", "skillet")
    (proposal,) = open_proposals(conn)

    outcome = review.accept(conn, proposal)

    assert "alias of skillet" in outcome.message
    assert conn.execute("SELECT source, confirmed FROM aliases WHERE text_normalized = 'saute pan'").fetchone()[:] == ("review", 1)
    assert find_proposal(conn, "equipment", "saute pan").status == "accepted"


def test_accepting_a_new_entry_creates_it_with_label_info_row_and_alias(conn):
    propose_new(conn, "wok", "wok", "wok", parent="skillet")
    (proposal,) = open_proposals(conn)

    review.accept(conn, proposal)

    wok = Vocabulary(conn, "equipment").exact("wok")
    assert wok.label_es_mx == "wok"
    assert conn.execute("SELECT parent_id FROM vocab_entries WHERE id = ?", (wok.id,)).fetchone()[0] == Vocabulary(conn, "equipment").by_name("skillet").id
    assert conn.execute("SELECT owned FROM equipment_info WHERE entry_id = ?", (wok.id,)).fetchone()[0] == 0


def test_the_other_spellings_of_a_new_entry_are_resolved_with_it(conn):
    for term in ("sous vide cooker", "sous vide vessel", "sous vide water bath"):
        propose_new(conn, term, "sous vide cooker", "circulador de inmersión")
    propose_new(conn, "smoker", "smoker", "ahumador")
    first = open_proposals(conn)[1]  # "sous vide cooker" sorts after "smoker"

    outcome = review.accept(conn, first)

    assert "also resolved" in outcome.message
    assert [p.term for p in open_proposals(conn)] == ["smoker"]
    vocabulary = Vocabulary(conn, "equipment")
    assert vocabulary.exact("sous vide vessel").name_en == "sous vide cooker" == vocabulary.exact("sous vide water bath").name_en


def test_a_new_entry_whose_name_exists_just_adds_the_alias(conn):
    propose_new(conn, "square baking dish", "skillet", "sartén")
    (proposal,) = open_proposals(conn)

    outcome = review.accept(conn, proposal)

    assert "alias of skillet" in outcome.message
    assert Vocabulary(conn, "equipment").exact("square baking dish").name_en == "skillet"


def test_a_proposal_without_a_target_cannot_be_accepted_as_is(conn):
    create_proposal(conn, "equipment", "tray", {}, "map_to", None, None, 0.5, [], None)
    (proposal,) = open_proposals(conn)

    with pytest.raises(ValueError, match="use edit"):
        review.accept(conn, proposal)


def test_rejecting_keeps_the_proposal_so_the_term_is_never_asked_again(conn):
    propose_new(conn, "container", "container", "recipiente")
    (proposal,) = open_proposals(conn)

    review.reject(conn, proposal)

    assert find_proposal(conn, "equipment", "container").status == "rejected" and open_proposals(conn) == []
    assert Vocabulary(conn, "equipment").exact("container") is None


def test_the_interactive_loop_applies_each_choice_and_counts_them(conn):
    propose_map(conn, "a pan", "skillet")
    propose_new(conn, "b wok", "wok", "wok")
    propose_new(conn, "c container", "container", "recipiente")
    propose_new(conn, "d tray", "tray", "charola")
    shown = []

    counts = review.review_proposals(conn, input_fn=inputs("a", "huh?", "r", "s", "q"), output=shown.append)

    assert counts == {"accepted": 1, "edited": 0, "rejected": 1, "skipped": 1}
    assert find_proposal(conn, "equipment", "a pan").status == "accepted"
    assert find_proposal(conn, "equipment", "b wok").status == "rejected"
    assert [p.term for p in open_proposals(conn)] == ["c container", "d tray"] or len(open_proposals(conn)) == 2
    assert any("seen in" in line and "Use it." in line for line in shown)


def test_edit_maps_a_term_to_another_existing_entry(conn):
    propose_new(conn, "stock pot", "stock pot", "olla")

    counts = review.review_proposals(conn, input_fn=inputs("e", "nope", "e", "pot"), output=lambda line: None)

    assert counts["edited"] == 1
    assert Vocabulary(conn, "equipment").exact("stock pot").name_en == "pot"
    assert find_proposal(conn, "equipment", "stock pot").status == "edited"


def test_edit_can_create_a_new_entry_with_changed_names(conn):
    propose_new(conn, "dutch oven", "dutch oven", "olla de hierro fundido")

    review.review_proposals(conn, input_fn=inputs("e", "new", "", "cocotte", "pot"), output=lambda line: None)

    entry = Vocabulary(conn, "equipment").exact("dutch oven")
    assert entry.label_es_mx == "cocotte"
    assert conn.execute("SELECT parent_id FROM vocab_entries WHERE id = ?", (entry.id,)).fetchone()[0] == Vocabulary(conn, "equipment").by_name("pot").id


def test_kind_filter_limits_what_is_shown(conn):
    propose_new(conn, "wok", "wok", "wok")
    create_proposal(conn, "attribute_key", "net carbs", {}, "new_entry", None, {"name_en": "net_carbs_g", "name_es_mx": "Netos", "value_type": "number"}, 0.7, [], None)

    review.review_proposals(conn, kind="attribute_key", input_fn=inputs("a"), output=lambda line: None)

    assert [p.term for p in open_proposals(conn)] == ["wok"]
    key = Vocabulary(conn, "attribute_key").exact("net_carbs_g")
    assert conn.execute("SELECT value_type FROM key_info WHERE entry_id = ?", (key.id,)).fetchone()[0] == "number"


def auto_alias(conn, text, target):
    entry = Vocabulary(conn, "equipment").by_name(target)
    conn.execute("INSERT INTO aliases (kind, lang, text_normalized, entry_id, confirmed, source) VALUES ('equipment', 'en', ?, ?, 0, 'auto')", (text, entry.id))


def test_spot_check_keeps_or_fixes_auto_accepted_aliases(conn):
    auto_alias(conn, "a kept thing", "skillet")
    auto_alias(conn, "b wrong thing", "skillet")
    auto_alias(conn, "c later thing", "skillet")

    counts = review.spot_check_auto(conn, input_fn=inputs("k", "f", "bogus", "f", "pot", "s"), output=lambda line: None)

    assert counts == {"kept": 1, "fixed": 1, "skipped": 1}
    rows = {r[0]: r[1:] for r in conn.execute("SELECT text_normalized, confirmed, source FROM aliases WHERE text_normalized LIKE '% thing'")}
    assert rows["a kept thing"] == (1, "auto") and rows["b wrong thing"] == (1, "review") and rows["c later thing"] == (0, "auto")
    assert Vocabulary(conn, "equipment").exact("b wrong thing").name_en == "pot"
