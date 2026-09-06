import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from obsidian_vault.cli import main
from obsidian_vault.keyword_search import search_keywords
from obsidian_vault.sync import sync_vault


class KeywordSearchTests(unittest.TestCase):
    def test_keyword_cli_and_fts_queries(self):
        with tempfile.TemporaryDirectory() as directory:
            vault = Path(directory)
            database = vault / 'data/chunks.sqlite3'
            for index, (name, body) in enumerate([
                ('relu', 'relu activation function'),
                ('short & café', 'relu function'),
                ('irrelevant', 'gardening tomatoes'),
            ]):
                (vault / (name + '.md')).write_text(f'---\nfile_id: {index}\n---\n{body}\n')
            sync_vault(vault, database)
            rows = search_keywords(database, 'relu activation function', 'vault')
            self.assertEqual({r.source_path for r in rows}, {'relu.md', 'short & café.md'})
            self.assertEqual(rows[0].source_path, 'relu.md')
            self.assertEqual(len(search_keywords(database, 'NEAR(relu function, 5)', 'vault', fts=True)), 2)
            self.assertEqual(len(search_keywords(database, '"relu function"', 'vault', fts=True)), 1)
            self.assertEqual(search_keywords(database, 'absent', 'vault'), [])
            self.assertEqual(len(search_keywords(database, 'relu', 'vault', limit=1)), 1)
            with self.assertRaises(ValueError):
                search_keywords(database, '"', 'vault', fts=True)
            with self.assertRaises(ValueError):
                search_keywords(database, ' ', 'vault')
            output = io.StringIO()
            with patch('sys.argv', ['obsidian-vault', 'search-keywords', '--vault', str(vault), '--vault-id', 'vault', '--json', '--', 'relu']), contextlib.redirect_stdout(output), patch('obsidian_vault.cli.DefaultEmbeddingModel', side_effect=AssertionError('embedding model loaded')):
                self.assertEqual(main(), 0)
            result = json.loads(output.getvalue())
            self.assertEqual(len(result), 2)
            self.assertIn('score', result[0])
            self.assertIn('%26', result[1]['uri'])

    def test_missing_index_is_not_created(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / 'missing.sqlite3'
            with self.assertRaisesRegex(ValueError, 'Run obsidian-vault index'):
                search_keywords(database, 'relu', 'vault')
            self.assertFalse(database.exists())
