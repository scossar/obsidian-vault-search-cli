# obsidian-vault

A Python library and CLI for chunking Obsidian notes, generating local
embeddings, and searching a vault by meaning. The code lives separately from
your notes; pass the vault path explicitly when running commands.

## Setup

Requires Python 3.13 or later and `uv`. From this repository:

```sh
uv sync --locked
uv run obsidian-vault --help
```

The examples below assume this repository is at `~/projects/obsidian-vault`
and your notes are at `~/obsidian_vault`. Substitute your own paths.

## Index a vault

```sh
uv run --project ~/projects/obsidian-vault \
  obsidian-vault index --vault ~/obsidian_vault
```

This prepares section-based chunks in `VAULT/data/chunks.sqlite3` and
synchronizes the `obsidian-vault` collection in `VAULT/data/chroma`.
Use `--database`, `--chroma`, or `--collection` to override these defaults.
Keep generated data out of the vault's Git repository.

Notes require unique `file_id` front matter values. Notes with missing or
duplicate IDs are reported and skipped. The indexer prefixes chunks with
their filename heading and heading ancestry, then splits them to fit the
embedding model's 256-token limit without silent truncation.

Embeddings use Chroma's local `all-MiniLM-L6-v2` ONNX model. The first run
downloads the model to Chroma's user cache. Later runs reuse unchanged
chunks and embeddings, update changed notes, and remove stale index records.
Run the index command after editing notes; there is no background watcher.
Use `index --rebuild` to force re-chunking while still reusing unchanged
embeddings.

For SQLite chunks only, without embedding generation:

```sh
uv run --project ~/projects/obsidian-vault \
  obsidian-vault chunk --vault ~/obsidian_vault --max-words 380
```

`chunk` uses a word limit; `index` uses the embedding model's token limit.
If `--vault` is omitted, the CLI uses the current working directory. Specifying
`--project` selects the Python environment; it does not select the vault.

## Search

```sh
uv run --project ~/projects/obsidian-vault \
  obsidian-vault search --vault ~/obsidian_vault --results 5 \
  -- "how does convolution work?"
```

The default output shows ranked excerpts in Rich panels with Obsidian links.
Use `--plain` for plain text, `--json` for integration output, or `--open 1`
to launch the first result in Obsidian. `--json` and `--plain` are mutually
exclusive. Search uses the existing Chroma index; it does not index notes.

The CLI discovers the vault ID from Obsidian's local configuration, falling
back to the vault directory name. Use `--vault-id` to override it. Opening
results requires a working `obsidian://` URI handler; searching does not
require the Obsidian application to be running.

## Omarchy integration

The `scossar.vault-search` Omarchy plugin is maintained in a separate
repository. **Plugin repository link: to be added.** It is a normal desktop
window that remains open when you switch applications or open a result.

Its subprocess interface is:

```sh
uv run --project ~/projects/obsidian-vault \
  obsidian-vault search --vault ~/obsidian_vault --json -- "your query"
```

On success, standard output is a JSON array containing `rank` (integer),
`chunk_id` (string), `document` (string), `distance` (number), `source_path`
(string), `heading_path` (array of strings), and `uri` (string). An empty
array means no results. The plugin displays the excerpts and opens the
returned URI unchanged. Failed commands exit nonzero; the plugin displays
standard error. No server or persistent Python worker is required.

## Development

```sh
cd ~/projects/obsidian-vault
uv run --locked python -m unittest discover -s tests -v
```

The package under `src/obsidian_vault/` contains the CLI, Markdown processing,
SQLite synchronization, Chroma integration, and search presentation. Tests
use temporary vaults and databases.

`main.py` and `scripts/chunk_notes.py` are retained as legacy CLI wrappers;
both call the same command parser and require a subcommand. Prefer the
installed `obsidian-vault` entry point for new usage.

The `scripts/` directory also contains standalone note-maintenance tools:

- `find_h1_first.py` lists notes whose first Markdown heading is H1.
- `remove_first_h1.py` removes that heading. Use its `--dry-run` option to
  preview changes before running it on a vault.

Both maintenance tools accept a directory argument and otherwise default to
`~/obsidian_vault`. They are not part of indexing or search.

## Repository separation

This project was extracted from the vault's `main-copy` branch. Earlier code
history remains in the vault repository; this repository starts independently
and contains no vault notes, databases, or submodules. The Omarchy plugin is
also versioned independently, with the JSON CLI contract connecting the two.
