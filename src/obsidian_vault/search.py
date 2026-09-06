from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode

from rich.console import Console, Group
from rich.panel import Panel
from rich.style import Style
from rich.text import Text


@dataclass(frozen=True)
class SearchResult:
    rank: int
    chunk_id: str
    document: str
    distance: float
    source_path: str
    heading_path: tuple[str, ...]
    uri: str


def find_vault_id(vault: Path, config_path: Path | None = None) -> str | None:
    """Return Obsidian's stable vault ID for a local vault path."""
    if config_path is None:
        config_root = Path(
            os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")
        )
        config_path = config_root / "obsidian" / "obsidian.json"
    try:
        configuration = json.loads(config_path.read_text())
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None

    resolved_vault = vault.expanduser().resolve()
    for vault_id, details in configuration.get("vaults", {}).items():
        try:
            configured_path = Path(details["path"]).expanduser().resolve()
        except (KeyError, TypeError):
            continue
        if configured_path == resolved_vault:
            return vault_id
    return None


def obsidian_uri(vault: str, source_path: str, heading: str | None = None) -> str:
    file_target = source_path
    if heading:
        file_target = f"{file_target}#{heading}"
    query = urlencode(
        {"vault": vault, "file": file_target},
        quote_via=quote,
        safe="",
    )
    return f"obsidian://open?{query}"


def prepare_results(
    query_result: dict[str, Any],
    vault_reference: str,
) -> list[SearchResult]:
    ids = query_result["ids"][0]
    documents = (query_result["documents"] or [[]])[0]
    metadatas = (query_result["metadatas"] or [[]])[0]
    distances = (query_result["distances"] or [[]])[0]
    results: list[SearchResult] = []
    for rank, (chunk_id, document, metadata, distance) in enumerate(
        zip(ids, documents, metadatas, distances), start=1
    ):
        raw_heading_path = metadata.get("heading_path", "[]")
        try:
            heading_path = tuple(json.loads(raw_heading_path))
        except (TypeError, ValueError, json.JSONDecodeError):
            heading_path = ()
        source_path = str(metadata["source_path"])
        heading = heading_path[-1] if len(heading_path) > 1 else None
        results.append(
            SearchResult(
                rank=rank,
                chunk_id=chunk_id,
                document=document,
                distance=float(distance),
                source_path=source_path,
                heading_path=heading_path,
                uri=obsidian_uri(vault_reference, source_path, heading),
            )
        )
    return results


def render_results(results: list[SearchResult], console: Console | None = None) -> None:
    console = console or Console(highlight=False)
    if not results:
        console.print("No results found.", style="yellow")
        return

    link_style = Style(color="bright_cyan", underline=True)
    for result in results:
        heading = " › ".join(result.heading_path) or result.source_path
        title = Text(f"{result.rank}. {heading}", style="bold cyan")
        _, separator, body = result.document.partition("\n\n")
        document = body if separator else result.document
        source = Text()
        source.append(result.source_path, style="dim")
        source.append(f"   distance {result.distance:.4f}", style="dim")
        source.append("   ")
        source.append("Open", style=link_style + Style(link=result.uri))
        console.print(
            Panel(
                Group(Text(document), Text(), source),
                title=title,
                title_align="left",
                border_style="blue",
                padding=(1, 2),
            )
        )
    console.print(
        "Open a link with [bold]Ctrl+click[/bold] in Ghostty or "
        "[bold]Ctrl+Shift+O[/bold] in Foot. You can also rerun the search "
        "with [bold]--open N[/bold].",
        style="dim",
    )


def render_plain_results(results: list[SearchResult]) -> None:
    for result in results:
        heading = " > ".join(result.heading_path) or result.source_path
        print(f"\n{result.rank}. {heading}")
        print(f"{result.distance:.6f}  {result.source_path}  {result.chunk_id}")
        print(result.document)
        print(result.uri)


def open_result(result: SearchResult) -> None:
    executable = shutil.which("xdg-open")
    if executable is None:
        raise RuntimeError("xdg-open is not installed")
    subprocess.Popen(
        [executable, result.uri],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
