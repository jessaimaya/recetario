"""Read an EPUB: metadata, table of contents and the plain text of every document.

The text keeps one line per block element, and every anchor (`id` or `<a name>`) is recorded as an offset into the
concatenated text, so a table-of-contents entry like `text00000.html#filepos0000091782` maps to a position.
"""

import posixpath
import re
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path

_NS = {
    "c": "urn:oasis:names:tc:opendocument:xmlns:container",
    "opf": "http://www.idpf.org/2007/opf",
    "dc": "http://purl.org/dc/elements/1.1/",
    "ncx": "http://www.daisy.org/z3986/2005/ncx/",
}
_ISBN = re.compile(r"^(?:97[89])?\d{9}[\dX]$")


class EpubError(Exception):
    pass


@dataclass(frozen=True)
class EpubMetadata:
    title: str
    author: str | None
    isbn: str | None
    asin: str | None
    lang: str | None


@dataclass(frozen=True)
class TocEntry:
    title: str
    src: str  # "<document>#<anchor>", the document relative to the OPF file


@dataclass(frozen=True)
class BookText:
    text: str
    anchors: dict[str, int]  # "<document>#<anchor>" -> offset into text
    documents: dict[str, int]  # "<document>" -> offset of its first character


@dataclass(frozen=True)
class Epub:
    metadata: EpubMetadata
    toc: list[TocEntry]
    book_text: BookText


def read_metadata(path: Path) -> EpubMetadata:
    with _open(path) as archive:
        return _metadata(_parse(archive.read(_opf_path(archive))))


def read_epub(path: Path) -> Epub:
    with _open(path) as archive:
        opf_path = _opf_path(archive)
        opf = _parse(archive.read(opf_path))
        opf_dir = posixpath.dirname(opf_path)

        manifest = {item.get("id"): item for item in opf.findall("opf:manifest/opf:item", _NS)}
        spine = [
            manifest[ref.get("idref")].get("href")
            for ref in opf.findall("opf:spine/opf:itemref", _NS)
            if ref.get("idref") in manifest
        ]
        documents = {
            name: archive.read(posixpath.join(opf_dir, name)).decode("utf-8", errors="replace") for name in spine
        }
        return Epub(
            metadata=_metadata(opf),
            toc=_toc(archive, opf, manifest, opf_dir),
            book_text=_book_text(documents),
        )


def _open(path: Path) -> zipfile.ZipFile:
    try:
        return zipfile.ZipFile(path)
    except zipfile.BadZipFile as error:
        raise EpubError(f"{path.name} is not a valid EPUB (not a zip file)") from error


def _parse(data: bytes) -> ET.Element:
    try:
        return ET.fromstring(data)
    except ET.ParseError as error:
        raise EpubError(f"malformed XML in the EPUB: {error}") from error


def _opf_path(archive: zipfile.ZipFile) -> str:
    try:
        container = _parse(archive.read("META-INF/container.xml"))
    except KeyError as error:
        raise EpubError("META-INF/container.xml is missing") from error
    rootfile = container.find("c:rootfiles/c:rootfile", _NS)
    if rootfile is None or not rootfile.get("full-path"):
        raise EpubError("container.xml does not point to a package file")
    return rootfile.get("full-path")


def _metadata(opf: ET.Element) -> EpubMetadata:
    def first(tag: str) -> str | None:
        element = opf.find(f"opf:metadata/dc:{tag}", _NS)
        return " ".join(element.text.split()) if element is not None and element.text else None

    isbn = asin = None
    for identifier in opf.findall("opf:metadata/dc:identifier", _NS):
        value = (identifier.text or "").strip()
        scheme = (identifier.get(f"{{{_NS['opf']}}}scheme") or "").upper()
        compact = value.removeprefix("urn:isbn:").replace("-", "").upper()
        if scheme == "ASIN":
            asin = value
        elif scheme == "ISBN" or value.lower().startswith("urn:isbn:") or _ISBN.match(compact):
            isbn = compact
    return EpubMetadata(title=first("title") or "(untitled)", author=first("creator"), isbn=isbn, asin=asin, lang=first("language"))


def _toc(archive: zipfile.ZipFile, opf: ET.Element, manifest: dict, opf_dir: str) -> list[TocEntry]:
    ncx_id = opf.find("opf:spine", _NS).get("toc") if opf.find("opf:spine", _NS) is not None else None
    item = manifest.get(ncx_id)
    if item is None:
        item = next((i for i in manifest.values() if i.get("media-type") == "application/x-dtbncx+xml"), None)
    if item is None:
        return []
    ncx = _parse(archive.read(posixpath.join(opf_dir, item.get("href"))))
    ncx_dir = posixpath.dirname(item.get("href"))
    entries = []
    for point in ncx.iter(f"{{{_NS['ncx']}}}navPoint"):
        label = point.find("ncx:navLabel/ncx:text", _NS)
        content = point.find("ncx:content", _NS)
        if label is None or content is None or not content.get("src"):
            continue
        document, _, fragment = content.get("src").partition("#")
        src = posixpath.normpath(posixpath.join(ncx_dir, document)) + (f"#{fragment}" if fragment else "")
        entries.append(TocEntry(title=" ".join((label.text or "").split()), src=src))
    return entries


_BLOCKS = {"p", "div", "li", "ol", "ul", "br", "tr", "table", "blockquote", "section", "h1", "h2", "h3", "h4", "h5", "h6"}
_SKIPPED = {"head", "script", "style"}


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.lines: list[str] = []
        self.anchors: dict[str, int] = {}
        self._length = 0  # characters emitted so far, including the newline after every line
        self._buffer = ""
        self._skipping = 0

    def handle_starttag(self, tag, attrs):
        if tag in _SKIPPED:
            self._skipping += 1
            return
        if tag in _BLOCKS:
            self._flush()
        attributes = dict(attrs)
        anchor = attributes.get("id") or (attributes.get("name") if tag == "a" else None)
        if anchor:
            self.anchors.setdefault(anchor, self._length + len(" ".join(self._buffer.split())))

    def handle_endtag(self, tag):
        if tag in _SKIPPED:
            self._skipping = max(0, self._skipping - 1)
        elif tag in _BLOCKS:
            self._flush()

    def handle_data(self, data):
        if not self._skipping:
            self._buffer += data

    def close(self):
        super().close()
        self._flush()

    def _flush(self):
        line = " ".join(self._buffer.split())  # also folds non-breaking spaces
        self._buffer = ""
        if line:
            self.lines.append(line)
            self._length += len(line) + 1


def _book_text(documents: dict[str, str]) -> BookText:
    parts, anchors, starts, base = [], {}, {}, 0
    for name, html in documents.items():
        extractor = _TextExtractor()
        extractor.feed(html)
        extractor.close()
        text = "\n".join(extractor.lines) + ("\n" if extractor.lines else "")
        starts[name] = base
        anchors.update({f"{name}#{anchor}": base + offset for anchor, offset in extractor.anchors.items()})
        parts.append(text)
        base += len(text)
    return BookText(text="".join(parts), anchors=anchors, documents=starts)
