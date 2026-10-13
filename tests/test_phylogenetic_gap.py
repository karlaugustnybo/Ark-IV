from __future__ import annotations

import importlib.util
import itertools
import unittest

import pandas as pd

HAS_ENGINE = importlib.util.find_spec("dated_complete_tree") is not None
if HAS_ENGINE:
    from ete4 import Tree

from ark_pipeline.phylogenetic_gap import genome_evidence, greedy_targets, marginal_gaps, rooted_pd


@unittest.skipUnless(HAS_ENGINE, "Optional engine: uv sync --group edge")
class PhylogeneticGapTests(unittest.TestCase):
    def tree(self):
        return Tree("((a:1,b:1):10,(c:3,d:3):4):0;", parser=1)

    def test_marginal_pd_matches_independent_path_union_for_every_baseline(self):
        tree = self.tree()
        for size in range(5):
            for names in itertools.combinations("abcd", size):
                baseline = set(names)
                gaps = marginal_gaps(tree, baseline).set_index("tree_tip")
                before = rooted_pd(tree, baseline)
                for tip in "abcd":
                    expected = rooted_pd(tree, baseline | {tip}) - before
                    self.assertAlmostEqual(gaps.loc[tip, "genomic_gap_myr"], expected)
                    self.assertEqual(gaps.loc[tip, "website_dna_on_tip"], tip in baseline)

    def test_nearest_relative_matches_independent_tree_distances(self):
        tree = self.tree()
        leaves = {leaf.name: leaf for leaf in tree.leaves()}
        for baseline in [{"a"}, {"a", "c"}, {"b", "c", "d"}, set("abcd")]:
            result = marginal_gaps(tree, baseline).set_index("tree_tip")
            for name in "abcd":
                expected = min(
                    (tree.get_distance(leaves[name], leaves[target]), target) for target in baseline
                )
                self.assertEqual(result.loc[name, "nearest_dna_tip"], expected[1])
                self.assertAlmostEqual(result.loc[name, "nearest_dna_distance_myr"], expected[0])

    def test_empty_baseline_polytomy_zero_lengths_and_reproducible_distance_ties(self):
        tree = Tree("(a:0,b:2,c:2,d:5):0;", parser=1)
        empty = marginal_gaps(tree, set()).set_index("tree_tip")
        self.assertEqual(empty.loc["d", "genomic_gap_myr"], 5)
        self.assertTrue(empty.nearest_dna_tip.isna().all())
        tied = marginal_gaps(tree, {"b", "c"}).set_index("tree_tip")
        self.assertEqual(tied.loc["d", "nearest_dna_tip"], "b")
        self.assertEqual(tied.loc["a", "genomic_gap_myr"], 0)
        with self.assertRaisesRegex(ValueError, "outside"):
            marginal_gaps(tree, {"missing"})

    def test_additional_representation_never_increases_a_gap(self):
        tree = self.tree()
        first = marginal_gaps(tree, {"c"}).set_index("tree_tip")
        second = marginal_gaps(tree, {"a", "c"}).set_index("tree_tip")
        self.assertTrue(second.genomic_gap_myr.le(first.genomic_gap_myr).all())
        self.assertEqual(second.loc["b", "genomic_gap_myr"], 1)

    def test_greedy_selection_recomputes_shared_branch_gain(self):
        tree = Tree("((a:1,b:1):10,c:5,d:8):0;", parser=1)
        candidates = pd.DataFrame(
            {
                "iucn_sis_id": [1, 2, 3],
                "tree_tip": ["a", "b", "d"],
                "threat_weight": [1.0, 1.0, 1.0],
            }
        )
        plan = greedy_targets(tree, {"c"}, candidates, 3)
        self.assertEqual(plan.tree_tip.tolist(), ["a", "d", "b"])
        self.assertEqual(plan.genomic_gap_myr.tolist(), [11, 8, 1])
        self.assertEqual(
            plan.genomic_gap_myr.sum(), rooted_pd(tree, set("abcd")) - rooted_pd(tree, {"c"})
        )
        self.assertTrue(greedy_targets(tree, {"c"}, candidates, 0).empty)

    def test_website_rule_includes_samples_projects_criteria_and_assembly_branch(self):
        base = dict(
            assembly_level=None,
            busco_completeness=None,
            sample_acquired=None,
            in_progress=None,
            ebp_standard_criteria=None,
            resampling_required=None,
        )
        inputs = [
            {},
            dict(sample_acquired="DTOL"),
            dict(in_progress="VGP"),
            dict(ebp_standard_criteria="6.C"),
            dict(assembly_level="Chromosome", busco_completeness="90"),
            dict(assembly_level="Chromosome", busco_completeness="89.9"),
            dict(assembly_level="Scaffold", busco_completeness="99"),
            dict(resampling_required="DTOL"),
            dict(sample_acquired=" "),
        ]
        rows = pd.DataFrame([{**base, **row, "taxon_id": str(i)} for i, row in enumerate(inputs)])
        result = genome_evidence(rows)
        self.assertEqual(
            result.legacy_broad_evidence.tolist(),
            [False, True, True, True, True, False, False, False, True],
        )
        self.assertFalse(result.loc[1, "qualifying_genome_proxy"])
        self.assertTrue(result.loc[7, "resampling_reported"])
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            genome_evidence(pd.concat([rows, rows]))


if __name__ == "__main__":
    unittest.main()
