"""SQL import verifies artifact receipts without constructing a full scenario engine."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pyarrow as pa
import pyarrow.parquet as pq

from ark_pipeline.aggregation.species_lists import signature, write_receipt
from ark_pipeline.builders.phylogenetic import calculation_code_identity
from backend.load_postgres import checked_phylogenetic_scores


class PhylogeneticImportTests(unittest.TestCase):
    def test_verified_scores_without_engine_and_rejects_corruption_or_code_drift(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            scores = root / 'species_phylogenetic.parquet'
            table = pa.table({'iucn_sis_id': ['1'], 'ed_myr': [2.0]})
            pq.write_table(table, scores)
            identity = {'code': calculation_code_identity()}
            write_receipt(root / 'receipt.json', identity, {'species_phylogenetic': scores},
                          {'schemas': {'species_phylogenetic': signature(table.schema)}})
            with patch('ark_pipeline.builders.phylogenetic.load_generation', side_effect=AssertionError('No scenario engine')):
                self.assertEqual(checked_phylogenetic_scores(root), scores.resolve())
            saved = json.loads((root / 'receipt.json').read_text())
            (root / 'receipt.json').write_text(json.dumps({**saved, 'identity': {'code': 'old'}}))
            with self.assertRaisesRegex(ValueError, 'Stale phylogenetic calculation'):
                checked_phylogenetic_scores(root)
            (root / 'receipt.json').write_text(json.dumps(saved))
            scores.write_bytes(b'corrupt')
            with self.assertRaisesRegex(ValueError, 'Stale/corrupt'):
                checked_phylogenetic_scores(root)
