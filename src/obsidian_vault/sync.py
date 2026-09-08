from __future__ import annotations

import json
import os
import sqlite3
import warnings
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from time import monotonic
from typing import Callable

from .database import ChunkDatabase
from .markdown import chunk_note
from .models import Note
from .notes import load_note

SKIP_DIRECTORIES = {".git", ".obsidian", ".venv", "data", "node_modules", "__pycache__"}


class MissingFileIdWarning(UserWarning):
    pass


class DuplicateFileIdWarning(UserWarning):
    pass


class NoteReadWarning(UserWarning):
    pass


@dataclass(frozen=True)
class SyncResult:
    discovered_notes: int
    processed_notes: int
    chunks: int
    missing_file_ids: int
    duplicate_notes: int
    read_errors: int
    rechunked_notes: int = 0
    reused_notes: int = 0


def markdown_paths(vault: Path) -> list[Path]:
    if not vault.is_dir():
        raise NotADirectoryError(f"Vault directory does not exist: {vault}")

    def walk_error(error: OSError) -> None:
        raise error

    paths: list[Path] = []
    for directory, subdirectories, filenames in os.walk(vault, onerror=walk_error):
        subdirectories[:] = sorted(
            name
            for name in subdirectories
            if name not in SKIP_DIRECTORIES and not name.startswith(".")
        )
        for filename in sorted(filenames):
            if Path(filename).suffix.lower() in {".md", ".markdown"}:
                paths.append(Path(directory) / filename)
    return paths


def sync_vault(
    vault: Path,
    database_path: Path,
    max_words: int = 380,
    *,
    max_tokens: int | None = None,
    token_counter: Callable[[str], int] | None = None,
    token_counter_id: str | None = None,
    rebuild: bool = False,
    progress: Callable[[str], None] | None = None,
) -> SyncResult:
    report = progress or (lambda message: None)
    started = monotonic()
    vault = vault.expanduser().resolve()
    database_path = database_path.expanduser().resolve()
    notes: list[Note] = []
    missing = 0
    read_errors = 0
    paths = markdown_paths(vault)
    report(f"Checking {len(paths)} Markdown files for changes...")

    for path in paths:
        try:
            notes.append(load_note(path, vault))
        except ValueError as error:
            if str(error) == "missing file_id front matter":
                missing += 1
                warnings.warn(f"{path}: {error}; skipping note", MissingFileIdWarning)
            else:
                read_errors += 1
                warnings.warn(f"{path}: {error}; skipping note", NoteReadWarning)
        except (OSError, UnicodeError) as error:
            read_errors += 1
            warnings.warn(f"{path}: {error}; skipping note", NoteReadWarning)

    notes_by_id: dict[str, list[Note]] = defaultdict(list)
    for note in notes:
        notes_by_id[note.file_id].append(note)
    duplicate_ids = {
        file_id for file_id, matches in notes_by_id.items() if len(matches) > 1
    }
    duplicate_notes = sum(len(notes_by_id[file_id]) for file_id in duplicate_ids)
    for file_id in sorted(duplicate_ids):
        matches = ", ".join(note.relative_path for note in notes_by_id[file_id])
        warnings.warn(
            f"duplicate file_id {file_id!r} in {matches}; skipping all matching notes",
            DuplicateFileIdWarning,
        )

    valid_notes = [note for note in notes if note.file_id not in duplicate_ids]
    processed_ids: set[str] = set()
    chunk_count = 0
    reused = 0
    rechunked = 0
    # Bump version when chunking behavior changes. Custom token counters must
    # supply a stable identity to opt into reuse.
    signature = json.dumps([1, str(vault), max_words, max_tokens, token_counter_id])
    cacheable = token_counter is None or token_counter_id is not None
    with ChunkDatabase(database_path) as database:
        try:
            database.connection.execute("BEGIN")
            previous = database.connection.execute(
                "SELECT signature FROM chunk_settings WHERE singleton = 1"
            ).fetchone()
            reuse_allowed = cacheable and not rebuild and previous == (signature,)
            database.delete_file_ids(duplicate_ids)
            pending: list[Note] = []
            for note in valid_notes:
                stored = database.connection.execute(
                    "SELECT source_path, title, content_hash FROM notes WHERE file_id = ?",
                    (note.file_id,),
                ).fetchone()
                if reuse_allowed and stored == (
                    note.relative_path,
                    note.title,
                    note.content_hash,
                ):
                    chunk_count += database.connection.execute(
                        "SELECT COUNT(*) FROM chunks WHERE file_id = ?", (note.file_id,)
                    ).fetchone()[0]
                    processed_ids.add(note.file_id)
                    reused += 1
                    continue
                pending.append(note)
            report(
                f"Checked {len(paths)} files in {monotonic() - started:.1f}s: "
                f"notes to chunk: {len(pending)}; unchanged: {reused}."
            )
            if pending and not reuse_allowed:
                if rebuild:
                    reason = "--rebuild requested"
                elif not cacheable:
                    reason = "token counter has no cache identity"
                elif previous is None:
                    reason = "no saved chunking settings; establishing cache"
                else:
                    reason = "chunking settings changed"
                report(f"Rebuilding all note chunks: {reason}.")
            chunk_started = last_report = monotonic()
            if pending:
                report(f"Chunking notes: 0/{len(pending)} complete...")
            for position, note in enumerate(pending, start=1):
                chunks = chunk_note(
                    note,
                    max_words=max_words,
                    max_tokens=max_tokens,
                    token_counter=token_counter,
                )
                try:
                    chunk_count += database.replace_note(note, chunks)
                except sqlite3.IntegrityError as error:
                    warnings.warn(
                        f"could not insert chunks for {note.relative_path} "
                        f"(file_id {note.file_id!r}): {error}",
                        DuplicateFileIdWarning,
                    )
                    database.connection.execute(
                        "DELETE FROM notes WHERE file_id = ?", (note.file_id,)
                    )
                    continue
                processed_ids.add(note.file_id)
                rechunked += 1
                now = monotonic()
                if now - last_report >= 1 or position == len(pending):
                    report(
                        f"Chunked {position}/{len(pending)} notes "
                        f"({now - chunk_started:.1f}s elapsed)."
                    )
                    last_report = now
            database.delete_notes_not_in(processed_ids)
            report(
                f"Synchronizing keyword index for {len(processed_ids)} eligible notes..."
            )
            database.sync_keyword_notes(
                (note for note in valid_notes if note.file_id in processed_ids),
                rebuild=rebuild,
            )
            database.connection.execute(
                "INSERT OR REPLACE INTO chunk_settings(singleton, signature) VALUES (1, ?)",
                (signature if cacheable else "",),
            )
            database.connection.commit()
        except Exception:
            database.connection.rollback()
            raise

    return SyncResult(
        discovered_notes=len(paths),
        processed_notes=len(processed_ids),
        chunks=chunk_count,
        missing_file_ids=missing,
        duplicate_notes=duplicate_notes,
        read_errors=read_errors,
        rechunked_notes=rechunked,
        reused_notes=reused,
    )
