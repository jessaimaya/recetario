"""Read a PDF: metadata and the text of every page, using poppler's `pdftotext` and `pdfinfo`.

Reading-order text is the default. On the first PDF (two-column ingredients, a nutrition table) it gave 0 extraction
retries and 0 step warnings, against 12 and 30 with layout mode, which keeps columns side by side on one line and so
interleaves a two-column ingredient list. Layout mode stays available for books where reading order scrambles a table.
"""

import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

_FOOTER_NUMBER_FIRST = re.compile(r"^(?P<page>\d{1,4})\s{2,}(?P<title>\S.*)$")
_FOOTER_NUMBER_LAST = re.compile(r"^(?P<title>\S.*?)\s{2,}(?P<page>\d{1,4})$")


class PdfError(Exception):
    pass


@dataclass(frozen=True)
class PdfMetadata:
    title: str | None
    author: str | None
    pages: int


@dataclass(frozen=True)
class PdfText:
    metadata: PdfMetadata
    pages: list[str]


def _run(command: list[str]) -> str:
    if shutil.which(command[0]) is None:
        raise PdfError(f"{command[0]} is not installed; PDF books need poppler (apt install poppler-utils, brew install poppler)")
    done = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if done.returncode != 0:
        raise PdfError(f"{command[0]} failed: {done.stderr.strip()[:200]}")
    return done.stdout


def read_metadata(path: Path) -> PdfMetadata:
    info = {}
    for line in _run(["pdfinfo", str(path)]).splitlines():
        key, _, value = line.partition(":")
        info[key.strip()] = value.strip()
    return PdfMetadata(info.get("Title") or None, info.get("Author") or None, int(info.get("Pages") or 0))


def read_pdf(path: Path, layout: bool = False) -> PdfText:
    metadata = read_metadata(path)
    text = _run(["pdftotext", *(["-layout"] if layout else []), str(path), "-"])
    pages = text.split("\f")
    if pages and not pages[-1].strip():
        pages.pop()  # pdftotext ends every page, including the last, with a form feed
    return PdfText(metadata, [_tidy(page) if layout else page.strip("\n") for page in pages])


def _tidy(page: str) -> str:
    """Shorten the long runs of spaces layout mode uses for columns, and drop trailing blanks."""
    lines = [re.sub(r" {4,}", "    ", line.rstrip()) for line in page.splitlines()]
    return "\n".join(lines).strip("\n")


def footer(page: str) -> tuple[int, str] | None:
    """(page number, title) from the last line of a page, as printed in many cookbooks ("12   Mushroom Salad")."""
    lines = [line.strip() for line in page.splitlines() if line.strip()]
    if not lines:
        return None
    for pattern in (_FOOTER_NUMBER_FIRST, _FOOTER_NUMBER_LAST):
        match = pattern.match(re.sub(r" {2,}", "  ", lines[-1]))
        if match:
            return int(match["page"]), match["title"].strip()
    if len(lines) >= 2:  # reading order puts the number and the title on separate lines, in either order
        first, last = lines[-2], lines[-1]
        if first.isdigit() and not last.isdigit():
            return int(first), last
        if last.isdigit() and not first.isdigit() and len(first) < 80:
            return int(last), first
    return None
