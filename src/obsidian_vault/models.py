from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Note:
    file_id: str
    path: Path
    relative_path: str
    title: str
    body: str
    body_start_line: int
    content_hash: str


@dataclass(frozen=True)
class Section:
    heading_path: tuple[str, ...]
    section_index: int
    heading_occurrence: int
    raw_markdown: str
    start_line: int
    end_line: int


@dataclass(frozen=True)
class Chunk:
    chunk_id: str
    file_id: str
    heading_path: tuple[str, ...]
    section_index: int
    heading_occurrence: int
    part_index: int
    raw_markdown: str
    embedding_text: str
    content_hash: str
    word_count: int
    token_count: int | None
    start_line: int
    end_line: int
