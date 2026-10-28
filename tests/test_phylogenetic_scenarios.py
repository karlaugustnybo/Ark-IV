from __future__ import annotations

import itertools
import unittest

import duckdb
import numpy as np

from ark_pipeline.dna_evidence import (
    DnaRule,
    evaluate_evidence,
    lineage_representation,
    website_sql,
)
from ark_pipeline.phylogenetic_scenarios import CompactTree, ScenarioEngine
from ark_pipeline.robustness import compare_scenarios


def tree():
    return CompactTree(np.array([-1, 0, 1, 1, 0, 4, 4]),
                       np.array([0., 10, 1, 1, 4, 3, 3]), np.array([2, 3, 5, 6]), tuple('abcd'))


def pd_union(t, selected):
    branches = set()
    for label, node in zip(t.tip_labels, t.tip_nodes):
        if label in selected:
            while node:
                branches.add(node)
                node = t.parent[node]
    return sum(t.length[n] for n in branches)


class CompactGapTests(unittest.TestCase):
    def test_every_baseline_against_independent_branch_union_and_monotonicity(self):
        t = tree()
        for count in range(5):
            for selected in itertools.combinations('abcd', count):
                baseline = set(selected)
                expected = [pd_union(t, baseline | {s}) - pd_union(t, baseline) for s in 'abcd']
                np.testing.assert_allclose(t.gaps(baseline), expected)
                for s in 'abcd':
                    self.assertTrue(np.all(t.gaps(baseline | {s}) <= t.gaps(baseline)))
        self.assertAlmostEqual(sum(t.fair_proportion()), sum(t.length))

    def test_nearest_ties_zero_branches_and_polytomy(self):
        t = CompactTree(np.array([-1, 0, 0, 0]), np.array([0., 0, 2, 2]), np.array([1, 2, 3]), ('a', 'b', 'c'))
        self.assertEqual(t.nearest({'b', 'c'}), [(2., 'b'), (0., 'b'), (0., 'c')])
        self.assertEqual(t.nearest(set()), [None] * 3)
        np.testing.assert_array_equal(t.gaps({'b', 'c'}), [0, 0, 0])
        with self.assertRaises(ValueError):
            t.gaps({'unknown'})
        with self.assertRaises(ValueError):
            CompactTree(np.array([-1, 2, 1]), np.zeros(3), np.array([1, 2]), ('a', 'b'))


