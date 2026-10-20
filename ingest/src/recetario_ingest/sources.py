"""The `sources` table: which books are known, and how far each one got."""

import hashlib
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from recetario_ingest.epub import EpubMetadata


@dataclass(frozen=True)
class Source:
    id: int
    file_hash: str
    raw_title: str
    status: str

    @property
    def slug(self) -> str:
        """Names the book's artifact folder; stable because it comes from the file contents."""
        return self.file_hash[:16]


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def find_by_hash(conn: sqlite3.Connection, file_hash: str) -> Source | None:
    return _source(conn.execute("SELECT * FROM sources WHERE file_hash = ?", (file_hash,)).fetchone())


def find_by_identifier(conn: sqlite3.Connection, metadata: EpubMetadata) -> Source | None:
    """Another file of the same book (same ISBN or ASIN)."""
    for column, value in (("isbn", metadata.isbn), ("asin", metadata.asin)):
        if value:
            row = conn.execute(f"SELECT * FROM sources WHERE {column} = ?", (value,)).fetchone()
            if row:
                return _source(row)
    return None


def register(conn: sqlite3.Connection, file_hash: str, file_format: str, metadata: EpubMetadata) -> Source:
    with conn:
        (source_id,) = conn.execute(
            """
            INSERT INTO sources (raw_title, author, isbn, asin, file_hash, format, lang)
            VALUES (?, ?, ?, ?, ?, ?, ?) RETURNING id
            """,
            (metadata.title, metadata.author, metadata.isbn, metadata.asin, file_hash, file_format, metadata.lang),
        ).fetchone()
    return Source(source_id, file_hash, metadata.title, "registered")


def set_status(conn: sqlite3.Connection, source: Source, status: str) -> Source:
    with conn:
        conn.execute(
            """
            UPDATE sources SET status = ?,
                ingested_at = CASE WHEN ? = 'done' THEN strftime('%Y-%m-%dT%H:%M:%SZ', 'now') ELSE ingested_at END
            WHERE id = ?
            """,
            (status, status, source.id),
        )
    return Source(source.id, source.file_hash, source.raw_title, status)


def _source(row: sqlite3.Row | None) -> Source | None:
    return Source(row["id"], row["file_hash"], row["raw_title"], row["status"]) if row else None
