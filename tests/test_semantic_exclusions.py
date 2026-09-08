import contextlib
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock, patch

from obsidian_vault.chroma_index import ChromaIndex
from obsidian_vault.cli import main
from obsidian_vault.keyword_search import matching_note_ids
from obsidian_vault.sync import sync_vault


class SemanticExclusionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.vault = Path(self.temp.name)
        self.database = self.vault / 'custom.sqlite3'

    def note(self, title, file_id, body):
        (self.vault / (title + '.md')).write_text(
            f'---\nfile_id: {file_id}\n---\n{body}\n')

    def test_matching_ids_are_unlimited_and_use_plain_keyword_rules(self):
        for i in range(60):
            self.note(f'Note {i}', f'n{i}', 'cLaUdE')
        self.note('ChatGPT', 'title', 'Writing tools')
        self.note('Other', 'other', 'claudette')
        self.note('Operators', 'operator', 'NOT')
        sync_vault(self.vault, self.database)
        self.assertEqual(set(matching_note_ids(self.database, 'Claude ChatGPT')),
                         {f'n{i}' for i in range(60)} | {'title'})
        self.assertEqual(matching_note_ids(self.database, 'NOT'), ['operator'])
        self.assertEqual(matching_note_ids(self.database, 'absent'), [])

    def test_missing_or_legacy_index_and_invalid_input(self):
        with self.assertRaisesRegex(ValueError, 'Keyword index not found'):
            matching_note_ids(self.database, 'Claude')
        self.assertFalse(self.database.exists())
        with contextlib.closing(sqlite3.connect(self.database)):
            pass
        for query in ('', '   ', '!?'):
            with self.subTest(query=query), self.assertRaisesRegex(ValueError, 'at least one keyword'):
                matching_note_ids(self.database, query)
        with self.assertRaisesRegex(ValueError, 'Keyword index not found'):
            matching_note_ids(self.database, 'Claude')

    def test_cli_fails_before_loading_model_when_filter_is_unavailable(self):
        for words in ('Claude', ''):
            with (
                self.subTest(words=words),
                patch('sys.argv', ['obsidian-vault', 'search', '--vault', str(self.vault),
                                   '--exclude-keywords', words, '--', 'writing']),
                patch('obsidian_vault.cli.DefaultEmbeddingModel') as model,
                self.assertRaisesRegex(SystemExit, '--exclude-keywords:'),
            ):
                main()
            model.assert_not_called()

    def test_real_chroma_filters_whole_notes_before_selecting_results(self):
        self.note('Mention elsewhere', 'body',
                  '# Writing\nAI assisted writing application\n\n# Provider\ncLaUdE')
        self.note('Claude', 'title', 'AI assisted writing application')
        self.note('Eligible A', 'a', 'eligible writing application')
        self.note('Eligible B', 'b', 'eligible writing application')
        sync_vault(self.vault, self.database)
        model = Mock()
        model.embedding_function = None
        model.embed.side_effect = lambda documents: [
            [0.8, 0.2, 0.0] if 'eligible' in text else [1.0, 0.0, 0.0]
            for text in documents
        ]
        index = ChromaIndex(self.vault / 'data/chroma', 'obsidian-vault', model)
        index.sync(self.database)
        blocked = index.collection.get(where={'file_id': 'body'})
        self.assertGreaterEqual(len(blocked['ids']), 2)
        self.assertTrue(any('claude' not in text.lower() for text in blocked['documents']))

        def search(exclusion=None):
            args = ['obsidian-vault', 'search', '--vault', str(self.vault),
                    '--database', str(self.database), '--vault-id', 'test-vault',
                    '--results', '2', '--json']
            if exclusion is not None:
                args.extend(['--exclude-keywords', exclusion])
            args.extend(['--', 'An AI assisted writing application'])
            output = io.StringIO()
            with patch('sys.argv', args), patch('obsidian_vault.cli.DefaultEmbeddingModel', return_value=model), contextlib.redirect_stdout(output):
                self.assertEqual(main(), 0)
            return json.loads(output.getvalue())

        baseline = search()
        self.assertTrue(all(r['source_path'] in {'Mention elsewhere.md', 'Claude.md'} for r in baseline))
        filtered = search('Claude')
        self.assertEqual({r['source_path'] for r in filtered}, {'Eligible A.md', 'Eligible B.md'})
        self.assertEqual([r['rank'] for r in filtered], [1, 2])
        self.assertEqual(search('absent'), baseline)
        self.assertEqual(search('Claude eligible'), [])
        with patch('obsidian_vault.cli.matching_note_ids', side_effect=AssertionError('SQLite accessed')):
            self.assertEqual(search(), baseline)
