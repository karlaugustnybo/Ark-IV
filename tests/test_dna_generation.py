from __future__ import annotations

import contextlib
import csv
import io
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pyarrow.parquet as pq
from litestar import Litestar
from litestar.testing import TestClient

from ark_pipeline.builders.dna import build_generation, calculate, current_lineage_ids
from ark_pipeline.cli.dna_prepare import build
from ark_pipeline.dna_evidence import DnaRule
from ark_pipeline.runtime.provenance import sha256
from backend.dna_analysis import completed_dna_scenario, dna_evidence_scenario, load_dna_generation
from backend.spatial_analysis import checked_artifact, checked_outputs
from tests import test_refresh_crosswalk


class DnaGenerationTests(unittest.TestCase):
    def test_sparse_evidence_fields_survive_publication_and_unsafe_sources_stay_unknown(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / 'input'
            source.write_text('synthetic')
            evidence = [dict(taxon_id='1', genomic_transfer_safe=True),
                dict(taxon_id='2', genomic_transfer_safe=True, sample_acquired='voucher'),
                dict(taxon_id='3', genomic_transfer_safe=False, sample_acquired='unsafe')]
            species = [dict(iucn_sis_id=str(i), taxon_id=str(i), genomic_transfer_safe=True) for i in range(1, 4)]
            result = build_generation(evidence, species, root / 'dna',
                sources={'fixture': dict(path=str(source), sha256=sha256(source))})
            generation = Path(result['generation'])
            restored = calculate(pq.read_table(generation / 'evidence.parquet').to_pylist(),
                pq.read_table(generation / 'species.parquet').to_pylist(), DnaRule(), generation.name)
            self.assertEqual(restored['rows'], pq.read_table(generation / 'default.parquet').to_pylist())
            self.assertTrue(restored['rows'][1]['qualifies'])
            self.assertIsNone(restored['rows'][2]['qualifies'])

    def test_tree_independent_registered_inputs_scenarios_and_safe_lineages(self):
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(io.StringIO()):
            root = Path(tmp)
            test_refresh_crosswalk.fixture(root)
            goat = root / 'sources/tol_species_all_ranks.tsv'
            with goat.open() as handle:
                reader = csv.DictReader(handle, delimiter='\t')
                fields, rows = reader.fieldnames, list(reader)
            # One assessed species has sample evidence. An unassessed relative
            # qualifies only under the assembly rule and seeds family/genus.
            for row in rows:
                if row['taxon_id'] == '10':
                    row['sample_acquired'] = 'yes'
                if row['taxon_id'] == '16':
                    row['assembly_level'] = 'Scaffold'
            with goat.open('w') as handle:
                writer = csv.DictWriter(handle, fieldnames=fields, delimiter='\t')
                writer.writeheader()
                writer.writerows(rows)
            test_refresh_crosswalk.register(root)
            crosswalk = test_refresh_crosswalk.RefreshCrosswalkTests().run_matcher(root)
            result = build(root, crosswalk, root / 'dna')
            self.assertEqual(result['status'], 'built')
            self.assertEqual(build(root, crosswalk, root / 'dna')['status'], 'reused')
            generation = Path(result['generation'])
            settings = SimpleNamespace(dna_generation_path=generation)
            with patch('backend.dna_analysis.RequestSettings', return_value=settings), TestClient(
                Litestar([dna_evidence_scenario]), raise_server_exceptions=False
            ) as client:
                default = client.get('/api/analysis/dna').json()
                self.assertEqual(default['total'], 6)
                own = next(r for r in default['rows'] if r['iucn_sis_id'] == '101')
                self.assertTrue(own['qualifies'])
                self.assertTrue(own['family_represented'])
                unsafe = next(r for r in default['rows'] if r['iucn_sis_id'] == '104')
                self.assertEqual(unsafe['identity_status'], 'unresolved_taxonomy')
                self.assertIsNone(unsafe['qualifies'])
                self.assertIsNone(unsafe['genus_represented'])
                changed = client.get('/api/analysis/dna?rule=any-assembly&species_id=102&per_page=1').json()
                self.assertEqual(changed['qualifying_records'], 1)
                self.assertTrue(changed['rows'][0]['genus_represented'])
                self.assertNotEqual(changed['scenario_id'], default['scenario_id'])
                self.assertEqual(client.get('/api/analysis/dna?expected_scenario=stale').status_code, 409)
                self.assertEqual(client.get('/api/analysis/dna?min_busco=30').status_code, 400)
                settings.dna_generation_path = None
                self.assertEqual(client.get('/api/analysis/dna').status_code, 503)
            default_disk = pq.read_table(generation / 'default.parquet').to_pylist()
            self.assertEqual(default_disk, default['rows'])
            for cache in (completed_dna_scenario, load_dna_generation, checked_outputs, checked_artifact):
                cache.cache_clear()

    def test_duplicate_own_identity_cannot_transfer_but_checked_relatives_seed_lineage(self):
        evidence = [dict(taxon_id='1', genomic_transfer_safe=True, sample_acquired='yes',
                         kingdom_taxid='k', family_taxid='f', genus_taxid='g')] * 2
        species = [dict(iucn_sis_id='a', taxon_id='1', genomic_transfer_safe=True,
                        kingdom_taxid='k', family_taxid='f', genus_taxid='g')]
        result = calculate(evidence, species, DnaRule(), 'fixture')
        self.assertIsNone(result['rows'][0]['qualifies'])
        self.assertTrue(result['rows'][0]['genus_represented'])
        self.assertEqual(result['qualifying_records'], 2)

    def test_lineage_missing_nodes_and_cycles(self):
        with tempfile.TemporaryDirectory() as tmp:
            nodes = Path(tmp) / 'nodes.dmp'
            nodes.write_text('1 | 1 | no rank |\n2 | 1 | kingdom |\n3 | 2 | family |\n4 | 3 | genus |\n5 | 4 | species |\n6 | 99 | species |\n')
            rows = current_lineage_ids(nodes, {'5', '6', '99'})
            self.assertEqual(rows['5']['genus_taxid'], '4')
            self.assertNotIn('family_taxid', rows['6'])
            self.assertFalse(rows['99']['current_species'])
            nodes.write_text('1 | 2 | family |\n2 | 1 | genus |\n')
            with self.assertRaisesRegex(ValueError, 'Cycle'):
                current_lineage_ids(nodes, {'1'})


if __name__ == '__main__':
    unittest.main()
