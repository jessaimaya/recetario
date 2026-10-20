"""Split a book into recipes using its table of contents, then classify each entry by its section labels.

Rules only: the table of contents gives the boundaries, and a slice is a recipe when it has both an ingredients label
and a directions label on a line of their own. Books without a usable table of contents need the label and heading
paths from DESIGN.md, which arrive when a book asks for them.
"""

import re
from collections import Counter
from dataclasses import dataclass

from recetario_ingest.epub import BookText, TocEntry

_INGREDIENTS_LABEL = re.compile(r"^ingredients?\s*:?$", re.IGNORECASE)
# one stray letter is allowed after the word: one cookbook prints "Directionst"
_DIRECTIONS_LABEL = re.compile(r"^(?:directions?|instructions?|method|preparation|steps?)\w?\s*:?$", re.IGNORECASE)
_COLUMN_GAP = re.compile(r"\s{2,}")
_STEP_START = re.compile(r"^\d+\s*[.)]\s")
_LEADING_NUMBER = re.compile(r"^\d+\s*[.)]\s*")


@dataclass(frozen=True)
class Slice:
    """One table-of-contents entry and the text it covers. `text` is None when the entry's anchor isn't in the book."""

    entry: TocEntry
    text: str | None


def slice_book(book: BookText, toc: list[TocEntry]) -> list[Slice]:
    """Cut the book text at the entries' anchors; each slice runs to the next entry in reading order."""
    located = []
    for position, entry in enumerate(toc):
        offset = book.anchors.get(entry.src)
        if offset is None and "#" not in entry.src:
            offset = book.documents.get(entry.src)
        if offset is not None:
            located.append((offset, position))
    located.sort()

    ends = {position: located[i + 1][0] if i + 1 < len(located) else len(book.text) for i, (_, position) in enumerate(located)}
    starts = dict((position, offset) for offset, position in located)
    return [
        Slice(entry, book.text[starts[position] : ends[position]].strip() if position in starts else None)
        for position, entry in enumerate(toc)
    ]


def classify(text: str | None) -> tuple[str, float]:
    """Return (kind, confidence): "recipe", "ambiguous" (only one of the two labels), "other" or "unresolved"."""
    if text is None:
        return "unresolved", 1.0
    cells = _cells(text)
    has_ingredients = any(_INGREDIENTS_LABEL.match(cell) for cell in cells)
    has_directions = any(_DIRECTIONS_LABEL.match(cell) for cell in cells)
    if has_ingredients and has_directions:
        return "recipe", 1.0
    if has_ingredients or has_directions:
        return "ambiguous", 0.5
    return "other", 1.0


def _cells(text: str) -> list[str]:
    """Every line, and every part of a line split at a wide gap, since a PDF page can put two labels side by side."""
    cells = []
    for line in text.splitlines():
        cells.append(line.strip())
        cells += [cell for cell in _COLUMN_GAP.split(line.strip()) if cell]
    return cells


def slice_pages(pages: list[str]) -> list[Slice]:
    """Cut a PDF into slices, one per recipe. A page with an ingredients label starts a recipe; a page that
    begins with a numbered step carries the previous recipe on; blank pages (photos) are skipped; any other page is
    its own slice and ends up as "other". The entry's `src` is "page-<n>", the first page of the slice."""
    printed = printed_titles(pages)
    groups: list[list[int]] = []
    starts_recipe = False
    for number, page in enumerate(pages, 1):
        lines = [line.strip() for line in page.splitlines() if line.strip()]
        if not lines:
            continue
        has_ingredients = any(_INGREDIENTS_LABEL.match(cell) for cell in _cells(page))
        continues = groups and starts_recipe and not has_ingredients and _STEP_START.match(lines[0])
        if continues:
            groups[-1].append(number)
            continue
        groups.append([number])
        starts_recipe = has_ingredients
    slices = []
    for group in groups:
        text = "\n\n".join(pages[number - 1].strip("\n") for number in group)
        slices.append(Slice(TocEntry(title=_page_title(pages[group[0] - 1], group[0], printed), src=f"page-{group[0]}"), text))
    return slices


_TOC_LINE = re.compile(r"^(?P<title>[^\d\s].*?)[\s.]+(?P<page>\d{1,4})$")


def printed_titles(pages: list[str]) -> dict[int, str]:
    """Titles from the book's printed contents, as {pdf page index: title}. A contents page is one where at least five lines
    end in a page number and which has no ingredients label; the printed numbers are mapped to PDF pages by the offset the
    page footers show (zero when they never agree)."""
    from recetario_ingest.pdf import footer

    printed: dict[int, str] = {}
    for page in pages:
        if any(_INGREDIENTS_LABEL.match(cell) for cell in _cells(page)):
            continue
        found = {}
        for line in page.splitlines():
            match = _TOC_LINE.match(line.strip())
            if match:
                found[int(match["page"])] = match["title"].strip()
        if len(found) >= 5:
            printed.update(found)
    offsets = Counter(footer(page)[0] - number for number, page in enumerate(pages, 1) if footer(page))
    offset = offsets.most_common(1)[0][0] if offsets else 0
    return {number - offset: title for number, title in printed.items()}


def _page_title(page: str, number: int, printed: dict[int, str]) -> str:
    """The printed contents title, else the footer title, else the page number."""
    from recetario_ingest.pdf import footer

    if number in printed:
        return printed[number]
    found = footer(page)
    return found[1] if found else f"page {number}"


def clean_title(toc_title: str) -> str:
    return _LEADING_NUMBER.sub("", toc_title).strip()


def reconcile_title(extracted: str, printed: str, authoritative: bool = False) -> str:
    """The model copies a header's styling ("MUSHROOM Salad") and can lose a split title. A printed title that is the same
    words wins, and so does any printed title when it is `authoritative` (a PDF's own contents page). Otherwise soften
    shouting: an all-capitals word of four letters or more in a mixed-case title is title-cased."""
    if printed and not printed.startswith("page ") and (authoritative or _letters(printed) == _letters(extracted)):
        return printed
    words = extracted.split()
    if any(word != word.upper() for word in words):
        words = [word.capitalize() if len(word) >= 4 and word == word.upper() and word.isalpha() else word for word in words]
    return " ".join(words)


def _letters(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())
