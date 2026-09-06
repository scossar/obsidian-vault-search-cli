#!/usr/bin/env python3
"""List Markdown files whose first ATX (#-style) heading has level one.

Usage: python3 find_h1_first.py [directory]
Defaults to ~/obsidian_vault. Searches recursively without modifying files.
Ignores YAML frontmatter, fenced code blocks, and HTML/Obsidian comments.
Only standalone #-style headings are considered (not Setext underlines).
"""

import argparse
import os
from pathlib import Path
import re
import sys


HEADING = re.compile(r"^ {0,3}(#{1,6})(?:[ \t]+|$)")
FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
COMMENTS = re.compile(r"<!--.*?-->|%%.*?%%|<!--.*\Z|%%.*\Z", re.DOTALL)


def first_heading(text: str) -> tuple[int, int] | None:
    """Return the zero-based line index and level of the first #-style heading."""
    lines = text.lstrip("\ufeff").splitlines()
    line_offset = 0
    if lines and lines[0].strip() == "---":
        for index, line in enumerate(lines[1:], start=1):
            if line.strip() in {"---", "..."}:
                lines = lines[index + 1:]
                line_offset = index + 1
                break
        else:
            return None

    # Remove code before comments so literal comment markers in code are harmless.
    visible = []
    fence_char = None
    fence_length = 0
    for line in lines:
        if fence_char is not None:
            if re.fullmatch(r" {0,3}" + re.escape(fence_char)
                            + "{" + str(fence_length) + r",}[ \t]*", line):
                fence_char = None
            visible.append("")
            continue
        fence = FENCE.match(line)
        if fence and not (fence[1][0] == "`" and "`" in fence[2]):
            fence_char = fence[1][0]
            fence_length = len(fence[1])
            visible.append("")
        else:
            visible.append(line)

    text = COMMENTS.sub(lambda match: "\n" * match[0].count("\n"),
                        "\n".join(visible))
    for index, line in enumerate(text.splitlines()):
        heading = HEADING.match(line)
        if heading:
            return line_offset + index, len(heading[1])
    return None


def first_heading_level(text: str) -> int | None:
    heading = first_heading(text)
    return heading[1] if heading else None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", nargs="?", default="~/obsidian_vault",
                        help="directory to search (default: ~/obsidian_vault)")
    args = parser.parse_args()
    root = Path(args.directory).expanduser().resolve()
    if not root.is_dir():
        parser.error(f"not a directory: {root}")

    errors = []
    matches = []
    for directory, subdirs, filenames in os.walk(root, onerror=errors.append):
        subdirs[:] = [name for name in subdirs
                      if name not in {".git", ".venv", "node_modules"}]
        for name in filenames:
            if Path(name).suffix.lower() != ".md":
                continue
            path = Path(directory) / name
            try:
                if first_heading_level(path.read_text(encoding="utf-8-sig")) == 1:
                    matches.append(path)
            except (OSError, UnicodeError) as error:
                errors.append(f"{path}: {error}")

    for path in sorted(matches):
        print(path)
    for error in errors:
        print(f"Warning: {error}", file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
