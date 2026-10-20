"""Split a book into recipes using its table of contents, then classify each entry by its section labels.

Rules only: the table of contents gives the boundaries, and a slice is a recipe when it has both an ingredients label
and a directions label on a line of their own. Books without a usable table of contents need the label and heading
paths from DESIGN.md, which arrive when a book asks for them.
"""

import re
from dataclasses import dataclass

from recetario_ingest.epub import BookText, TocEntry

_INGREDIENTS_LABEL = re.compile(r"^ingredients?\s*:?$", re.IGNORECASE)
_DIRECTIONS_LABEL = re.compile(r"^(?:directions?|instructions?|method|preparation|steps?)\s*:?$", re.IGNORECASE)
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
    lines = [line.strip() for line in text.splitlines()]
    has_ingredients = any(_INGREDIENTS_LABEL.match(line) for line in lines)
    has_directions = any(_DIRECTIONS_LABEL.match(line) for line in lines)
    if has_ingredients and has_directions:
        return "recipe", 1.0
    if has_ingredients or has_directions:
        return "ambiguous", 0.5
    return "other", 1.0


def clean_title(toc_title: str) -> str:
    return _LEADING_NUMBER.sub("", toc_title).strip()
