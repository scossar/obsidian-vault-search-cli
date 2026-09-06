from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from typing import Callable

from markdown_it import MarkdownIt
from markdown_it.token import Token

from .models import Chunk, Note, Section


_MARKDOWN = MarkdownIt("commonmark")
_WIKI_EMBED = re.compile(r"!\[\[([^]|]+)(?:\|([^]]+))?\]\]")
_WIKI_LINK = re.compile(r"(?<!!)\[\[([^]|]+)(?:\|([^]]+))?\]\]")
_IMAGE_EXTENSIONS = {".avif", ".gif", ".jpeg", ".jpg", ".png", ".svg", ".webp"}
_FENCE_OPEN = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")


def filename_title(path: Path) -> str:
    """Construct the synthetic H1 without inventing title capitalization."""
    return " ".join(path.stem.replace("_", " ").split())


def _normalize_obsidian_links(text: str) -> str:
    def embedded(match: re.Match[str]) -> str:
        target, alias = match.group(1), match.group(2)
        display = alias or target
        is_image = Path(target.split("#", 1)[0]).suffix.lower() in _IMAGE_EXTENSIONS
        kind = "Image" if is_image else "Embedded note"
        return f"[{kind}: {display}]"

    text = _WIKI_EMBED.sub(embedded, text)
    return _WIKI_LINK.sub(lambda match: match.group(2) or match.group(1), text)


def _inline_text(token: Token) -> str:
    if not token.children:
        return _normalize_obsidian_links(token.content)

    pieces: list[str] = []
    for child in token.children:
        if child.type in {"text", "code_inline"}:
            pieces.append(child.content)
        elif child.type in {"softbreak", "hardbreak"}:
            pieces.append("\n")
        elif child.type == "image":
            source = child.attrGet("src") or ""
            label = child.content or Path(source).name
            pieces.append(f"[Image: {label}]")
        elif child.type == "html_inline":
            pieces.append(re.sub(r"<[^>]+>", "", child.content))
    return _normalize_obsidian_links("".join(pieces))


def normalize_markdown(markdown: str) -> str:
    """Produce embedding text while retaining code and LaTeX notation."""
    blocks: list[str] = []
    for token in _MARKDOWN.parse(markdown):
        if token.type == "inline":
            text = _inline_text(token).strip()
            if text:
                blocks.append(text)
        elif token.type in {"fence", "code_block"}:
            language = token.info.strip() if token.type == "fence" else ""
            label = f"Code ({language}):" if language else "Code:"
            blocks.append(f"{label}\n{token.content.rstrip()}")
        elif token.type == "html_block":
            text = re.sub(r"<[^>]+>", "", token.content).strip()
            if text:
                blocks.append(text)
    return "\n\n".join(blocks).strip()


def _heading_text(inline: Token) -> str:
    return " ".join(_inline_text(inline).split())


def extract_sections(note: Note) -> list[Section]:
    """Split a note at Markdown headings and retain each heading ancestry."""
    lines = note.body.splitlines(keepends=True)
    tokens = _MARKDOWN.parse(note.body)
    headings: list[tuple[int, int, int, str]] = []
    for index, token in enumerate(tokens):
        if token.type != "heading_open" or token.map is None:
            continue
        inline = tokens[index + 1]
        level = int(token.tag[1:])
        headings.append((token.map[0], token.map[1], level, _heading_text(inline)))

    sections: list[Section] = []
    stack: list[tuple[int, str]] = []
    current_path = (note.title,)
    current_start = 0
    path_occurrences: Counter[tuple[str, ...]] = Counter()

    def append_section(end: int) -> None:
        nonlocal current_start
        raw = "".join(lines[current_start:end]).strip()
        if not normalize_markdown(raw):
            return
        occurrence = path_occurrences[current_path]
        path_occurrences[current_path] += 1
        sections.append(
            Section(
                heading_path=current_path,
                section_index=len(sections),
                heading_occurrence=occurrence,
                raw_markdown=raw,
                start_line=note.body_start_line + current_start + 1,
                end_line=note.body_start_line + end,
            )
        )

    for start, end, level, text in headings:
        append_section(start)
        while stack and stack[-1][0] >= level:
            stack.pop()
        stack.append((level, text))
        current_path = (note.title, *(heading for _, heading in stack))
        current_start = end
    append_section(len(lines))
    return sections


def _word_count(text: str) -> int:
    return len(re.findall(r"\S+", text))


def _block_units(markdown: str) -> list[str]:
    lines = markdown.splitlines(keepends=True)
    spans: list[tuple[int, int]] = []
    for token in _MARKDOWN.parse(markdown):
        if token.level != 0 or token.map is None or token.type == "heading_open":
            continue
        if token.nesting not in {0, 1}:
            continue
        span = (token.map[0], token.map[1])
        if spans and span[0] < spans[-1][1]:
            continue
        spans.append(span)
    return [
        "".join(lines[start:end]).strip()
        for start, end in spans
        if "".join(lines[start:end]).strip()
    ]


TextMeasure = Callable[[str], int]


def _split_at_whitespace(
    text: str, budget: int, measure: TextMeasure = _word_count
) -> list[str]:
    pieces = re.findall(r"\S+(?:\s+|$)", text)
    result: list[str] = []
    current: list[str] = []
    for piece in pieces:
        if measure(piece) > budget:
            if current:
                result.append("".join(current).strip())
                current = []
            result.extend(_split_at_characters(piece, budget, measure))
            continue
        if current and measure("".join(current) + piece) > budget:
            result.append("".join(current).strip())
            current = []
        current.append(piece)
    if current:
        result.append("".join(current).strip())
    return result


