import sqlite3
import tempfile
import unittest
import warnings
from contextlib import closing
from pathlib import Path
from unittest.mock import patch
from obsidian_vault.sync import sync_vault


class KeywordIndexTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.vault = Path(self.temp.name)
        self.db = self.vault / 'data/chunks.sqlite3'
        self.note = self.vault / 'Relu.md'
        self.note.write_text('---\nfile_id: stable\nsecret: metadataonly\n---\nrelu activation function\n')

    def sync(self):
        with warnings.catch_warnings():
            warnings.simplefilter('ignore')
            return sync_vault(self.vault, self.db)

    def rows(self, query):
        with closing(sqlite3.connect(self.db)) as conn:
            return conn.execute('SELECT file_id, source_path FROM notes_fts WHERE notes_fts MATCH ?', (query,)).fetchall()

    def test_edit_rename_delete(self):
        self.sync()
        self.assertEqual(self.rows('relu AND function'), [('stable', 'Relu.md')])
        self.assertEqual(self.rows('metadataonly'), [])
        self.note.write_text('---\nfile_id: stable\n---\nsigmoid function\n')
        renamed = self.vault / 'Sigmoid.md'
        self.note.rename(renamed)
        self.sync()
        self.assertEqual(self.rows('relu'), [])
        self.assertEqual(self.rows('sigmoid'), [('stable', 'Sigmoid.md')])
        renamed.unlink()
        self.sync()
        self.assertEqual(self.rows('sigmoid'), [])

    def test_backfill_without_rechunking_and_no_unchanged_rewrite(self):
        self.sync()
        with closing(sqlite3.connect(self.db)) as conn:
            conn.execute('DROP TABLE notes_fts')
            conn.commit()
        with patch('obsidian_vault.sync.chunk_note', side_effect=AssertionError('rechunked')):
            self.assertEqual(self.sync().reused_notes, 1)
        self.assertEqual(len(self.rows('function')), 1)
        with closing(sqlite3.connect(self.db)) as conn:
            conn.execute("CREATE TRIGGER prevent_fts_rewrite BEFORE DELETE ON notes_fts_content BEGIN SELECT RAISE(ABORT, 'unexpected rewrite'); END")
            conn.commit()
        self.assertEqual(self.sync().reused_notes, 1)

    def test_duplicate_and_removed_ids(self):
        self.sync()
        other = self.vault / 'Other.md'
        other.write_text('---\nfile_id: stable\n---\nrelu\n')
        self.sync()
        self.assertEqual(self.rows('relu'), [])
        other.unlink()
        self.sync()
        self.assertEqual(len(self.rows('relu')), 1)
        self.note.write_text('relu without frontmatter')
        self.sync()
        self.assertEqual(self.rows('relu'), [])

    def test_failure_rolls_back_chunk_changes(self):
        self.sync()
        self.note.write_text('---\nfile_id: stable\n---\nchanged\n')
        with patch('obsidian_vault.database.ChunkDatabase.sync_keyword_notes', side_effect=RuntimeError('failed')):
            with self.assertRaises(RuntimeError):
                self.sync()
        self.assertEqual(len(self.rows('function')), 1)
        with closing(sqlite3.connect(self.db)) as conn:
            self.assertIn('activation', conn.execute('SELECT raw_markdown FROM chunks').fetchone()[0])
