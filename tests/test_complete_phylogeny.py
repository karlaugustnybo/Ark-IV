from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

HAS_ENGINE = importlib.util.find_spec('dated_complete_tree') is not None
if HAS_ENGINE:
    from ete4 import Tree

from ark_pipeline.builders.phylogenetic import build_generation, load_generation
from ark_pipeline.cli.phylogenetic_global import attach_identities, validate_link_crosswalk
from ark_pipeline.compact_phylogeny import read_complete_tree, score_complete_tree
from ark_pipeline.dna_evidence import DnaRule
from ark_pipeline.edge import score_clade
from ark_pipeline.phylogenetic_scenarios import CompactTree
from ark_pipeline.runtime.provenance import sha256


@unittest.skipUnless(HAS_ENGINE, 'Optional engine: uv sync --group edge')
class CompletePhylogenyTests(unittest.TestCase):
    def test_global_bridges_reconcile_identity_across_reviewed_crosswalk_generations(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            crosswalk = root / 'crosswalk.parquet'
            original = dict(iucn_sis_id=1, iucn_assessment_id=10, iucn_scientific_name='Alpha beta')
            pq.write_table(pa.Table.from_pylist([original]), root / 'species_tree_links.parquet')
            # Genomic review changes are permitted; assessment identity is not.
            pq.write_table(pa.Table.from_pylist([{**original, 'genomic_permission': False}]), crosswalk)
            validate_link_crosswalk(root, crosswalk)
            for change in ({'iucn_assessment_id': 11}, {'iucn_scientific_name': 'Alpha gamma'}):
                pq.write_table(pa.Table.from_pylist([{**original, **change}]), crosswalk)
                with self.assertRaisesRegex(ValueError, 'assessment/name identities'):
                    validate_link_crosswalk(root, crosswalk)
            pq.write_table(pa.Table.from_pylist([original, original]), crosswalk)
            with self.assertRaisesRegex(ValueError, 'unique nonnull SIS'):
                validate_link_crosswalk(root, crosswalk)

    def test_release_parser_retains_topology_lengths_and_ambiguous_occurrences(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / 'tree.tre'
            source.write_text('((a_ott1:1,b_ott2:2)i:3,(a_ott1:4,c_ott3:5)j:6)life_ott0:99;')
            tree, audit = read_complete_tree(source)
            self.assertEqual(audit['ambiguous_occurrences'], 2)
            self.assertEqual(audit['root_label'], 'life_ott0')
            np.testing.assert_array_equal(tree.parent, [-1, 0, 1, 1, 0, 4, 4])
            np.testing.assert_array_equal(tree.length, [0, 3, 1, 2, 6, 4, 5])
            self.assertNotIn('a_ott1', tree.tip_labels)
            self.assertEqual(len(set(tree.tip_labels)), 4)
            self.assertAlmostEqual(sum(tree.fair_proportion()), 21)
            for bad in ('(a_ott1:1,b_ott2:-1);', '(a_ott1:1,b_ott2:NaN);', '(a_ott1:1,,b_ott2:2);', '((a_ott1:1,b_ott2:2):2;', '(a_ott1,b_ott2:1);'):
                source.write_text(bad)
                with self.assertRaises(ValueError, msg=bad):
                    read_complete_tree(source)

    def test_compact_scores_match_pinned_engine_with_unknown_extinct_polytomy_and_unary_nodes(self):
        cases = ['((a:1,b:2):3,(c:4,d:5,e:0):2):0;', '(((a:1):2,b:3):4,(c:2,d:3):5):0;']
        for newick in cases:
            for extinct in (False, True):
                tree = Tree(newick, parser=1)
                categories = dict(a='EN', b='DD', c='EW', d='EX' if extinct else 'LC')
                rows = [dict(tree_tip=label, tree_identity_verified=True, join_status='joined', redlist_category=category)
                        for label, category in categories.items()]
                expected, _ = score_clade(tree.copy(), rows, 8, 42, stable_risk_order=True)
                actual, diagnostics = score_complete_tree(CompactTree.from_tree(tree), rows, 8, 42)
                by_tip = {r['tree_tip']: r for r in expected}
                for row in actual:
                    for key, value in row.items():
                        if key != 'tree_tip':
                            self.assertAlmostEqual(value, by_tip[row['tree_tip']][key], places=10, msg=key)
                self.assertAlmostEqual(diagnostics['sum_ed_myr'], diagnostics['clade_pd_myr'])

    def test_global_publication_reuse_and_non_mammal_scenario(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / 'tree.tre'
            source.write_text('((mammal_ott1:1,mammal_ott2:1):10,(plant_ott3:2,plant_ott4:2):4)life_ott0:0;')
            tree, _ = read_complete_tree(source)
            evidence = [dict(taxon_id='plant-relative', tree_tip='plant_ott4', genomic_transfer_safe=True, assembly_level='Scaffold')]
            species = [dict(iucn_sis_id=str(i), tree_tip=label, tree_identity_verified=True,
                            genomic_transfer_safe=True, redlist_category='EN') for i, label in enumerate(tree.tip_labels)]
            options = dict(sources={'tree': {'path': str(source), 'sha256': sha256(source)}},
                           scope='all-acquired-clades', root='life_ott0', risk_samples=2)
            built = build_generation(tree, evidence, species, root / 'out', **options)
            engine, scores = load_generation(Path(built['generation']))
            rows = {r['tree_tip']: r for r in engine.rank(DnaRule('any-assembly'), {'EN': 3})['rows']}
            self.assertEqual(rows['plant_ott3']['gap_myr'], 2)
            self.assertEqual(rows['mammal_ott1']['gap_myr'], 11)
            self.assertGreater(scores['2']['ed_myr'], 0)
            self.assertEqual(build_generation(tree, evidence, species, root / 'out', **options)['status'], 'reused')

    def test_identity_bridges_preserve_unsafe_and_duplicate_records(self):
        evidence = [dict(taxon_id='direct', genomic_transfer_safe=True),
                    dict(taxon_id='bridge', genomic_transfer_safe=True),
                    dict(taxon_id='unsafe', genomic_transfer_safe=False),
                    dict(taxon_id='duplicate', genomic_transfer_safe=False),
                    dict(taxon_id='duplicate', genomic_transfer_safe=False)]
        targets = [dict(iucn_sis_id=str(i), taxon_id=taxid, genomic_transfer_safe=True)
                   for i, taxid in enumerate(('direct', 'bridge', 'unsafe', 'duplicate'))]
        links = [dict(iucn_sis_id=str(i), tree_tip=f't{i}', tree_identity_verified=True, redlist_category='EN') for i in range(4)]
        evidence, targets = attach_identities(evidence, targets, links, {'direct': {'t0'}}, set(), [], [])
        self.assertEqual(evidence[0]['tree_tip'], 't0')
        self.assertEqual(evidence[1]['tree_tip'], 't1')
        self.assertFalse(targets[2]['genomic_transfer_safe'])
        self.assertFalse(targets[3]['genomic_transfer_safe'])
        self.assertEqual(len(evidence), 5)


if __name__ == '__main__':
    unittest.main()
