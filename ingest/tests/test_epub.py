import zipfile

import pytest
from epub_fixtures import make_epub, recipe_html, sample_book

from recetario_ingest import epub


def test_metadata_reads_title_author_language_and_identifiers(tmp_path):
    identifiers = (
        '<dc:identifier id="bookid">urn:uuid:1</dc:identifier>'
        '<dc:identifier opf:scheme="ASIN">B000TEST01</dc:identifier>'
        '<dc:identifier>urn:isbn:978-0-306-40615-7</dc:identifier>'
    )
    path = sample_book(tmp_path / "b.epub", identifiers=identifiers, title="  Spaced \n Title ")

    metadata = epub.read_metadata(path)

    assert metadata == epub.EpubMetadata("Spaced Title", "Test Author", "9780306406157", "B000TEST01", "en")


def test_uuid_identifier_is_not_an_isbn(tmp_path):
    metadata = epub.read_metadata(sample_book(tmp_path / "b.epub"))

    assert metadata.isbn is None and metadata.asin is None


def test_toc_keeps_reading_order_and_cleans_label_whitespace(tmp_path):
    book = epub.read_epub(sample_book(tmp_path / "b.epub", recipes=3))

    assert [entry.title for entry in book.toc] == ["Introduction", "1. Recipe 1", "2. Recipe 2", "3. Recipe 3", "Tips", "Conclusion"]
    assert book.toc[1].src == "part2.html#r1"


def test_text_has_one_line_per_block_and_skips_head_content(tmp_path):
    path = make_epub(tmp_path / "b.epub", {"a.html": "<p>One <b>bold</b>\n word</p><div>Two<br/>Three</div>"}, [("T", "a.html")])

    text = epub.read_epub(path).book_text.text

    assert text == "One bold word\nTwo\nThree\n"  # no "x" from <title>, none of the <style>


def test_anchor_offset_points_at_the_line_that_follows_it(tmp_path):
    book = epub.read_epub(sample_book(tmp_path / "b.epub", recipes=3)).book_text

    for number in (1, 2, 3):
        offset = book.anchors[f"{'part2' if number <= 2 else 'part3'}.html#r{number}"]
        assert book.text[offset:].startswith(f"Recipe {number}\n")


def test_anchors_in_later_documents_are_offset_by_the_earlier_ones(tmp_path):
    book = epub.read_epub(sample_book(tmp_path / "b.epub", recipes=3)).book_text

    assert book.documents["part2.html"] == len("Welcome!\nClick HERE for a bonus.\n")
    assert book.anchors["part1.html#intro"] == 0


def test_not_a_zip_is_an_epub_error(tmp_path):
    path = tmp_path / "broken.epub"
    path.write_text("not a zip")

    with pytest.raises(epub.EpubError, match="not a valid EPUB"):
        epub.read_metadata(path)


def test_missing_container_is_an_epub_error(tmp_path):
    path = tmp_path / "empty.epub"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip")

    with pytest.raises(epub.EpubError, match="container.xml"):
        epub.read_metadata(path)
