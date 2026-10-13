from __future__ import annotations

import importlib.util
import unittest

import numpy as np
import pandas as pd

from ark_pipeline.edge_benchmark import (
    agreement,
    benchmark_newick,
    empirical_risks,
    fixed_engine_scores,
    independent_branch_scores,
    reference_tip_map,
)

HAS_ENGINE = importlib.util.find_spec("dated_complete_tree") is not None


class CalculatorAgreementTests(unittest.TestCase):
    def test_detects_actual_disagreement_and_rejects_mismatched_species(self):
        a = pd.DataFrame({"species": ["A", "B"], "ed2_myr": [2.0, 3.0], "edge2_myr": [0.2, 0.3]})
        b = a.iloc[::-1].copy()
        self.assertTrue(agreement(a, b)[1]["passed"])
        b.loc[b.species == "A", "ed2_myr"] = 2.01
        self.assertFalse(agreement(a, b)[1]["passed"])
        with self.assertRaisesRegex(ValueError, "identical unique"):
            agreement(a, b.iloc[:1])

    def test_risk_vector_uses_identical_uniforms_across_category_changes(self):
        rows = [["LC", ".1"], ["LC", ".2"], ["CR", ".8"], ["CR", ".9"]]
        x = empirical_risks(rows, ["A", "B"], {"A": "LC", "B": "CR"}, 42, 20)
        y = empirical_risks(rows, ["A", "B"], {"A": "CR", "B": "CR"}, 42, 20)
        np.testing.assert_allclose(x[:, 0] + 0.7, y[:, 0])
        np.testing.assert_array_equal(x[:, 1], y[:, 1])


@unittest.skipUnless(HAS_ENGINE, "optional EDGE engine not installed")
class BranchOracleTests(unittest.TestCase):
    def setUp(self):
        from ete4 import Tree

        self.Tree = Tree
        self.tree = Tree("((A:1,B:1):2,C:3):0;", parser=1)
        self.tree.dist = 0

    def test_shared_branch_depends_on_other_species_risk(self):
        p = {"A": 0.2, "B": 0.4, "C": 0.6}
        oracle = independent_branch_scores(self.tree, p).set_index("species")
        np.testing.assert_allclose(oracle.loc[["A", "B", "C"], "ed2_myr"], [1.8, 1.4, 3.0])
        np.testing.assert_allclose(oracle.loc[["A", "B", "C"], "edge2_myr"], [0.36, 0.56, 1.8])
        self.assertTrue(
            agreement(fixed_engine_scores(self.tree, p), oracle.reset_index())[1]["passed"]
        )

    def test_zero_risk_does_not_remove_focal_shared_history(self):
        out = independent_branch_scores(self.tree, {"A": 0.0, "B": 0.4, "C": 0.6}).set_index(
            "species"
        )
        self.assertAlmostEqual(out.loc["A", "ed2_myr"], 1.8)
        self.assertEqual(out.loc["A", "edge2_myr"], 0.0)
        self.assertEqual(out.loc["B", "ed2_myr"], 1.0)

    def test_polytomy_and_tip_order(self):
        p = {"A": 0.2, "B": 0.4, "C": 0.6}
        star = self.Tree("(C:3,A:1,B:2):0;", parser=1)
        star.dist = 0
        oracle = independent_branch_scores(star, p)
        self.assertTrue(agreement(fixed_engine_scores(star, p), oracle)[1]["passed"])
        reverse = self.Tree("(C:3,(B:1,A:1):2):0;", parser=1)
        reverse.dist = 0
        self.assertTrue(
            agreement(
                independent_branch_scores(reverse, p), independent_branch_scores(self.tree, p)
            )[1]["passed"]
        )

    def test_crown_stem_and_missing_risk_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "tips differ"):
            independent_branch_scores(self.tree, {"A": 0.1, "B": 0.2})
        self.tree.dist = 5
        with self.assertRaisesRegex(ValueError, "excluded crown stem"):
            independent_branch_scores(self.tree, {"A": 0.1, "B": 0.2, "C": 0.3})

    def test_serialization_retains_full_precision(self):
        original_tip = next(self.tree.search_nodes(name="A"))
        original_tip.dist = 0.12345678901234567
        reread = self.Tree(benchmark_newick(self.tree), parser=1)
        reread.dist = 0
        self.assertEqual(next(reread.search_nodes(name="A")).dist, original_tip.dist)

    def test_reviewed_sis_rename_and_duplicate_reference_identity(self):
        tree = self.Tree("(New_name_ott1:1,Other_name_ott2:1):0;", parser=1)
        tree.dist = 0
        reference = [
            {"reference_name": "Old name", "iucn_sis_id": 10},
            {"reference_name": "Other name", "iucn_sis_id": 20},
        ]
        reviewed = pd.DataFrame(
            {"tree_tip": ["New_name_ott1"], "iucn_sis_id": [10], "join_status": ["scored"]}
        )
        mapping, audit = reference_tip_map(tree, reference, reviewed)
        self.assertEqual(mapping["New_name_ott1"], "old name")
        self.assertEqual(audit["methods"]["reviewed_sis_identity"], 1)
        reviewed = pd.DataFrame(
            {
                "tree_tip": ["New_name_ott1", "Other_name_ott2"],
                "iucn_sis_id": [10, 10],
                "join_status": ["scored", "scored"],
            }
        )
        self.assertEqual(reference_tip_map(tree, reference, reviewed)[0], {})


if __name__ == "__main__":
    unittest.main()
