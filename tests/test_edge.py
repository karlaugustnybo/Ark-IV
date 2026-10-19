from __future__ import annotations

import hashlib
import importlib.util
import io
import math
import tarfile
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd

from ark_pipeline.edge import (
    candidate_list,
    canonical_taxid,
    compare_reference,
    extract_clade,
    normalize_category,
    original_edge,
    reviewed_tip_join,
    score_clade,
    tip_identifiers,
    verify_source,
)

HAS_ENGINE = importlib.util.find_spec("dated_complete_tree") is not None


def assessment(sis: int, ncbi: str, category: str = "LC", **overrides) -> dict:
    return {
        "iucn_sis_id": sis,
        "assessment_sis_id": str(sis),
        "iucn_scientific_name": f"Test species{sis}",
        "matched_ncbi_species_taxid": ncbi,
        "safe_for_automatic_species_trait_transfer": True,
        "redlistCategory": category,
        "scopes": "Global",
        **overrides,
    }


class EdgeInputTests(unittest.TestCase):
    def test_named_crown_preserves_internal_lengths_and_excludes_stem(self):
        self.assertEqual(
            extract_clade(
                "(((A:1,B:1)AB:2,C:3)Mammalia_ott244265:7,D:10)life:0;", "Mammalia_ott244265"
            ),
            "((A:1,B:1)AB:2,C:3)Mammalia_ott244265:0;",
        )
        with self.assertRaisesRegex(ValueError, "found 0"):
            extract_clade("(A:1,B:1)Other:0;", "Mammalia_ott244265")
        with self.assertRaisesRegex(ValueError, "found 2"):
            extract_clade("((A:1,B:1)C:1,(D:1,E:1)C:1)root:0;", "C")
        with self.assertRaisesRegex(ValueError, "unquoted"):
            extract_clade("('A(x)':1,B:1)C:0;", "C")

    def test_original_edge_and_category_rules(self):
        self.assertAlmostEqual(original_edge(0, "CR"), 4 * math.log(2))
        self.assertAlmostEqual(original_edge(2, "LC"), math.log(3))
        for category in ("DD", "NE", "EX", "EW"):
            self.assertIsNone(original_edge(2, category))
        self.assertEqual(normalize_category("Critically Endangered"), "CR")
        self.assertEqual(normalize_category("LR/cd"), "NT")
        with self.assertRaisesRegex(ValueError, "Unknown"):
            normalize_category("UNKNOWN")

    def test_strict_threshold_and_lists(self):
        self.assertEqual(candidate_list("CR", 0.95), "threatened")
        self.assertEqual(candidate_list("DD", 0.95), "research")
        self.assertEqual(candidate_list("NE", 1), "research")
        self.assertEqual(candidate_list("NT", 0.95), "watch")
        self.assertIsNone(candidate_list("CR", 0.949))
        self.assertIsNone(candidate_list("EW", 1))
        self.assertIsNone(candidate_list("EX", 1))

    def test_current_rank_gate_rejects_merged_genus_and_absent_ids(self):
        joined = reviewed_tip_join(
            [assessment(1, "101", "EN"), assessment(2, "2", "VU"), assessment(3, "3")],
            {"1": {"A"}, "2": {"B"}, "3": {"C"}},
            {"101": "1"}, current_ranks={"1": "genus", "3": "species"},
        )
        self.assertEqual([r["join_status"] for r in joined],
                         ["ncbi_not_current_species", "ncbi_not_current_species", "joined"])
        self.assertEqual([r["tree_tip"] for r in joined], [None, None, "C"])
        self.assertEqual(joined[0]["canonical_ncbi_taxid"], "1")

    def test_join_never_chooses_ambiguous_or_unsafe_taxa(self):
        rows = [
            assessment(1, "old"),
            assessment(2, "22"),
            assessment(3, "22"),
            assessment(4, "44"),
            assessment(5, "55", safe_for_automatic_species_trait_transfer=False),
            assessment(6, "66", scopes="Europe"),
            assessment(7, "77", assessment_sis_id="8"),
            assessment(8, "88", assessment_sis_id=None),
            assessment(9, "99"),
        ]
        identifiers = {"11": {"A"}, "22": {"B"}, "44": {"C", "D"}, "55": {"E"}}
        joined = reviewed_tip_join(rows, identifiers, {"old": "11"})
        self.assertEqual(
            [row["join_status"] for row in joined],
            [
                "joined",
                "ambiguous_iucn_to_tip",
                "ambiguous_iucn_to_tip",
                "ambiguous_ncbi_to_ott",
                "unsafe_crosswalk",
                "non_global_or_subpopulation",
                "assessment_identifier_conflict",
                "assessment_not_found",
                "ncbi_not_in_clade",
            ],
        )
        self.assertEqual(joined[0]["canonical_ncbi_taxid"], "11")
        with self.assertRaisesRegex(ValueError, "Cycle"):
            canonical_taxid("1", {"1": "2", "2": "1"})

    def test_corrupt_cache_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tree.tre"
            path.write_bytes(b"expected")
            source = {"id": "tree", "sha256": hashlib.sha256(b"expected").hexdigest()}
            verify_source(path, source)
            path.write_bytes(b"changed")
            with self.assertRaisesRegex(ValueError, "checksum changed"):
                verify_source(path, source)

    def test_reference_uses_sis_ids_and_separates_category_drift(self):
        species = pd.DataFrame(
            [
                {
                    "iucn_sis_id": 1,
                    "join_status": "scored",
                    "redlist_category": "CR",
                    "ark_ed2_myr_median": 5.0,
                    "ark_edge2_myr_median": 4.0,
                },
                {
                    "iucn_sis_id": 2,
                    "join_status": "scored",
                    "redlist_category": "EN",
                    "ark_ed2_myr_median": 2.0,
                    "ark_edge2_myr_median": 1.0,
                },
                {"iucn_sis_id": 3, "join_status": "unsafe_crosswalk", "redlist_category": "LC"},
            ]
        )
        reference = [
            {
                "iucn_sis_id": 1,
                "reference_category": "CR",
                "reference_ed2_myr": 4.0,
                "reference_edge2_myr": 3.0,
            },
            {
                "iucn_sis_id": 2,
                "reference_category": "VU",
                "reference_ed2_myr": 1.0,
                "reference_edge2_myr": 0.5,
            },
            {
                "iucn_sis_id": 3,
                "reference_category": "LC",
                "reference_ed2_myr": 10.0,
                "reference_edge2_myr": 1.0,
            },
        ]
        comparison, report = compare_reference(species, reference)
        self.assertEqual(len(comparison), 2)
        self.assertEqual(report["same_category"]["species"], 1)
        self.assertEqual(report["category_changed_species"], 1)
        self.assertAlmostEqual(report["all_joined"]["edge2_spearman"], 1.0)
        self.assertEqual(comparison.ed2_residual_myr.tolist(), [1.0, 1.0])


