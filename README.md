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
maintains a full-note SQLite FTS5 keyword index (`notes_fts`) in that same
SQLite database, and synchronizes the `obsidian-vault` collection in
`VAULT/data/chroma`. This one command updates all three representations.
The keyword index stores each eligible note's filename-derived title and full
Markdown body (excluding frontmatter), independently of chunk boundaries.
Existing databases gain the keyword index on their next indexing run, including
unchanged notes, without requiring `--rebuild` or new embeddings.
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

For SQLite chunks and the keyword index, without embedding generation:

```sh
uv run --project ~/projects/obsidian-vault \
  obsidian-vault chunk --vault ~/obsidian_vault --max-words 380
```

`chunk` uses a word limit; `index` uses the embedding model's token limit.
If `--vault` is omitted, the CLI uses the current working directory. Specifying
`--project` selects the Python environment; it does not select the vault.

## Which notes are indexed?

**Every indexed note must have a nonempty, unique `file_id` property in YAML
frontmatter.** For example:

```markdown
---
file_id: 54a7d801-b7fc-4f16-8907-15e4fbb33d89
---

Your note content goes here.
```

The ID lets the index recognize a note after it is renamed or moved. Assign
it once and keep it unchanged when editing, renaming, or moving the note.
When duplicating a note, give the copy a new ID.

A UUID is a useful convention, but the library does not require UUID syntax.
Add IDs using your preferred note-editing workflow, with a different value for
each note you want to index. The indexer reads your notes; it does not generate
IDs, add or change properties, or modify your Markdown files.

- Missing or empty IDs: warn and skip the note.
- Duplicate IDs: warn and skip **all** notes sharing that ID.
- Unreadable or invalid notes: warn and skip them.
- Previously indexed notes that are deleted or become ineligible are removed
  from the SQLite indexes; a successful `index` run also removes their Chroma records.

These rules apply to both keyword and semantic indexing. The scanner considers
`.md` and `.markdown` files, skipping hidden directories and generated directories
such as `data`, `node_modules`, and `.venv`.

The panel's filename and literal ripgrep searches read saved files directly;
they do not require a `file_id`. Indexed keyword search must be refreshed with
`index` (or `chunk` for SQLite only), just as semantic search must be refreshed.
Use `search-keywords` for FTS5 queries; `search` continues to use Chroma.

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

### Exclude keywords from semantic results

Use `--exclude-keywords` to exclude whole notes whose indexed title or body
contains any of the supplied words, while ranking the remaining passages by
semantic similarity:

```sh
uv run --project ~/projects/obsidian-vault \
  obsidian-vault search --vault ~/obsidian_vault \
  --exclude-keywords 'Claude' \
  -- 'An AI assisted writing application'
```

For example, `--exclude-keywords 'Claude ChatGPT'` excludes notes mentioning
either word. Matching uses the same case-insensitive, token-based rules as
ordinary keyword search. Input is split into words; FTS operators and phrase
syntax are not interpreted. Empty or punctuation-only input is an error.
Frontmatter and folder paths are not searched. A mention anywhere in the
indexed title or body excludes every passage from that note.

SQLite finds all matching note IDs, and Chroma applies the exclusion before
selecting the requested number of semantic results. Semantic scores and the
JSON result format are unchanged. If no notes match the exclusion, search
proceeds normally; if every candidate is excluded, results are empty.

This option requires the keyword index at `VAULT/data/chunks.sqlite3`; use
`--database PATH` for a custom location. A missing keyword index produces an
error. Run `index` after editing notes to keep SQLite and Chroma synchronized;
exclusions reflect indexed content. Searches without `--exclude-keywords` do
not require SQLite. No schema change or re-embedding is needed for existing
synchronized indexes. This option is currently available through the CLI.

## Keyword search (FTS5)

After running `index`, search remembered words without needing the exact phrase:

