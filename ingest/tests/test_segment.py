import pytest

from recetario_ingest import segment
from recetario_ingest.epub import BookText, TocEntry

TEXT = "Intro\nBonus\nOne\nIngredients\nrice\nDirections\ncook\nTwo\nIngredients\neggs\nEnd\nBye\n"
BOOK = BookText(
    text=TEXT,
    anchors={"a.html#intro": 0, "a.html#one": TEXT.index("One"), "a.html#two": TEXT.index("Two"), "a.html#end": TEXT.index("End")},
    documents={"a.html": 0},
)


def entries(*pairs):
    return [TocEntry(title, src) for title, src in pairs]


def test_slices_run_from_each_anchor_to_the_next_in_reading_order():
    toc = entries(("Two", "a.html#two"), ("Intro", "a.html#intro"), ("One", "a.html#one"), ("End", "a.html#end"))

    slices = {s.entry.title: s.text for s in segment.slice_book(BOOK, toc)}

    assert slices["Intro"] == "Intro\nBonus"
    assert slices["One"] == "One\nIngredients\nrice\nDirections\ncook"
    assert slices["End"] == "End\nBye"


def test_entry_without_a_fragment_starts_at_its_document():
    slices = segment.slice_book(BOOK, entries(("Whole", "a.html")))

    assert slices[0].text == TEXT.strip()


def test_entry_whose_anchor_is_missing_has_no_text():
    slices = segment.slice_book(BOOK, entries(("Ghost", "a.html#nope"), ("One", "a.html#one")))

    assert slices[0].text is None
    assert slices[1].text is not None


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("One\nIngredients\nrice\nDirections\ncook", ("recipe", 1.0)),
        ("One\nINGREDIENT:\nrice\nInstructions:\nmix", ("recipe", 1.0)),
        ("Two\nIngredients\neggs", ("ambiguous", 0.5)),
        ("Two\nDirections\nmix", ("ambiguous", 0.5)),
        ("A recipe with ingredients and directions in a sentence", ("other", 1.0)),
        ("", ("other", 1.0)),
        (None, ("unresolved", 1.0)),
    ],
)
def test_classify_needs_both_labels_on_their_own_line(text, expected):
    assert segment.classify(text) == expected


@pytest.mark.parametrize(("raw", "clean"), [("12. Pesto", "Pesto"), ("3) Soup", "Soup"), ("Conclusion", "Conclusion"), ("7 Up Cake", "7 Up Cake")])
def test_clean_title_drops_only_the_leading_number(raw, clean):
    assert segment.clean_title(raw) == clean
