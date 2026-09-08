from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path
from time import monotonic

from .chroma_index import ChromaIndex, DefaultEmbeddingModel, query_collection
from .search import (
    find_vault_id,
    open_result,
    prepare_results,
    render_plain_results,
    render_results,
)
from .sync import sync_vault
from .keyword_search import matching_note_ids, search_keywords


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="obsidian-vault",
        description="Prepare Obsidian notes for text embeddings.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    chunk = subparsers.add_parser("chunk", help="write heading-aware chunks to SQLite")
    chunk.add_argument("--vault", type=Path, default=Path.cwd(), help="vault directory")
    chunk.add_argument(
        "--database",
        type=Path,
        help="SQLite database path (default: VAULT/data/chunks.sqlite3)",
    )
    chunk.add_argument(
        "--max-words",
        type=int,
        default=380,
        help="maximum words per chunk, including its heading path",
    )
    index = subparsers.add_parser(
        "index", help="synchronize chunks, keyword index, and Chroma embeddings"
    )
    index.add_argument("--vault", type=Path, default=Path.cwd(), help="vault directory")
    index.add_argument("--database", type=Path, help="SQLite database path")
    index.add_argument("--chroma", type=Path, help="Chroma database directory")
    index.add_argument("--collection", default="obsidian-vault")
    index.add_argument("--batch-size", type=int, default=128)
    index.add_argument("--rebuild", action="store_true", help="rebuild SQLite chunks and keyword index")
    search = subparsers.add_parser("search", help="query the Chroma collection")
    search.add_argument("query")
    search.add_argument("--vault", type=Path, default=Path.cwd(), help="vault directory")
    search.add_argument("--chroma", type=Path, help="Chroma database directory")
    search.add_argument("--collection", default="obsidian-vault")
    search.add_argument("--results", type=int, default=5)
    search.add_argument("--database", type=Path, help="keyword SQLite database (default: VAULT/data/chunks.sqlite3)")
    search.add_argument(
        "--exclude-keywords",
        metavar="WORDS",
        help="exclude whole notes containing any supplied word in their indexed title or body",
    )
    search.add_argument(
        "--vault-id",
        help="Obsidian vault ID or name (default: discover from Obsidian config)",
    )
    search.add_argument(
        "--open",
        type=int,
        metavar="RANK",
        help="open the numbered result in Obsidian",
    )
    output = search.add_mutually_exclusive_group()
    output.add_argument("--plain", action="store_true", help="disable Rich panels")
    output.add_argument("--json", action="store_true", help="emit a JSON array of search results")
    keywords = subparsers.add_parser("search-keywords", help="rank notes using the SQLite FTS5 index")
    keywords.add_argument("query")
    keywords.add_argument("--vault", type=Path, default=Path.cwd())
    keywords.add_argument("--database", type=Path, help="default: VAULT/data/chunks.sqlite3")
    keywords.add_argument("--results", type=int, default=20)
    keywords.add_argument("--vault-id", help="Obsidian vault ID or name")
    keywords.add_argument("--json", action="store_true", help="emit a JSON result array")
    keywords.add_argument("--fts", action="store_true", help="interpret query as FTS5 syntax (OR, AND, NEAR, quoted phrases)")
    keywords.add_argument("--open", type=int, metavar="RANK", help="open a numbered result in Obsidian")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.command == "search-keywords":
        vault = args.vault.expanduser().resolve()
        reference = args.vault_id or find_vault_id(vault) or vault.name
        try:
            results = search_keywords(args.database or vault / "data/chunks.sqlite3",
                                      args.query, reference, args.results, fts=args.fts)
        except (ValueError, OSError) as error:
            raise SystemExit(str(error)) from error
        if args.json:
            print(json.dumps([asdict(result) for result in results], ensure_ascii=False))
        else:
            for result in results:
                print(f"\n{result.rank}. {result.heading_path[0]}")
                print(result.source_path)
                print(result.document)
                print(result.uri)
            if not results:
                print("No results found.")
        if args.open is not None:
            if not 1 <= args.open <= len(results):
                raise SystemExit(f"--open must be between 1 and {len(results)} for this query")
            open_result(results[args.open - 1])
        return 0
    if args.command == "chunk":
        if args.max_words < 1:
            raise SystemExit("--max-words must be greater than zero")
        database = args.database or args.vault / "data/chunks.sqlite3"
        result = sync_vault(args.vault, database, max_words=args.max_words)
        print(
            f"Processed {result.processed_notes}/{result.discovered_notes} notes; "
            f"wrote {result.chunks} chunks to {database}."
        )
        if result.missing_file_ids or result.duplicate_notes or result.read_errors:
            print(
                f"Skipped: {result.missing_file_ids} missing file_id, "
                f"{result.duplicate_notes} duplicate-ID notes, "
                f"{result.read_errors} read errors."
            )
        return 0
    if args.command == "index":
        if args.batch_size < 1:
            raise SystemExit("--batch-size must be greater than zero")
        started = monotonic()
        args.vault = args.vault.expanduser().resolve()
        database = args.database or args.vault / "data/chunks.sqlite3"
        chroma_path = args.chroma or args.vault / "data/chroma"
        model = DefaultEmbeddingModel()
        print(f"Vault: {args.vault}", flush=True)
        print(f"Chunk database: {database.expanduser().resolve()}", flush=True)
        chunks = sync_vault(
            args.vault,
            database,
            max_tokens=model.max_tokens,
            token_counter=model.count_tokens,
            token_counter_id=model.token_counter_id,
            rebuild=args.rebuild,
            progress=lambda message: print(message, flush=True),
        )
        print(
            f"Chunk database: {chunks.chunks} total chunks across "
            f"{chunks.processed_notes} notes; "
            f"notes re-chunked: {chunks.rechunked_notes}, reused: {chunks.reused_notes}.",
            flush=True,
        )
        print(
            f"Synchronizing Chroma collection {args.collection!r} "
            f"at {chroma_path.expanduser().resolve()}...",
            flush=True,
        )
        result = ChromaIndex(chroma_path, args.collection, model).sync(
            database, batch_size=args.batch_size
        )
        print(
            f"Chroma collection {args.collection!r}: {result.total_chunks} total, "
            f"{result.upserted} embedded, {result.unchanged} unchanged, "
            f"{result.deleted} stale records deleted."
        )
        print(f"Index complete in {monotonic() - started:.1f}s.", flush=True)
        return 0
    if args.command == "search":
        if args.results < 1:
            raise SystemExit("--results must be greater than zero")
        args.vault = args.vault.expanduser().resolve()
        where = None
        if args.exclude_keywords is not None:
            try:
                excluded_ids = matching_note_ids(
                    args.database or args.vault / "data/chunks.sqlite3",
                    args.exclude_keywords,
                )
            except (ValueError, OSError) as error:
                raise SystemExit(f'--exclude-keywords: {error}') from error
            if excluded_ids:
                where = {"file_id": {"$nin": excluded_ids}}
        chroma_path = args.chroma or args.vault / "data/chroma"
        model = DefaultEmbeddingModel()
        model.prepare()
        query_result = query_collection(
            chroma_path,
            args.collection,
            args.query,
            args.results,
            model,
            where=where,
        )
        vault_reference = args.vault_id or find_vault_id(args.vault) or args.vault.name
        results = prepare_results(query_result, vault_reference)
        if args.json:
            print(json.dumps([asdict(result) for result in results], ensure_ascii=False))
        elif args.plain:
            render_plain_results(results)
        else:
            render_results(results)
        if args.open is not None:
            if not 1 <= args.open <= len(results):
                raise SystemExit(
                    f"--open must be between 1 and {len(results)} for this query"
                )
            open_result(results[args.open - 1])
        return 0
    raise AssertionError(f"unknown command: {args.command}")
