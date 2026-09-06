from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import Mock, patch

from obsidian_vault.chroma_index import ChromaIndex, DefaultEmbeddingModel
from obsidian_vault.sync import DuplicateFileIdWarning, sync_vault


class IncrementalIndexTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.vault = Path(self.temp.name)
        self.database = self.vault / 'chunks.sqlite3'
        self.note = self.vault / 'Note.md'
        self.note.write_text('---\nfile_id: first\n---\nHello world.\n')

    def sync(self, **kwargs):
        return sync_vault(self.vault, self.database, **kwargs)

    def test_unchanged_notes_skip_chunking_and_database_writes(self):
        first = self.sync()
        with patch('obsidian_vault.sync.chunk_note', side_effect=AssertionError('rechunked')), patch('obsidian_vault.database.ChunkDatabase.replace_note', side_effect=AssertionError('rewritten')):
            second = self.sync()
        self.assertEqual(second.chunks, first.chunks)
        self.assertEqual(second.reused_notes, 1)
        self.assertEqual(second.rechunked_notes, 0)

    def test_missing_vault_does_not_clear_existing_chunks(self):
        self.sync()
        with self.assertRaises(NotADirectoryError):
            sync_vault(self.vault / 'missing', self.database)
        self.assertEqual(self.sync().reused_notes, 1)

    def test_add_edit_delete_and_move(self):
        self.sync()
        other = self.vault / 'Other.md'
        other.write_text('---\nfile_id: second\n---\nAnother note.\n')
        result = self.sync()
        self.assertEqual((result.rechunked_notes, result.reused_notes), (1, 1))
        self.note.write_text(self.note.read_text() + 'Edited.\n')
        result = self.sync()
        self.assertEqual((result.rechunked_notes, result.reused_notes), (1, 1))
        folder = self.vault / 'folder'
        folder.mkdir()
        self.note.rename(folder / self.note.name)
        other.unlink()
        result = self.sync()
        self.assertEqual((result.rechunked_notes, result.reused_notes), (1, 0))
        with closing(sqlite3.connect(self.database)) as connection:
            self.assertEqual(connection.execute('SELECT source_path FROM notes').fetchall(), [('folder/Note.md',)])
            self.assertEqual(connection.execute('SELECT DISTINCT file_id FROM chunks').fetchall(), [('first',)])

    def test_settings_changes_and_explicit_rebuild_invalidate_cache(self):
        self.sync()
        self.assertEqual(self.sync(max_words=20).rechunked_notes, 1)
        self.assertEqual(self.sync(max_words=20).reused_notes, 1)
        self.assertEqual(self.sync(max_words=20, rebuild=True).rechunked_notes, 1)
        options = dict(max_tokens=20, token_counter=lambda text: len(text.split()), token_counter_id='test-v1')
        self.assertEqual(self.sync(**options).rechunked_notes, 1)
        self.assertEqual(self.sync(**options).reused_notes, 1)
        options['token_counter_id'] = 'test-v2'
        self.assertEqual(self.sync(**options).rechunked_notes, 1)
        del options['token_counter_id']
        self.assertEqual(self.sync(**options).rechunked_notes, 1)
        self.assertEqual(self.sync(**options).rechunked_notes, 1)

    def test_existing_database_without_signature_rebuilds_once(self):
        self.sync()
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute('DROP TABLE chunk_settings')
            connection.commit()
        self.assertEqual(self.sync().rechunked_notes, 1)
        self.assertEqual(self.sync().reused_notes, 1)

    def test_new_duplicate_invalidates_cached_note(self):
        self.sync()
        (self.vault / 'Duplicate.md').write_text(self.note.read_text())
        with self.assertWarns(DuplicateFileIdWarning):
            result = self.sync()
        self.assertEqual(result.chunks, 0)
        self.assertEqual(result.processed_notes, 0)

    def test_failed_rebuild_preserves_previous_cache(self):
        first = self.sync()
        with patch('obsidian_vault.sync.chunk_note', side_effect=RuntimeError('failed')):
            with self.assertRaises(RuntimeError):
                self.sync(max_words=20)
        second = self.sync()
        self.assertEqual(second.reused_notes, 1)
        self.assertEqual(second.chunks, first.chunks)

    def test_cached_tokenizer_does_not_initialize_onnx(self):
        with patch('obsidian_vault.chroma_index.Tokenizer') as tokenizer, patch('obsidian_vault.chroma_index.Path.is_file', return_value=True), patch('obsidian_vault.chroma_index.ONNXMiniLM_L6_V2') as onnx:
            model = DefaultEmbeddingModel()
            tokenizer.from_file.return_value.encode.return_value.ids = [1, 2, 3]
            self.assertEqual(model.count_tokens('hello'), 3)
            model.count_tokens('again')
            tokenizer.from_file.assert_called_once()
            onnx.assert_not_called()

    def test_chroma_reuses_embeddings_and_updates_moved_note_path(self):
        self.sync(max_tokens=256, token_counter=lambda text: len(text.split()))
        model = Mock()
        model.embed.side_effect = lambda documents: [[1.0, 0.0, 0.0] for _ in documents]
        model.embedding_function = None
        index = ChromaIndex(self.vault / 'chroma', 'test-notes', model)
        first = index.sync(self.database)
        self.assertEqual(first.upserted, 1)
        model.embed.reset_mock()
        self.assertEqual(index.sync(self.database).unchanged, 1)
        folder = self.vault / 'folder'
        folder.mkdir()
        self.note.rename(folder / self.note.name)
        self.sync(max_tokens=256, token_counter=lambda text: len(text.split()))
        self.assertEqual(index.sync(self.database).upserted, 0)
        self.assertEqual(index.collection.get()['metadatas'][0]['source_path'], 'folder/Note.md')
        model.embed.assert_not_called()
        (folder / self.note.name).unlink()
        self.sync()
        self.assertEqual(index.sync(self.database).deleted, 1)


if __name__ == '__main__':
    unittest.main()
