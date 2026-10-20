import shutil

import pytest

from recetario_ingest import pdf, segment

RECIPE = "MUSHROOM\nSalad          Prep Time: 15 mins\n\nIngredients\n4 slices bacon      1/2 tsp salt\n2 eggs\n\nDirectionst\n1. Fry the bacon.\n2. Enjoy.\n\n12     Mushroom Salad"
SIDE_BY_SIDE = "EASY\nRice          Prep Time: 30 mins\n\nIngredients    Directionst\n2 tbsps olive oil    1. Heat the oil.\n\n92    Easy Rice"
CONTINUED = "9. Put everything in the fridge.\n10. Serve.\n\nFruit Salad                  65"


def test_footer_reads_the_title_and_page_number_on_either_side():
    assert pdf.footer("text\n\n12                    Mushroom Salad") == (12, "Mushroom Salad")
    assert pdf.footer("text\n\nKale and Capers                      18") == (18, "Kale and Capers")
    assert pdf.footer("text\n\n18\n\nAnchovies, Kale, and Capers") == (18, "Anchovies, Kale, and Capers")  # reading order
    assert pdf.footer("text\n\nKale and Capers\n\n19") == (19, "Kale and Capers")
    assert pdf.footer("just text") is None
    assert pdf.footer("") is None


def test_a_label_in_a_column_still_counts():
    assert segment.classify(SIDE_BY_SIDE)[0] == "recipe"
    assert segment.classify(RECIPE)[0] == "recipe"  # "Directionst" has a stray letter
    assert segment.classify("Ingredients\nflour")[0] == "ambiguous"


def test_pages_become_recipes_and_photo_pages_are_skipped():
    slices = segment.slice_pages(["Cover page\nMy Cookbook", "", RECIPE, "", "", SIDE_BY_SIDE, "The end\nThanks for reading"])
    assert [(s.entry.src, segment.classify(s.text)[0]) for s in slices] == [
        ("page-1", "other"), ("page-3", "recipe"), ("page-6", "recipe"), ("page-7", "other"),
    ]
    assert slices[1].entry.title == "Mushroom Salad"
    assert slices[2].entry.title == "Easy Rice"


def test_a_page_that_starts_with_a_numbered_step_continues_the_recipe():
    slices = segment.slice_pages([RECIPE, CONTINUED, SIDE_BY_SIDE])
    assert [s.entry.src for s in slices] == ["page-1", "page-3"]
    assert "9. Put everything in the fridge." in slices[0].text


def test_a_numbered_page_after_front_matter_is_not_a_continuation():
    slices = segment.slice_pages(["Welcome", "1. Not a recipe step"])
    assert len(slices) == 2


needs_poppler = pytest.mark.skipif(shutil.which("pdftotext") is None, reason="poppler is not installed")


def tiny_pdf(path, lines):
    """A one-page PDF written by hand, so the reader is tested on a real file without a PDF library."""
    stream = "BT /F1 12 Tf 72 720 Td 14 TL " + " ".join(f"({line}) Tj T*" for line in lines) + " ET"
    objects = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    body, offsets = "%PDF-1.4\n", []
    for number, obj in enumerate(objects, 1):
        offsets.append(len(body))
        body += f"{number} 0 obj\n{obj}\nendobj\n"
    xref = len(body)
    body += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n" + "".join(f"{offset:010d} 00000 n \n" for offset in offsets)
    body += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n"
    path.write_bytes(body.encode("latin-1"))


@needs_poppler
def test_reads_pages_from_a_real_file(tmp_path):
    path = tmp_path / "book.pdf"
    tiny_pdf(path, ["Ingredients", "2 eggs", "Directions", "1. Boil."])
    book = pdf.read_pdf(path)
    assert book.metadata.pages == 1 and len(book.pages) == 1
    assert "2 eggs" in book.pages[0] and segment.classify(book.pages[0])[0] == "recipe"


def test_a_missing_poppler_says_how_to_install_it(monkeypatch, tmp_path):
    monkeypatch.setattr(shutil, "which", lambda name: None)
    with pytest.raises(pdf.PdfError, match="poppler"):
        pdf.read_metadata(tmp_path / "x.pdf")


@pytest.mark.parametrize(
    ("extracted", "printed", "expected"),
    [
        ("MUSHROOM Salad", "Mushroom Salad", "Mushroom Salad"),
        ("MUSHROOM Salad", "page 12", "Mushroom Salad"),
        ("EASY African Style Brown Rice", "page 92", "Easy African Style Brown Rice"),
        ("BBQ Chicken", "page 3", "BBQ Chicken"),
        ("Corn On The Cob", "Easiest Corn On The Cob", "Corn On The Cob"),
        ("ALL CAPS TITLE", "page 5", "ALL CAPS TITLE"),
    ],
)
def test_reconcile_title(extracted, printed, expected):
    assert segment.reconcile_title(extracted, printed) == expected


def test_an_authoritative_printed_title_replaces_a_split_one():
    assert segment.reconcile_title("CABBAGE", "Cabbage from Russia", authoritative=True) == "Cabbage from Russia"
    assert segment.reconcile_title("CABBAGE", "Cabbage from Russia") == "CABBAGE"  # not authoritative, and not mixed case


CONTENTS = "Table of Contents\nMushroom Salad 12\nKale Salad 13\nSpinach Salad I 14\nAlmond Salad 15\nSeattle Salad 16"


def test_titles_come_from_the_printed_contents_and_follow_the_page_offset():
    # the recipe is the third PDF page and its footer says it is printed page 12, so printed numbers are 9 more than PDF pages
    pages = [CONTENTS, "Cover", RECIPE]
    assert segment.printed_titles(pages) == {3: "Mushroom Salad", 4: "Kale Salad", 5: "Spinach Salad I", 6: "Almond Salad", 7: "Seattle Salad"}
    assert segment.slice_pages(pages)[-1].entry.title == "Mushroom Salad"


def test_a_page_that_is_not_a_contents_page_adds_nothing():
    assert segment.printed_titles(["Notes\nOne 1\nTwo 2", RECIPE]) == {}