@unittest.skipUnless(HAS_ENGINE, "Optional engine: uv sync --group edge")
class EdgeEngineTests(unittest.TestCase):
    def tree(self):
        from ete4 import Tree

        return Tree("((A_ott11:1,B_ott22:1)AB:2,C_ott33:3)Mammalia_ott244265:0;", parser=1)

    def test_reference_engine_against_hand_calculated_branch_sharing(self):
        from dated_complete_tree import tree_metrics as engine

        tree = self.tree()
        risks = {"A_ott11": 0.2, "B_ott22": 0.5, "C_ott33": 0.7}
        for leaf in tree.leaves():
            leaf.add_prop("pext", risks[leaf.name])
        engine.label_desc_leaves(tree)
        engine.compute_ed_scores(tree)
        engine.compute_edge2_scores(tree)
        expected = {"A_ott11": (2, 2, 0.4), "B_ott22": (2, 1.4, 0.7), "C_ott33": (3, 3, 2.1)}
        for leaf in tree.leaves():
            for key, value in zip(
                ("ed_score", "ed2_score", "edge2_score"), expected[leaf.name], strict=True
            ):
                self.assertAlmostEqual(leaf.props[key], value)
        self.assertAlmostEqual(engine.compute_pd(tree), 7)

    def test_complete_clade_and_risk_sampling_are_reproducible(self):
        rows = reviewed_tip_join([assessment(1, "11", "CR")], {"11": {"A_ott11"}}, {})
        first, report = score_clade(self.tree(), rows, 8, 42)
        second, _ = score_clade(self.tree(), rows, 8, 42)
        self.assertEqual(first, second)
        self.assertEqual(report["retained_tree_tips"], 3)
        self.assertEqual(report["unassessed_or_unjoined_tree_tips"], 2)
        self.assertAlmostEqual(report["sum_ed_myr"], 7)

    def test_extinct_is_removed_and_ew_is_near_certainty(self):
        rows = reviewed_tip_join(
            [assessment(1, "11", "EX"), assessment(2, "22", "EW")],
            {"11": {"A_ott11"}, "22": {"B_ott22"}},
            {},
        )
        scores, report = score_clade(self.tree(), rows, 8, 42)
        self.assertEqual(report["excluded_extinct_tips"], 1)
        self.assertEqual({row["tree_tip"] for row in scores}, {"B_ott22", "C_ott33"})
        row = next(row for row in scores if row["tree_tip"] == "B_ott22")
        self.assertEqual(row["ark_ge2_median"], 0.97)
        self.assertIsNone(row["risk_only_candidate_list"])
        self.assertAlmostEqual(row["terminal_branch_myr"], 3)

    def test_iucn_endangered_status_overrules_ott_extinction_flag_in_scoring(self):
        tree = self.tree()
        next(leaf for leaf in tree.leaves() if leaf.name == "A_ott11").add_prop(
            "ott_flags", "extinct"
        )
        rows = reviewed_tip_join([assessment(1, "11", "EN")], {"11": {"A_ott11"}}, {})
        scores, report = score_clade(tree, rows, 8, 42)
        scored = next(row for row in scores if row["tree_tip"] == "A_ott11")
        self.assertEqual(report["retained_tree_tips"], 3)
        self.assertEqual(scored["redlist_category"], "EN")
        self.assertFalse(scored["effective_extinct"])
        self.assertTrue(scored["ott_extinction_overridden"])
        self.assertEqual(scored["ott_flags"], "extinct")

    def test_placeholder_tips_are_retained_without_false_identifiers(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ott.tgz"
            content = b"uid\t|\tparent_uid\t|\tname\t|\trank\t|\tsourceinfo\t|\tuniqname\t|\tflags\t|\n11\t|\t1\t|\tA\t|\tspecies\t|\tncbi:old\t|\t\t|\t\t|\n"
            with tarfile.open(path, "w:gz") as archive:
                info = tarfile.TarInfo("ott/taxonomy.tsv")
                info.size = len(content)
                archive.addfile(info, io.BytesIO(content))
            from ete4 import Tree

            tree = Tree("(A_ott11:1,(Foo_sp._ott0000:1,Bar_sp._ott0000:1)node:0)root:0;", parser=1)
            identifiers = tip_identifiers(tree, path, {"old": "11"})
            self.assertEqual(identifiers, {"11": {"A_ott11"}})
            self.assertEqual(len(tree), 3)

    def test_complete_local_build_and_stale_assessment_rejection(self):
        import json

        from ark_pipeline.cli.edge_prototype import run
        from ark_pipeline.runtime.provenance import sha256

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "IUCN_Red_List").mkdir()
            (root / "TOL").mkdir()
            assessments = root / "IUCN_Red_List/assessments.csv"
            assessments.write_text(
                "assessmentId,internalTaxonId,redlistCategory,scopes,possiblyExtinct,possiblyExtinctInTheWild,yearPublished,assessmentDate\n"
                "101,1,Critically Endangered,Global,false,false,2026,2026-01-01\n"
                "102,2,Least Concern,Global,false,false,2026,2026-01-01\n"
            )
            (root / "TOL/merged.dmp").write_text("old\t|\t11\t|\n")
            crosswalk = root / "crosswalk.parquet"
            pd.DataFrame(
                [
                    {
                        "iucn_sis_id": sis,
                        "iucn_assessment_id": aid,
                        "iucn_scientific_name": f"Test species{sis}",
                        "iucn_class": "MAMMALIA",
                        "matched_ncbi_species_taxid": taxid,
                        "safe_for_automatic_species_trait_transfer": True,
                        "match_status": "MATCHED",
                    }
                    for sis, aid, taxid in ((1, 101, "old"), (2, 102, "22"))
                ]
            ).to_parquet(crosswalk)
            (root / "match_summary.json").write_text(
                json.dumps({"sources": {"iucn_assessments": {"sha256": sha256(assessments)}}})
            )
            tree_path = root / "tree.tre"
            tree_path.write_text("((A_ott11:1,B_ott22:1)AB:2,C_ott33:3)Mammalia_ott244265:7;")
            taxonomy_path = root / "ott.tgz"
            content = (
                "uid\t|\tparent_uid\t|\tname\t|\trank\t|\tsourceinfo\t|\tuniqname\t|\tflags\t|\n"
            )
            for ott, taxid in ((11, "old"), (22, "22"), (33, "33")):
                content += f"{ott}\t|\t1\t|\tTest\t|\tspecies\t|\tncbi:{taxid}\t|\t\t|\t\t|\n"
            data = content.encode()
            with tarfile.open(taxonomy_path, "w:gz") as archive:
                info = tarfile.TarInfo("ott/taxonomy.tsv")
                info.size = len(data)
                archive.addfile(info, io.BytesIO(data))
            (root / "TOL/nodes.dmp").write_text("11 | 1 | species |\n22 | 1 | species |\n")
            config_path = Path(__file__).resolve().parents[1] / "config/edge_prototype.toml"
            import tomllib

            config = tomllib.loads(config_path.read_text())
            args = SimpleNamespace(
                root=root,
                crosswalk=crosswalk,
                samples=8,
                seed=42,
                output_dir=root / "output",
                config=config_path,
            )
            sources = {
                "dated-tree": {"path": str(tree_path)},
                "ott-taxonomy": {"path": str(taxonomy_path)},
                "mammal-reference": {"path": str(root / "unused.xlsx")},
            }
            reference = [
                {
                    "iucn_sis_id": 1,
                    "reference_category": "CR",
                    "reference_ed2_myr": 1.0,
                    "reference_edge2_myr": 1.0,
                },
                {
                    "iucn_sis_id": 2,
                    "reference_category": "LC",
                    "reference_ed2_myr": 1.0,
                    "reference_edge2_myr": 0.1,
                },
            ]
            with patch(
                "ark_pipeline.cli.edge_prototype.read_mammal_reference", return_value=reference
            ):
                report = run(args, config, sources)
            self.assertEqual(report["coverage"]["join_status_counts"], {"scored": 2})
            self.assertEqual(report["tree"]["unassessed_or_unjoined_tree_tips"], 1)
            self.assertEqual(report["phylogenetic_tree_count"], 1)
            for name, record in report["outputs"].items():
                self.assertEqual(record["sha256"], sha256(args.output_dir / name))
            species = pd.read_parquet(args.output_dir / "species_edge_prototype.parquet")
            self.assertEqual(species.iucn_sis_id.tolist(), [1, 2])
            self.assertEqual(species.redlist_category.tolist(), ["CR", "LC"])
            assessments.write_text(
                assessments.read_text().replace("Critically Endangered", "Endangered")
            )
            with self.assertRaisesRegex(ValueError, "snapshot is stale"):
                run(args, config, sources)


if __name__ == "__main__":
    unittest.main()
