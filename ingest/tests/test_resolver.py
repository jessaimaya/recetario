from fakes import ResolveScript, map_answer, new_answer

from recetario_ingest.resolver import Resolver
from recetario_ingest.terms import TermExample
from recetario_ingest.vocabulary import Vocabulary, find_proposal

EXAMPLES = [TermExample("Fried Rice", "Heat the wok over high heat.")]


def resolver(conn, *answers):
    llm = ResolveScript(*answers)
    return Resolver(conn, llm, "v1", source_id=None), llm


def test_a_known_term_resolves_without_asking_the_model(conn):
    r, llm = resolver(conn)

    result = r.resolve("equipment", "frying pan", EXAMPLES)

    assert (result.method, result.outcome, result.entry) == ("exact", "resolved", "skillet") and llm.inputs == []


def test_a_confident_mapping_to_a_shown_candidate_is_accepted_as_an_unconfirmed_auto_alias(conn):
    r, llm = resolver(conn, map_answer("skillet", 0.95))

    result = r.resolve("equipment", "cast iron skillet", EXAMPLES)

    assert (result.method, result.outcome, result.entry) == ("llm", "resolved", "skillet")
    assert conn.execute("SELECT source, confirmed FROM aliases WHERE text_normalized = 'cast iron skillet'").fetchone()[:] == ("auto", 0)
    assert find_proposal(conn, "equipment", "cast iron skillet") is None
    assert r.resolve("equipment", "cast iron skillet", EXAMPLES).method == "exact" and len(llm.inputs) == 1


def test_a_mapping_below_the_threshold_waits_for_review_and_writes_no_alias(conn):
    r, _ = resolver(conn, map_answer("skillet", 0.8))

    result = r.resolve("equipment", "cast iron skillet", EXAMPLES)

    proposal = find_proposal(conn, "equipment", "cast iron skillet")
    assert result.outcome == "proposed" and proposal.status == "open" and proposal.confidence == 0.8
    assert proposal.target_entry_id == Vocabulary(conn, "equipment").by_name("skillet").id
    assert Vocabulary(conn, "equipment").exact("cast iron skillet") is None


def test_a_target_the_model_was_never_shown_is_never_auto_accepted(conn):
    r, _ = resolver(conn, map_answer("microwave", 0.99))  # "microwave" is not a fuzzy neighbour of "cast iron skillet"

    result = r.resolve("equipment", "cast iron skillet", EXAMPLES)

    assert result.outcome == "proposed"
    assert Vocabulary(conn, "equipment").exact("cast iron skillet") is None


def test_a_new_entry_is_always_a_proposal_even_when_the_model_is_sure(conn):
    r, _ = resolver(conn, new_answer("wok", "wok", confidence=0.99, value_type="text", unit="x"))

    result = r.resolve("equipment", "wok", EXAMPLES)

    proposal = find_proposal(conn, "equipment", "wok")
    assert result.outcome == "proposed" and proposal.action == "new_entry"
    assert proposal.new_entry == {"name_en": "wok", "name_es_mx": "wok"}  # value_type and unit only matter for attribute keys
    assert Vocabulary(conn, "equipment").by_name("wok") is None


def test_attribute_key_proposals_keep_their_type_and_unit(conn):
    r, _ = resolver(conn, new_answer("net_carbs_g", "Carbohidratos netos (g)", value_type="number", unit="g"))

    r.resolve("attribute_key", "net carbs", [])

    assert find_proposal(conn, "attribute_key", "net carbs").new_entry["value_type"] == "number"


def test_a_new_entry_that_already_exists_becomes_a_capped_mapping_proposal(conn):
    r, _ = resolver(conn, new_answer("skillet", "sartén", confidence=0.99))

    result = r.resolve("equipment", "pan with a lid", EXAMPLES)

    proposal = find_proposal(conn, "equipment", "pan with a lid")
    assert result.outcome == "proposed" and proposal.action == "map_to" and proposal.confidence < 0.9


def test_an_open_or_decided_proposal_is_never_asked_again(conn):
    r, llm = resolver(conn, new_answer("wok", "wok"))
    r.resolve("equipment", "wok", EXAMPLES)
    conn.execute("UPDATE proposals SET status = 'rejected'")

    again = r.resolve("equipment", "wok", EXAMPLES)

    assert again.method == "existing_proposal" and len(llm.inputs) == 1


def test_the_model_sees_the_term_examples_and_the_candidates_with_their_aliases(conn):
    r, llm = resolver(conn, new_answer("wok", "wok"))

    r.resolve("equipment", "wok pan", EXAMPLES)

    shown = llm.inputs[0]
    assert "kind: equipment" in shown and "term: wok pan" in shown
    assert 'recipe "Fried Rice": Heat the wok over high heat.' in shown
    assert "- skillet (es-MX: sartén; aliases:" in shown and "frying pan" in shown


def test_usage_is_passed_up_for_cost_tracking(conn):
    r, _ = resolver(conn, new_answer("wok", "wok"))

    assert r.resolve("equipment", "wok", EXAMPLES).usage.cost_usd == 0.001
