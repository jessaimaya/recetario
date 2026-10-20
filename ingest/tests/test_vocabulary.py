import pytest

from recetario_ingest.vocabulary import Vocabulary, add_alias, create_entry, create_proposal, find_proposal, open_proposals


def test_exact_finds_names_labels_aliases_plurals_and_hyphenated_forms(conn):
    equipment = Vocabulary(conn, "equipment")

    assert equipment.exact("skillet").name_en == "skillet"
    assert equipment.exact("Sartén").name_en == "skillet"  # es-MX label, accents ignored
    assert equipment.exact("frying pan").name_en == "skillet"  # alias
    assert equipment.exact("baking sheets").name_en == "baking sheet"  # plural
    assert equipment.exact("nonstick skillet").name_en == "skillet"
    assert equipment.exact("unicorn") is None


def test_hyphens_match_spaces(conn):
    conn.execute("INSERT INTO aliases (kind, lang, text_normalized, entry_id, confirmed, source) "
                 "SELECT 'equipment', 'en', 'non-stick pan', id, 1, 'seed' FROM vocab_entries WHERE name_en = 'skillet'")

    assert Vocabulary(conn, "equipment").exact("non stick pan").name_en == "skillet"


def test_kinds_do_not_leak_into_each_other(conn):
    assert Vocabulary(conn, "ingredient").exact("skillet") is None
    assert Vocabulary(conn, "attribute_key").exact("servings").kind == "attribute_key"


def test_a_merged_entry_answers_with_its_target(conn):
    skillet = Vocabulary(conn, "equipment").by_name("skillet")
    old = create_entry(conn, "equipment", "frypan", {"es-MX": "sartencito"})
    conn.execute("UPDATE vocab_entries SET merged_into_id = ? WHERE id = ?", (skillet.id, old))

    vocabulary = Vocabulary(conn, "equipment")

    assert vocabulary.exact("frypan").id == skillet.id and vocabulary.by_name("frypan") is None


def test_candidates_rank_by_similarity_and_whole_word_containment(conn):
    names = [c.entry.name_en for c in Vocabulary(conn, "equipment").candidates("heavy bottomed pot")]

    assert names[0] == "pot"
    assert "skillet" not in names[:1]


def test_candidates_can_be_empty_for_unrelated_text(conn):
    assert Vocabulary(conn, "equipment").candidates("zzzzqx") == []


def test_create_entry_adds_labels_aliases_and_the_info_row(conn):
    entry_id = create_entry(conn, "equipment", "wok", {"es-MX": "wok"})
    key_id = create_entry(conn, "attribute_key", "net_carbs_g", {"es-MX": "Carbohidratos netos (g)"}, value_type="number", unit="g")

    assert Vocabulary(conn, "equipment").exact("wok").id == entry_id
    assert conn.execute("SELECT owned, is_basic FROM equipment_info WHERE entry_id = ?", (entry_id,)).fetchone()[:] == (0, 0)
    assert conn.execute("SELECT value_type, unit FROM key_info WHERE entry_id = ?", (key_id,)).fetchone()[:] == ("number", "g")
    assert Vocabulary(conn, "attribute_key").exact("Carbohidratos netos (g)").id == key_id


def test_add_alias_never_overwrites(conn):
    skillet = Vocabulary(conn, "equipment").by_name("skillet")
    pot = Vocabulary(conn, "equipment").by_name("pot")

    assert add_alias(conn, "equipment", "en", "wok pan", skillet.id, "auto", False) is True
    assert add_alias(conn, "equipment", "en", "wok pan", pot.id, "auto", False) is False
    assert Vocabulary(conn, "equipment").exact("wok pan").id == skillet.id


def test_proposals_round_trip_and_only_open_ones_are_listed(conn):
    pid = create_proposal(conn, "equipment", "wok", {"examples": []}, "new_entry", None, {"name_en": "wok"}, 0.8, [{"source": "llm", "ref": "m", "snippet": "s"}], None)
    conn.execute("UPDATE proposals SET status = 'rejected' WHERE id = ?", (pid,))
    create_proposal(conn, "equipment", "tray", {}, "map_to", None, None, 0.5, [], None)

    assert [p.term for p in open_proposals(conn)] == ["tray"]
    assert find_proposal(conn, "equipment", "wok").status == "rejected"
    assert find_proposal(conn, "equipment", "nothing") is None