```sh
uv run --project ~/projects/obsidian-vault \
  obsidian-vault search-keywords --vault ~/obsidian_vault \
  --results 10 -- "relu activation function"
```

Ordinary input is split into words and matched with OR: any word may match,
so missing or intervening words are allowed. FTS5 BM25 ranks results, weighting
filename titles five times as strongly as body text. This is word matching,
not substring search, typo correction, or semantic matching. The Unicode
tokenizer ignores case and, by default, Latin diacritics; no stemming is used.
More matched words do not guarantee a higher rank. Each result represents one
note and includes a body excerpt and an Obsidian link.

To use operators, quoted phrases, or proximity syntax, pass `--fts`:

```sh
uv run --project ~/projects/obsidian-vault \
  obsidian-vault search-keywords --vault ~/obsidian_vault --fts \
  -- 'relu OR activation OR function'
uv run --project ~/projects/obsidian-vault \
  obsidian-vault search-keywords --vault ~/obsidian_vault --fts \
  -- 'NEAR(relu function, 5)'
```

With `--fts`, the library accepts
[SQLite FTS5 query syntax](https://www.sqlite.org/fts5.html#full_text_query_syntax):

| Syntax | Example | Meaning |
| --- | --- | --- |
| `AND` | `relu AND sigmoid` | Both match. |
| `OR` | `relu OR sigmoid` | Either matches. |
| `NOT` | `relu NOT sigmoid` | Include relu; exclude sigmoid. |
| Whitespace | `relu sigmoid` | Implicit AND. |
| Parentheses | `relu NOT (sigmoid OR softmax)` | Group expressions. |
| `"…"`, `+` | `"activation function"`, `activation + function` | Consecutive tokens, in order. |
| `*` | `activ*`, `"activation func"*` | Final token is a prefix; keep `*` outside quotes. |
| `^` | `title:^relu` | Phrase starts at the column's first token; unavailable inside NEAR. |
| `NEAR(…, N)` | `NEAR(relu function, 5)` | Either order, at most N intervening tokens; N defaults to 10. |
| `:` and `{…}` | `title:relu`, `{title body}:relu`, `body:(relu OR sigmoid)` | Restrict search to columns. |
| `-` before a column filter | `-title:relu`, `-{title}:relu` | Search other columns. |

Use uppercase `AND`, `OR`, `NOT`, and `NEAR`. Standalone `NOT sigmoid`
is invalid. Precedence: implicit AND, NOT, AND, OR; use explicit operators
beside parentheses. Quote punctuation-containing terms; escape embedded
double quotes by doubling them. Phrases match tokens, not literal punctuation.
Only `title` and `body` are indexed; paths and IDs are not searchable fields.

Without `--fts`, words such as `OR` are ordinary search words. Invalid FTS
syntax produces an error. Search opens SQLite read-only and never refreshes
or creates an index; rerun `index` after changing notes. No model, embedding
server, or running Obsidian instance is required.

Options include `--database`, `--vault-id`, `--results` (default 20), `--open N`,
and `--json`. JSON is an array with `rank`, `file_id`, `source_path`,
`heading_path`, `document` (plain-text excerpt), `excerpt_html` (escaped excerpt
with FTS matches underlined and line breaks preserved), `score` (FTS5 BM25; lower is
better), and `uri`. Empty results produce `[]`. Scores are not probabilities
and are not comparable to semantic distances.

This command is also available through the Omarchy panel's **Keywords** mode.
Enable **FTS syntax** there to use these operators. **Text** mode uses literal
ripgrep search.

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

`main.py` is retained as a legacy CLI wrapper;
it calls the same command parser and requires a subcommand. Prefer the
installed `obsidian-vault` entry point for new usage.

## Repository separation

This project was extracted from the vault's `main-copy` branch. Earlier code
history remains in the vault repository; this repository starts independently
and contains no vault notes, databases, or submodules. The Omarchy plugin is
also versioned independently, with the JSON CLI contract connecting the two.
