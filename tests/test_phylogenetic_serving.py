from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

HAS_ENGINE = importlib.util.find_spec("dated_complete_tree") is not None
if HAS_ENGINE:
    from ete4 import Tree
from litestar import Litestar
from litestar.testing import TestClient

from ark_pipeline.builders.phylogenetic import build_generation, load_generation
from ark_pipeline.dna_evidence import DnaRule
from ark_pipeline.runtime.provenance import sha256
from backend.analysis import analytical_engine, phylogenetic_scenario


def generation(root):
    source = root / 'input.txt'
    source.write_text('synthetic evidence generation')
    tree = Tree('((a:1,b:1):10,(c:3,d:3):4):0;', parser=1)
    evidence = [dict(taxon_id='unassessed', tree_tip='c', genomic_transfer_safe=True, assembly_level='Scaffold')]
    species = [dict(iucn_sis_id=str(i), tree_tip=tip, tree_identity_verified=True,
                    genomic_transfer_safe=True, redlist_category='EN') for i, tip in enumerate('abcd')]
    options = dict(sources={'fixture': {'path': str(source), 'sha256': sha256(source)}},
                   scope='Mammalia', root='synthetic-mammal-crown', risk_samples=2)
    result = build_generation(tree, evidence, species, root / 'scores', **options)
    return result, (tree, evidence, species, options)


@unittest.skipUnless(HAS_ENGINE, "Optional engine: uv sync --group edge")
class PhylogeneticServingTests(unittest.TestCase):
    def test_unknown_lineage_baseline_warning_survives_api_projection(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _, (tree, evidence, species, options) = generation(root)
            evidence.append(dict(taxon_id='unresolved', genomic_transfer_safe=False, sample_acquired='voucher'))
            result = build_generation(tree, evidence, species, root / 'scores', **options)
            settings = SimpleNamespace(phylogenetic_generation_path=Path(result['generation']))
            analytical_engine.cache_clear()
            try:
                with patch('backend.analysis.RequestSettings', return_value=settings), TestClient(Litestar([phylogenetic_scenario])) as client:
                    response = client.get('/api/analysis/phylogenetic')
                    self.assertEqual(response.status_code, 200, response.text)
                    self.assertEqual(response.json()['unresolved_positive_records'], 1)
                    self.assertTrue(response.json()['unresolved_without_genus'])
            finally:
                analytical_engine.cache_clear()

    def test_interruption_keeps_previous_publication_and_resumes(self):
        import pyarrow.parquet as pq
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result, (tree, evidence, species, options) = generation(root)
            previous = (root / 'scores/current').resolve()
            write = pq.write_table
            def interrupted(table, path, **kwargs):
                if Path(path).stem == 'evidence':
                    raise RuntimeError('interrupted')
                return write(table, path, **kwargs)
            with patch('ark_pipeline.builders.phylogenetic.pq.write_table', side_effect=interrupted):
                with self.assertRaisesRegex(RuntimeError, 'interrupted'):
                    build_generation(tree, evidence, species, root / 'scores', **{**options, 'seed': 99})
            self.assertEqual((root / 'scores/current').resolve(), previous)
            resumed = build_generation(tree, evidence, species, root / 'scores', **{**options, 'seed': 99})
            self.assertEqual(resumed['status'], 'built')
            self.assertNotEqual((root / 'scores/current').resolve(), previous)
            self.assertTrue(Path(result['generation']).is_dir())

    def test_cached_default_dynamic_agreement_atomic_reuse_and_source_invalidation(self):
        import pyarrow.parquet as pq
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result, (tree, evidence, species, options) = generation(root)
            path = Path(result['generation'])
            engine, scores = load_generation(path)
            self.assertEqual(engine.calculate(DnaRule(), nearest=True)['rows'], pq.read_table(path / 'default_gaps.parquet').to_pylist())
            self.assertAlmostEqual(sum(r['ed_myr'] for r in scores.values()), 22)
            self.assertEqual(build_generation(tree, evidence, species, root / 'scores', **options)['status'], 'reused')
            (root / 'input.txt').write_text('changed')
            with self.assertRaisesRegex(ValueError, 'source changed'):
                build_generation(tree, evidence, species, root / 'scores', **options)
            with (path / 'evidence.parquet').open('ab') as handle:
                handle.write(b'corrupt')
            with self.assertRaisesRegex(ValueError, 'corrupt'):
                load_generation(path)

    def test_pagination_missing_generation_parameter_validation_and_scenario_guard(self):
        with tempfile.TemporaryDirectory() as tmp:
            result, _ = generation(Path(tmp))
            settings = SimpleNamespace(phylogenetic_generation_path=Path(result['generation']))
            analytical_engine.cache_clear()
            with patch('backend.analysis.RequestSettings', return_value=settings), TestClient(Litestar([phylogenetic_scenario]), raise_server_exceptions=False) as client:
                response = client.get('/api/analysis/phylogenetic?rule=any-assembly&per_page=1&nearest=true')
                self.assertEqual(response.status_code, 200, response.text)
                body = response.json()
                self.assertEqual((body['total'], len(body['rows'])), (4, 1))
                self.assertEqual(body['rows'][0]['gap_myr'], 11)
                self.assertEqual(body['rows'][0]['priority'], 33)
                self.assertEqual(body['rows'][0]['nearest_tip'], 'c')
                self.assertEqual(client.get('/api/analysis/phylogenetic?expected_scenario=stale').status_code, 409)
                for query in ('page=0', 'per_page=101', 'rule=unknown', 'min_busco=50', 'en=nan', 'en=inf', 'en=-1'):
                    self.assertEqual(client.get('/api/analysis/phylogenetic?' + query).status_code, 400, query)
                settings.phylogenetic_generation_path = None
                self.assertEqual(client.get('/api/analysis/phylogenetic').status_code, 503)
            analytical_engine.cache_clear()


if __name__ == '__main__':
    unittest.main()
