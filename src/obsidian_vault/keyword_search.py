"""Read-only, ranked full-note keyword search using the existing FTS5 index."""
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
import re
import sqlite3

from .search import obsidian_uri


@dataclass(frozen=True)
class KeywordResult:
    rank: int
    file_id: str
    source_path: str
    heading_path: tuple[str, ...]
    document: str
    score: float
    uri: str


def search_keywords(database: Path, query: str, vault_reference: str,
                    limit: int = 20, *, fts: bool = False) -> list[KeywordResult]:
    if limit < 1:
        raise ValueError('--results must be greater than zero')
    if not query.strip():
        raise ValueError('Enter at least one keyword.')
    if fts:
        expression = query
    else:
        # Treat ordinary input as words, not executable FTS query syntax.
        words = list(dict.fromkeys(re.findall(r'\w+', query, re.UNICODE)))
        if not words:
            raise ValueError('Enter at least one keyword containing letters or numbers.')
        expression = ' OR '.join('"' + word + '"' for word in words)
    database = database.expanduser().resolve()
    if not database.is_file():
        raise ValueError('Keyword index not found. Run obsidian-vault index --vault VAULT first.')
    with closing(sqlite3.connect(database.as_uri() + '?mode=ro', uri=True)) as conn:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'notes_fts'").fetchone():
            raise ValueError('Keyword index not found. Run obsidian-vault index --vault VAULT first.')
        try:
            rows = conn.execute('''
                SELECT file_id, source_path, title,
                       snippet(notes_fts, 4, '', '', ' … ', 48),
                       bm25(notes_fts, 0, 0, 0, 5, 1) AS score
                FROM notes_fts WHERE notes_fts MATCH ?
                ORDER BY score, source_path COLLATE NOCASE, source_path LIMIT ?
            ''', (expression, limit)).fetchall()
        except sqlite3.OperationalError as error:
            raise ValueError(f'Keyword search failed: {error}') from error
    return [KeywordResult(rank, file_id, path, (title,), document, score,
                          obsidian_uri(vault_reference, path))
            for rank, (file_id, path, title, document, score) in enumerate(rows, 1)]