def _split_at_characters(text: str, budget: int, measure: TextMeasure) -> list[str]:
    """Split a single tokenizer-heavy string that contains no whitespace."""
    result: list[str] = []
    remaining = text
    while remaining:
        low, high = 1, len(remaining)
        best = 0
        while low <= high:
            middle = (low + high) // 2
            if measure(remaining[:middle]) <= budget:
                best = middle
                low = middle + 1
            else:
                high = middle - 1
        if best == 0:
            raise ValueError("chunk limit is too small for a single character")
        result.append(remaining[:best].strip())
        remaining = remaining[best:]
    return [piece for piece in result if piece]


def _split_oversized_unit(
    raw: str, budget: int, measure: TextMeasure = _word_count
) -> list[str]:
    normalized_measure = lambda text: measure(normalize_markdown(text))
    lines = raw.splitlines()
    opening = _FENCE_OPEN.match(lines[0]) if lines else None
    if opening and len(lines) > 1:
        fence_character = opening.group(1)[0]
        fence_length = len(opening.group(1))
        closing = re.compile(
            rf"^ {{0,3}}{re.escape(fence_character)}{{{fence_length},}}[ \t]*$"
        )
        if closing.match(lines[-1]):
            opener = lines[0]
            closer = lines[-1]

            def wrapped(content: str) -> str:
                return f"{opener}\n{content}\n{closer}"

            if normalized_measure(wrapped("")) >= budget:
                raise ValueError("code fence leaves no room for code in this chunk")
            content_parts = _split_at_whitespace(
                "\n".join(lines[1:-1]),
                budget,
                lambda content: normalized_measure(wrapped(content)),
            )
            return [wrapped(part) for part in content_parts]

    sentences = re.split(r"(?<=[.!?])(?=\s+)", raw)
    pieces: list[str] = []
    current = ""
    for sentence in sentences:
        if normalized_measure(sentence) > budget:
            if current.strip():
                pieces.append(current.strip())
                current = ""
            pieces.extend(_split_at_whitespace(sentence, budget, normalized_measure))
        elif current and normalized_measure(current + sentence) > budget:
            pieces.append(current.strip())
            current = sentence
        else:
            current += sentence
    if current.strip():
        pieces.append(current.strip())
    return pieces


def _chunk_id(
    file_id: str,
    heading_path: tuple[str, ...],
    heading_occurrence: int,
    part_index: int,
) -> str:
    identity = json.dumps(
        [file_id, heading_path, heading_occurrence, part_index],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(identity.encode()).hexdigest()


def chunk_section(
    note: Note,
    section: Section,
    max_words: int = 380,
    *,
    max_tokens: int | None = None,
    token_counter: TextMeasure | None = None,
) -> list[Chunk]:
    prefix = " > ".join(section.heading_path)
    if (max_tokens is None) != (token_counter is None):
        raise ValueError("max_tokens and token_counter must be provided together")
    measure = token_counter or _word_count
    limit = max_tokens if max_tokens is not None else max_words
    document_measure = lambda body: measure(f"{prefix}\n\n{body}")
    if document_measure("") >= limit:
        unit = "tokens" if token_counter else "words"
        raise ValueError(f"heading path exceeds the {limit}-{unit} chunk limit: {prefix}")

    units: list[str] = []
    for unit in _block_units(section.raw_markdown):
        normalized = normalize_markdown(unit)
        if document_measure(normalized) <= limit:
            units.append(unit)
        else:
            units.extend(_split_oversized_unit(unit, limit, document_measure))

    groups: list[list[str]] = []
    current: list[str] = []
    for unit in units:
        candidate = "\n\n".join([*current, unit])
        if current and document_measure(normalize_markdown(candidate)) > limit:
            groups.append(current)
            current = []
        current.append(unit)
    if current:
        groups.append(current)

    chunks: list[Chunk] = []
    for part_index, group in enumerate(groups):
        raw = "\n\n".join(group)
        body = normalize_markdown(raw)
        embedding_text = f"{prefix}\n\n{body}"
        token_count = token_counter(embedding_text) if token_counter else None
        if measure(embedding_text) > limit:
            raise ValueError(f"failed to split chunk below {limit}: {prefix}")
        chunks.append(
            Chunk(
                chunk_id=_chunk_id(
                    note.file_id,
                    section.heading_path,
                    section.heading_occurrence,
                    part_index,
                ),
                file_id=note.file_id,
                heading_path=section.heading_path,
                section_index=section.section_index,
                heading_occurrence=section.heading_occurrence,
                part_index=part_index,
                raw_markdown=raw,
                embedding_text=embedding_text,
                content_hash=hashlib.sha256(embedding_text.encode()).hexdigest(),
                word_count=_word_count(embedding_text),
                token_count=token_count,
                start_line=section.start_line,
                end_line=section.end_line,
            )
        )
    return chunks


def chunk_note(
    note: Note,
    max_words: int = 380,
    *,
    max_tokens: int | None = None,
    token_counter: TextMeasure | None = None,
) -> list[Chunk]:
    return [
        chunk
        for section in extract_sections(note)
        for chunk in chunk_section(
            note,
            section,
            max_words=max_words,
            max_tokens=max_tokens,
            token_counter=token_counter,
        )
    ]
