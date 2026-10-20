"""The stages of the ingest pipeline that exist so far: text, then segment."""

import hashlib
from pathlib import Path

from recetario_ingest import epub, segment
from recetario_ingest.pipeline import Context, Stage, StageError, StageOutput, Unit


def _hash(*parts: str) -> str:
    return hashlib.sha256("\0".join(parts).encode()).hexdigest()


class TextStage(Stage):
    """Book level: read the EPUB into one text, with the anchors and table of contents needed to cut it up."""

    name = "text"
    version = "1"

    def __init__(self, path: Path) -> None:
        self._path = path

    def units(self, ctx: Context) -> list[Unit]:
        return [Unit(key=None, input_hash=ctx.source.file_hash)]

    def run(self, ctx: Context, unit: Unit) -> StageOutput:
        book = epub.read_epub(self._path)
        return StageOutput(
            {
                "text": book.book_text.text,
                "anchors": book.book_text.anchors,
                "documents": book.book_text.documents,
                "toc": [{"title": entry.title, "src": entry.src} for entry in book.toc],
            }
        )


class SegmentStage(Stage):
    """Recipe level: one unit per table-of-contents entry, classified as a recipe or not."""

    name = "segment"
    version = "1"

    def units(self, ctx: Context) -> list[Unit]:
        stored = ctx.store.read("text", None)
        if not stored["toc"]:
            raise StageError("the book has no table of contents; segmenting without one isn't implemented yet")
        book = epub.BookText(stored["text"], stored["anchors"], stored["documents"])
        toc = [epub.TocEntry(**entry) for entry in stored["toc"]]
        units, seen = [], set()
        for slice_ in segment.slice_book(book, toc):
            key = _recipe_key(slice_.entry.src)
            while key in seen:  # two entries can point at the same anchor
                key += "-again"
            seen.add(key)
            input_hash = _hash(slice_.entry.src, slice_.entry.title, slice_.text or "")
            units.append(Unit(key=key, input_hash=input_hash, payload=slice_))
        return units

    def run(self, ctx: Context, unit: Unit) -> StageOutput:
        slice_ = unit.payload
        kind, confidence = segment.classify(slice_.text)
        return StageOutput(
            {
                "recipe_key": unit.key,
                "locator": slice_.entry.src,
                "toc_title": slice_.entry.title,
                "title": segment.clean_title(slice_.entry.title),
                "kind": kind,
                "confidence": confidence,
                "method": "toc",
                "text": slice_.text or "",
            }
        )


def _recipe_key(src: str) -> str:
    """The locator as a name that is safe in a file name: text00000.html#fp1 -> text00000.html-fp1."""
    return src.replace("#", "-").replace("/", "_")