class EvidenceScenarioTests(unittest.TestCase):
    def test_website_compatibility_and_quality_proxy_are_distinct(self):
        inputs = [{}, {'sample_acquired': ' '}, {'sample_acquired': 'false'},
                  {'assembly_level': 'Chromosome', 'busco_completeness': '101'},
                  {'assembly_level': ' Chromosome', 'busco_completeness': '95'},
                  {'assembly_level': 'Scaffold', 'resampling_required': 'DTOL'}]
        self.assertEqual([evaluate_evidence(r, DnaRule()).qualifies for r in inputs], [None, True, True, True, False, False])
        self.assertFalse(evaluate_evidence(inputs[3], DnaRule('taxon-quality-proxy')).qualifies)
        self.assertTrue(evaluate_evidence(inputs[-1], DnaRule('any-assembly')).qualifies)
        for value in ('NaN', 'Infinity'):
            row = {'assembly_level': 'Chromosome', 'busco_completeness': value}
            self.assertTrue(evaluate_evidence(row, DnaRule()).qualifies)
            self.assertFalse(evaluate_evidence(row, DnaRule('taxon-quality-proxy')).qualifies)
            inputs.append(row)
        with duckdb.connect() as c:
            for row in inputs:
                fields = ['sample_acquired', 'in_progress', 'ebp_standard_criteria', 'assembly_level', 'busco_completeness']
                sql = ','.join(f'?::VARCHAR AS {k}' for k in fields)
                actual = c.execute(f'SELECT {website_sql()} FROM (SELECT {sql})', [row.get(k) for k in fields]).fetchone()[0]
                self.assertEqual(actual, evaluate_evidence(row, DnaRule()).qualifies is True)
        for params in ({'version': 2}, {'min_busco': 20}, {'name': 'taxon-quality-proxy', 'min_busco': float('nan')}):
            with self.assertRaises(ValueError):
                DnaRule(**params)

    def engine(self):
        evidence = [dict(taxon_id='unassessed', tree_tip='c', genomic_transfer_safe=True, assembly_level='Scaffold', genus_taxid='g2'),
                    dict(taxon_id='unresolved', genomic_transfer_safe=False, sample_acquired='yes', genus_taxid='g1')]
        species = [dict(iucn_sis_id=str(i), tree_tip=s, tree_identity_verified=True,
                        genomic_transfer_safe=s != 'd', genus_taxid='g1' if s in 'ab' else 'g2', redlist_category='EN')
                   for i, s in enumerate('abcd')]
        return ScenarioEngine(tree(), evidence, species, dict(tree='t', root='r', scope='mammals', units='Myr', stem='excluded', evidence='e', identities='i'), max_scenarios=1)

    def test_rule_dependent_withholding_unassessed_relatives_and_filter_invariance(self):
        e = self.engine()
        first = e.rank(DnaRule(), {'EN': 3})
        self.assertEqual(first['unresolved_positive_records'], 1)
        original = {r['iucn_sis_id']: r for r in first['rows']}
        self.assertIsNone(original['0']['priority'])
        self.assertIsNone(original['1']['priority'])
        self.assertEqual(original['2']['priority'], 21)
        assembly = e.rank(DnaRule('any-assembly'), {'EN': 3}, nearest=True)
        rows = {r['iucn_sis_id']: r for r in assembly['rows']}
        self.assertEqual(rows['0']['gap_myr'], 11)
        self.assertEqual(rows['0']['priority'], 33)
        self.assertEqual(rows['2']['priority'], 0)
        self.assertIsNone(rows['3']['gap_myr'])
        filtered = e.rank(DnaRule('any-assembly'), {'EN': 3}, species_ids={'0'}, nearest=True)
        self.assertEqual(filtered['rows'], [rows['0']])
        self.assertEqual(filtered['scenario_id'], assembly['scenario_id'])
        changed = e.rank(DnaRule('any-assembly'), {'EN': 5}, nearest=True)
        self.assertEqual(changed['scenario_id'], assembly['scenario_id'])
        self.assertNotEqual(changed['weighted_scenario_id'], assembly['weighted_scenario_id'])
        self.assertEqual(len(e._cache), 1)
        self.assertEqual(e.rank(DnaRule('any-assembly'), {'EN': 3})['scenario_id'], assembly['scenario_id'])

    def test_upper_bounds_include_withheld_species_and_shares_sum_to_branch_union(self):
        e = self.engine()
        for rule, represented in ((DnaRule(), set()), (DnaRule('any-assembly'), {'c'})):
            rows = {r['iucn_sis_id']: r for r in e.calculate(rule)['rows']}
            # '3' is unsafe and '0'/'1' are baseline-incomplete: all withheld, all bounded.
            self.assertIsNone(rows['3']['gap_myr'])
            expected = dict(zip('0123', tree().gaps(represented)))
            self.assertEqual({k: r['gap_upper_myr'] for k, r in rows.items()}, expected)
            union = pd_union(tree(), set('abcd')) - pd_union(tree(), represented)
            self.assertAlmostEqual(sum(r['gap_share_upper_myr'] for r in rows.values()), union)
        self.assertEqual(rows['0']['gap_share_upper_myr'], 6)  # 10/2 shared + 1 pendant
        self.assertEqual(rows['3']['gap_share_upper_myr'], 3)  # sister 'c' is represented
        t = tree()
        np.testing.assert_allclose(t.gap_shares(set(), {'a': 2, 'b': 1}), [10 / 3 + 1 / 2, 10 / 3 + 1, 0, 0])

    def test_extinct_species_claim_no_fair_share(self):
        e = self.engine()
        e.species[0]['redlist_category'] = 'EX'
        rows = {r['iucn_sis_id']: r for r in e.rank(DnaRule(), {'EN': 3})['rows']}
        self.assertIsNone(rows['0']['gap_share_upper_myr'])
        self.assertEqual(rows['0']['gap_upper_myr'], 11)
        self.assertEqual(rows['1']['gap_share_upper_myr'], 11)  # sole claimant of the shared stem
        self.assertEqual(rows['1']['redlist_category'], 'EN')

    def test_tree_only_identity_does_not_seed_lineage_representation(self):
        rows = [dict(taxon_id='x', kingdom_taxid='k', family_taxid='f', genus_taxid='g', sample_acquired='yes', genomic_transfer_safe=False)]
        targets = rows + [dict(kingdom_taxid='k')]
        result = lineage_representation(rows, targets, DnaRule())
        self.assertFalse(result[0]['family_represented'])
        self.assertIsNone(result[1]['family_represented'])

    def test_common_cohort_ties_and_unknowns(self):
        result = compare_scenarios({'b': 2., 'a': 2., 'x': None}, {'a': 2., 'b': 2., 'y': 9.})
        self.assertEqual(result['common_cohort'], 2)
        self.assertIsNone(result['spearman_rank_correlation'])
        self.assertEqual(result['top_percent_overlap']['1']['fraction'], 1)


if __name__ == '__main__':
    unittest.main()
