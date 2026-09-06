from __future__ import annotations

import sqlite3
import tempfile
import unittest
import warnings
from pathlib import Path

from obsidian_vault.markdown import chunk_note, extract_sections, normalize_markdown
from obsidian_vault.notes import load_note
from obsidian_vault.search import find_vault_id, obsidian_uri, prepare_results
from obsidian_vault.sync import (
    DuplicateFileIdWarning,
    MissingFileIdWarning,
    sync_vault,
)


class ChunkingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.vault = Path(self.temporary_directory.name)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def write_note(self, name: str, file_id: str | None, body: str) -> Path:
        metadata = f"---\nfile_id: {file_id}\n---\n\n" if file_id else ""
        path = self.vault / name
        path.write_text(metadata + body)
        return path

    def test_heading_hierarchy_uses_filename_as_root(self) -> None:
        path = self.write_note(
            "my_note.md",
            "note-1",
            "Introduction.\n\n## Parent\nParent text.\n\n### Child\n"
            "Child text.\n\n## Next\nNext text.",
        )
        note = load_note(path, self.vault)
        sections = extract_sections(note)
        self.assertEqual(
            [section.heading_path for section in sections],
            [
                ("my note",),
                ("my note", "Parent"),
                ("my note", "Parent", "Child"),
                ("my note", "Next"),
            ],
        )

    def test_heading_like_code_does_not_start_a_section(self) -> None:
        path = self.write_note(
            "code.md", "note-2", "## Real\n\n```python\n# Not a heading\n```"
        )
        sections = extract_sections(load_note(path, self.vault))
        self.assertEqual(len(sections), 1)
        self.assertEqual(sections[0].heading_path, ("code", "Real"))

    def test_links_images_code_and_latex_are_normalized(self) -> None:
        text = (
            "[[Target|label]] and [site](https://example.com).\n\n"
            "![[diagram.png]]\n\n$$x^2$$\n\n```python\nprint('ok')\n```"
        )
        normalized = normalize_markdown(text)
        self.assertIn("label and site", normalized)
        self.assertIn("[Image: diagram.png]", normalized)
        self.assertIn("$$x^2$$", normalized)
        self.assertIn("Code (python):", normalized)

    def test_split_chunks_have_deterministic_unique_ids(self) -> None:
        path = self.write_note(
            "long.md",
            "note-3",
            "## Repeated\nOne two three four five.\n\nSix seven eight nine ten.\n"
            "## Repeated\nEleven twelve thirteen fourteen fifteen.",
        )
        note = load_note(path, self.vault)
        first = chunk_note(note, max_words=8)
        second = chunk_note(note, max_words=8)
        self.assertGreater(len(first), 2)
        self.assertEqual(
            [chunk.chunk_id for chunk in first],
            [chunk.chunk_id for chunk in second],
        )
        self.assertEqual(len({chunk.chunk_id for chunk in first}), len(first))
        self.assertTrue(all(chunk.word_count <= 8 for chunk in first))

    def test_oversized_code_fence_stays_within_limit(self) -> None:
        path = self.write_note(
            "code.md",
            "note-4",
            "```text\n" + " ".join(f"word-{index}" for index in range(30)) + "\n```",
        )
        chunks = chunk_note(load_note(path, self.vault), max_words=10)
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(chunk.raw_markdown.startswith("```text\n") for chunk in chunks))
        self.assertTrue(all(chunk.raw_markdown.endswith("\n```") for chunk in chunks))
        self.assertTrue(all(chunk.word_count <= 10 for chunk in chunks))

    def test_token_counter_can_set_the_chunk_limit(self) -> None:
        path = self.write_note(
            "tokens.md",
            "note-5",
            "## Topic\nOne two three four.\n\nFive six seven eight.",
        )
        counter = lambda text: len(text.split()) * 2
        chunks = chunk_note(
            load_note(path, self.vault),
            max_tokens=12,
            token_counter=counter,
        )
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(chunk.token_count is not None for chunk in chunks))
        self.assertTrue(all(chunk.token_count <= 12 for chunk in chunks))

    def test_token_heavy_string_without_spaces_is_split(self) -> None:
        path = self.write_note(
            "encoded.md",
            "note-6",
            "```text\n" + ("value%2Fpath%3Fkey%3Dlong" * 20) + "\n```",
        )
        counter = lambda text: len(text)
        chunks = chunk_note(
            load_note(path, self.vault),
            max_tokens=100,
            token_counter=counter,
        )
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(chunk.token_count <= 100 for chunk in chunks))

    def test_missing_and_duplicate_file_ids_warn_and_are_skipped(self) -> None:
        self.write_note("missing.md", None, "No identifier.")
        self.write_note("one.md", "duplicate", "First note.")
        self.write_note("two.md", "duplicate", "Second note.")
        self.write_note("valid.md", "valid", "Valid note.")
        database = self.vault / "chunks.sqlite3"

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            result = sync_vault(self.vault, database)

        self.assertTrue(any(item.category is MissingFileIdWarning for item in caught))
        self.assertTrue(any(item.category is DuplicateFileIdWarning for item in caught))
        self.assertEqual(result.processed_notes, 1)
        self.assertEqual(result.duplicate_notes, 2)
        with sqlite3.connect(database) as connection:
            note_rows = connection.execute("SELECT file_id FROM notes").fetchall()
            self.assertEqual(note_rows, [("valid",)])
            rows = connection.execute("SELECT chunk_id, file_id FROM chunks").fetchall()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0][1], "valid")

    def test_obsidian_uri_encodes_path_and_heading(self) -> None:
        uri = obsidian_uri("vault id", "Folder/My note.md", "A heading & more")
        self.assertEqual(
            uri,
            "obsidian://open?vault=vault%20id&file="
            "Folder%2FMy%20note.md%23A%20heading%20%26%20more",
        )

    def test_find_vault_id_matches_the_resolved_path(self) -> None:
        config = self.vault / "obsidian.json"
        config.write_text(
            '{"vaults":{"abc123":{"path":"'
            + str(self.vault)
            + '"}}}'
        )
        self.assertEqual(find_vault_id(self.vault, config), "abc123")

    def test_root_and_heading_results_create_the_right_links(self) -> None:
        query_result = {
            "ids": [["root", "heading"]],
            "documents": [["Note\n\nRoot", "Note > Topic\n\nBody"]],
            "metadatas": [[
                {"source_path": "Note.md", "heading_path": '["Note"]'},
                {"source_path": "Note.md", "heading_path": '["Note", "Topic"]'},
            ]],
            "distances": [[0.1, 0.2]],
        }
        results = prepare_results(query_result, "vault-id")
        self.assertEqual(results[0].uri, "obsidian://open?vault=vault-id&file=Note.md")
        self.assertEqual(
            results[1].uri,
            "obsidian://open?vault=vault-id&file=Note.md%23Topic",
        )


if __name__ == "__main__":
    unittest.main()
