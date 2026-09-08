from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Iterable

from .models import Chunk, Note

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS notes (
    file_id TEXT PRIMARY KEY,
    source_path TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    content_hash TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS chunks (
    chunk_id TEXT PRIMARY KEY,
    file_id TEXT NOT NULL REFERENCES notes(file_id) ON DELETE CASCADE,
    heading_path TEXT NOT NULL,
    section_index INTEGER NOT NULL,
    heading_occurrence INTEGER NOT NULL,
    part_index INTEGER NOT NULL,
    raw_markdown TEXT NOT NULL,
    embedding_text TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    word_count INTEGER NOT NULL,
    token_count INTEGER,
    start_line INTEGER NOT NULL,
    end_line INTEGER NOT NULL,
    UNIQUE(file_id, section_index, part_index)
);

CREATE INDEX IF NOT EXISTS chunks_file_id ON chunks(file_id);

CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts USING fts5(
    file_id UNINDEXED,
    source_path UNINDEXED,
    content_hash UNINDEXED,
    title,
    body,
    tokenize = 'unicode61'
);

CREATE TABLE IF NOT EXISTS chunk_settings (
    singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
    signature TEXT NOT NULL
);
"""


class ChunkDatabase:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path)
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.executescript(SCHEMA)
        columns = {
            row[1]
            for row in self.connection.execute("PRAGMA table_info(chunks)").fetchall()
        }
        if "content_hash" not in columns:
            self.connection.execute(
                "ALTER TABLE chunks ADD COLUMN content_hash TEXT NOT NULL DEFAULT ''"
            )
        if "token_count" not in columns:
            self.connection.execute("ALTER TABLE chunks ADD COLUMN token_count INTEGER")

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> "ChunkDatabase":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def replace_note(self, note: Note, chunks: Iterable[Chunk]) -> int:
        self.connection.execute(
            "DELETE FROM notes WHERE source_path = ? AND file_id != ?",
            (note.relative_path, note.file_id),
        )
        self.connection.execute(
            """
            INSERT INTO notes(file_id, source_path, title, content_hash)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(file_id) DO UPDATE SET
                source_path = excluded.source_path,
                title = excluded.title,
                content_hash = excluded.content_hash
            """,
            (note.file_id, note.relative_path, note.title, note.content_hash),
        )
        self.connection.execute("DELETE FROM chunks WHERE file_id = ?", (note.file_id,))
        count = 0
        for chunk in chunks:
            self.connection.execute(
                """
                INSERT INTO chunks(
                    chunk_id, file_id, heading_path, section_index,
                    heading_occurrence, part_index, raw_markdown,
                    embedding_text, content_hash, word_count, token_count,
                    start_line, end_line
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    chunk.chunk_id,
                    chunk.file_id,
                    json.dumps(chunk.heading_path, ensure_ascii=False),
                    chunk.section_index,
                    chunk.heading_occurrence,
                    chunk.part_index,
                    chunk.raw_markdown,
                    chunk.embedding_text,
                    chunk.content_hash,
                    chunk.word_count,
                    chunk.token_count,
                    chunk.start_line,
                    chunk.end_line,
                ),
            )
            count += 1
        return count

    def delete_file_ids(self, file_ids: Iterable[str]) -> None:
        self.connection.executemany(
            "DELETE FROM notes WHERE file_id = ?", ((file_id,) for file_id in file_ids)
        )

    def delete_notes_not_in(self, file_ids: set[str]) -> None:
        rows = self.connection.execute("SELECT file_id FROM notes").fetchall()
        stale = [(file_id,) for (file_id,) in rows if file_id not in file_ids]
        self.connection.executemany("DELETE FROM notes WHERE file_id = ?", stale)

    def sync_keyword_notes(
        self, notes: Iterable[Note], *, rebuild: bool = False
    ) -> None:
        """Maintain full-note FTS rows within the caller's sync transaction.

        This also backfills existing chunk databases without re-chunking notes
        or generating embeddings. Frontmatter is excluded from searchable text.
        """
        stored = {
            row[0]: row[1:]
            for row in self.connection.execute(
                "SELECT file_id, source_path, title, content_hash FROM notes_fts"
            )
        }
        seen = set()
        for note in notes:
            seen.add(note.file_id)
            if not rebuild and stored.get(note.file_id) == (
                note.relative_path,
                note.title,
                note.content_hash,
            ):
                continue
            self.connection.execute(
                "DELETE FROM notes_fts WHERE file_id = ?", (note.file_id,)
            )
            self.connection.execute(
                "INSERT INTO notes_fts(file_id, source_path, content_hash, title, body) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    note.file_id,
                    note.relative_path,
                    note.content_hash,
                    note.title,
                    note.body,
                ),
            )
        self.connection.executemany(
            "DELETE FROM notes_fts WHERE file_id = ?",
            ((file_id,) for file_id in stored.keys() - seen),
        )
