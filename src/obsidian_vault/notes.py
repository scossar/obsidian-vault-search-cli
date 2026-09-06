from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import frontmatter

from .markdown import filename_title
from .models import Note


def _body_and_start_line(text: str) -> tuple[str, int]:
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        return text, 0
    for index, line in enumerate(lines[1:], start=1):
        if line.strip() in {"---", "..."}:
            return "".join(lines[index + 1 :]), index + 1
    return text, 0


def load_note(path: Path, vault: Path) -> Note:
    raw = path.read_bytes()
    text = raw.decode("utf-8-sig")
    parsed = frontmatter.loads(text)
    metadata: dict[str, Any] = dict(parsed.metadata)
    raw_file_id = metadata.get("file_id")
    file_id = str(raw_file_id).strip() if raw_file_id is not None else ""
    if not file_id:
        raise ValueError("missing file_id front matter")
    body, body_start_line = _body_and_start_line(text)
    return Note(
        file_id=file_id,
        path=path,
        relative_path=path.relative_to(vault).as_posix(),
        title=filename_title(path),
        body=body,
        body_start_line=body_start_line,
        content_hash=hashlib.sha256(raw).hexdigest(),
    )
