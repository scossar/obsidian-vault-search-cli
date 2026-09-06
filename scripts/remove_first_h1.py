#!/usr/bin/env python3
"""Remove the first #-style heading line when it is H1.

Searches ~/obsidian_vault recursively by default. Preserves surrounding blank
lines and all other content. Uses find_h1_first.py's detection rules, ignoring
frontmatter, fenced code, and comments. Skips symbolic links.

Each run removes at most one heading per file. If the next heading is also H1,
a subsequent run will remove that heading too.
"""

import argparse
import os
from pathlib import Path
import sys

from find_h1_first import HEADING, first_heading


def remove_first_h1(data: bytes) -> bytes:
    """Remove the heading line, preserving the UTF-8 BOM and other line endings."""
    bom = b"\xef\xbb\xbf" if data.startswith(b"\xef\xbb\xbf") else b""
    text = data[len(bom):].decode("utf-8")
    heading = first_heading(text)
    if heading is None or heading[1] != 1:
        return data

    line_index, _ = heading
    lines = text.splitlines(keepends=True)
    match = HEADING.match(lines[line_index])
    if match is None or len(match[1]) != 1:
        raise ValueError("detected heading is not a standalone H1; skipping file")
    del lines[line_index]
    return bom + "".join(lines).encode("utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", nargs="?", default="~/obsidian_vault",
                        help="directory to search (default: ~/obsidian_vault)")
    parser.add_argument("--dry-run", action="store_true",
                        help="list affected files without modifying them")
    args = parser.parse_args()
    root = Path(args.directory).expanduser().resolve()
    if not root.is_dir():
        parser.error(f"not a directory: {root}")

    errors = []
    changed = 0
    for directory, subdirs, filenames in os.walk(root, onerror=errors.append):
        subdirs[:] = sorted(name for name in subdirs
                           if name not in {".git", ".venv", "node_modules"})
        for name in sorted(filenames):
            path = Path(directory) / name
            if path.suffix.lower() != ".md" or path.is_symlink():
                continue
            try:
                original = path.read_bytes()
                updated = remove_first_h1(original)
                if updated == original:
                    continue
                if not args.dry_run:
                    path.write_bytes(updated)
                print(path)
                changed += 1
            except (OSError, UnicodeError, ValueError) as error:
                errors.append(f"{path}: {error}")

    action = "Would update" if args.dry_run else "Updated"
    print(f"{action} {changed} file(s).", file=sys.stderr)
    for error in errors:
        print(f"Warning: {error}", file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
