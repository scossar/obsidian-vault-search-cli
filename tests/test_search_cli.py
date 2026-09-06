from __future__ import annotations

import contextlib
import io
import json
import unittest
from unittest.mock import patch

from obsidian_vault.cli import main


class SearchJsonTests(unittest.TestCase):
    def run_search(self, result):
        output = io.StringIO()
        with (
            patch('sys.argv', ['obsidian-vault', 'search', '--json', '--', '-quoted "query"; $(literal)']),
            patch('obsidian_vault.cli.DefaultEmbeddingModel'),
            patch('obsidian_vault.cli.query_collection', return_value=result) as query,
            patch('obsidian_vault.cli.find_vault_id', return_value='vault-id'),
            contextlib.redirect_stdout(output),
        ):
            self.assertEqual(main(), 0)
            self.assertEqual(query.call_args.args[2], '-quoted "query"; $(literal)')
        return json.loads(output.getvalue())

    def test_json_preserves_text_headings_and_openable_uri(self):
        rows = self.run_search({
            'ids': [['chunk-1']],
            'documents': [['Heading\n\nA "quote", café, and a newline.\nNext line.']],
            'metadatas': [[{'source_path': 'a & b.md', 'heading_path': '["Title", "A heading"]'}]],
            'distances': [[0.125]],
        })
        self.assertEqual(rows[0]['document'], 'Heading\n\nA "quote", café, and a newline.\nNext line.')
        self.assertEqual(rows[0]['heading_path'], ['Title', 'A heading'])
        self.assertEqual(rows[0]['uri'], 'obsidian://open?vault=vault-id&file=a%20%26%20b.md%23A%20heading')
        self.assertEqual(rows[0]['rank'], 1)
        self.assertEqual(rows[0]['distance'], 0.125)

    def test_empty_results_are_an_array(self):
        self.assertEqual(self.run_search({
            'ids': [[]], 'documents': [[]], 'metadatas': [[]], 'distances': [[]],
        }), [])
