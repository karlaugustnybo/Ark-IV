from __future__ import annotations

import importlib.util
import io
import tarfile
import tempfile
import unittest
from pathlib import Path

import pandas as pd

HAS_ENGINE = importlib.util.find_spec("dated_complete_tree") is not None
if HAS_ENGINE:
    from ete4 import Tree

from ark_pipeline.edge import score_clade
from ark_pipeline.edge_matching import (
    build_ott_index,
    current_species_lineages,
    improve_matches,
    normalize_name,
    tree_taxonomy,
)
from ark_pipeline.edge_trees import acquire_archive, extract_archive
from ark_pipeline.edge_validation import (
    align_mammal_crown,
    rank_tree,
    stability_summary,
    topology_signature,
)


def add_member(archive, name, contents):
    content = contents.encode()
    member = tarfile.TarInfo(name)
    member.size = len(content)
    archive.addfile(member, io.BytesIO(content))


class EdgeFollowupTests(unittest.TestCase):
    def test_current_ncbi_lineage_overrides_old_crosswalk_lineage(self):
        with tempfile.TemporaryDirectory() as tmp:
            nodes = Path(tmp) / "nodes.dmp"
            names = Path(tmp) / "names.dmp"
            nodes.write_text(
                "1 | 2 | species |\n2 | 3 | genus |\n3 | 5 | family |\n5 | 5 | no rank |\n"
            )
            names.write_text(
                "1 | Test one | | scientific name |\n2 | Newgenus | | scientific name |\n3 | Newidae | | scientific name |\n"
            )
            current = current_species_lineages(nodes, names, {"1", "99"})
            self.assertEqual(current["1"], {"ncbi_genus": "Newgenus", "ncbi_family": "Newidae"})
            self.assertEqual(current["99"], {"ncbi_genus": None, "ncbi_family": None})
            cw = Path(tmp) / "cw.parquet"
            pd.DataFrame(
                [{"iucn_sis_id": 1, "ncbi_genus": "Test", "ncbi_family": "Testidae"}]
            ).to_parquet(cw)
            tips = pd.DataFrame(
                [
                    {
                        "tree_tip": "tip",
                        "taxon_rank": "species",
                        "normalized_name": "test one",
                        "ott_genus": "Test",
                        "ott_family": "Testidae",
                        "sourceinfo": "",
                    }
                ]
            )
            rows = [
                {
                    "iucn_sis_id": 1,
                    "iucn_scientific_name": "Test one",
                    "canonical_ncbi_taxid": "1",
                    "tree_tip": None,
                    "join_status": "ncbi_not_in_clade",
                }
            ]
            improved, audit = improve_matches(
                rows, cw, tips, {"1": {"test one"}}, current_lineages=current
            )
            self.assertEqual(improved[0]["join_status"], "ncbi_not_in_clade")
            self.assertEqual(audit.iloc[0].reason, "lineage_conflict_or_missing")

    @unittest.skipUnless(HAS_ENGINE, "Install the optional edge dependency group")
    def test_risk_draws_are_invariant_to_tree_traversal(self):
        rows = [
            {"tree_tip": name, "redlist_category": category, "join_status": "joined"}
            for name, category in [("A", "VU"), ("B", "DD"), ("C", "LC")]
        ]
        first, _ = score_clade(
            Tree("((A:1,B:1)n:2,C:3)r:0;", parser=1), rows, 32, 5, stable_risk_order=True
        )
        second, _ = score_clade(
            Tree("(C:3,(B:1,A:1)n:2)r:0;", parser=1), rows, 32, 5, stable_risk_order=True
        )
        self.assertEqual(
            {row["tree_tip"]: row for row in first}, {row["tree_tip"]: row for row in second}
        )

    def test_archive_skips_metadata_and_cache_checks_crowns(self):
        with tempfile.TemporaryDirectory() as tmp:
            buffer = io.BytesIO()
            with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
                add_member(archive, "._tree.tre", "invalid metadata")
                for index in range(3):
                    add_member(
                        archive,
                        f"tree{index}.tre",
                        "((A_ott1:1,B_ott2:1)Mammalia_ott244265:2,C_ott3:3)r:0;",
                    )
            buffer.seek(0)
            source = {"id": "test", "url": "https://example.invalid/archive", "total_trees": 3}
            receipt = extract_archive(buffer, source, Path(tmp), "Mammalia_ott244265", 2)
            self.assertEqual(len(receipt["trees"]), 2)
            self.assertFalse(receipt["archive_checksum_verified"])
            self.assertEqual(acquire_archive(source, Path(tmp), "Mammalia_ott244265", 2), receipt)
            Path(receipt["trees"][0]["path"]).write_text("corrupted")
            with self.assertRaisesRegex(ValueError, "changed"):
                acquire_archive(source, Path(tmp), "Mammalia_ott244265", 2)

    @unittest.skipUnless(HAS_ENGINE, "Install the optional edge dependency group")
    def test_ott_index_trims_tabs_and_resolves_ancestors(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive_path = Path(tmp) / "ott.tgz"
            contents = (
                "uid\t|\tparent_uid\t|\tname\t|\trank\t|\tsourceinfo\t|\tuniqname\t|\tflags\t|\n"
            )
            contents += "1\t|\t2\t|\tTest alpha\t|\tspecies\t|\tncbi:10\t|\t\t|\t\t|\n"
            contents += "2\t|\t3\t|\tTest\t|\tgenus\t|\t\t|\t\t|\t\t|\n"
            contents += "3\t|\t4\t|\tTestidae\t|\tfamily\t|\t\t|\t\t|\t\t|\n"
            contents += "4\t|\t\t|\tMammalia\t|\tclass\t|\t\t|\t\t|\t\t|\n"
            with tarfile.open(archive_path, "w:gz") as archive:
                add_member(archive, "ott/taxonomy.tsv", contents)
            index = build_ott_index(archive_path, Path(tmp) / "index.parquet")
            metadata = tree_taxonomy(
                Tree("(Test_alpha_ott1:1,Unknown_ott0000:1)r:0;", parser=1), index
            )
            self.assertEqual(metadata.iloc[0].ott_genus, "Test")
            self.assertEqual(metadata.iloc[0].ott_family, "Testidae")
            self.assertEqual(metadata.iloc[0].taxon_rank, "species")
            self.assertEqual(metadata.iloc[0].normalized_name, "test alpha")
            self.assertEqual(normalize_name(float("nan")), "")

    @unittest.skipUnless(HAS_ENGINE, "Install the optional edge dependency group")
    def test_only_known_nonmammal_environmental_extras_are_pruned(self):
        tree = Tree("((A:1,B:1)n:2,Environmental:3)r:0;", parser=1)
        metadata = pd.DataFrame(
            [
                {
                    "tree_tip": "Environmental",
                    "taxon_name": "Chordata environmental sample",
                    "ott_class": None,
                }
            ]
        )
        self.assertEqual(align_mammal_crown(tree, {"A", "B"}, metadata), ["Environmental"])
        self.assertEqual(set(tree.leaf_names()), {"A", "B"})
        self.assertTrue(all(leaf.dist == 1 for leaf in tree.leaves()))
        self.assertEqual(tree.props["excluded_environmental_crown_stem_myr"], 2)
        self.assertEqual(tree.dist, 0)
        with self.assertRaisesRegex(ValueError, "missing"):
            align_mammal_crown(tree, {"A", "B", "Missing"}, metadata)
        changed = Tree("(A:1,B:1,Environmental:1)r:0;", parser=1)
        metadata.loc[0, "ott_class"] = "Mammalia"
        with self.assertRaisesRegex(ValueError, "Unexpected extra"):
            align_mammal_crown(changed, {"A", "B"}, metadata)
        self.assertEqual(
            topology_signature(Tree("(A:1,B:1)r:0;", parser=1)),
            topology_signature(Tree("(B:2,A:2)other:0;", parser=1)),
        )

    def test_exact_match_guards_conflicts_lineage_and_reserved_tips(self):
        with tempfile.TemporaryDirectory() as tmp:
            crosswalk = Path(tmp) / "cw.parquet"
            pd.DataFrame(
                [
                    {"iucn_sis_id": sis, "ncbi_genus": "Test", "ncbi_family": "Testidae"}
                    for sis in range(1, 8)
                ]
            ).to_parquet(crosswalk)
            tips = pd.DataFrame(
                [
                    {
                        "tree_tip": "tip1",
                        "taxon_rank": "species",
                        "normalized_name": "test one",
                        "ott_genus": "Test",
                        "ott_family": "Testidae",
                        "sourceinfo": "",
                    },
                    {
                        "tree_tip": "tip2",
                        "taxon_rank": "species",
                        "normalized_name": "test two",
                        "ott_genus": "Test",
                        "ott_family": "Testidae",
                        "sourceinfo": "ncbi:999",
                    },
                    {
                        "tree_tip": "tip3",
                        "taxon_rank": "species",
                        "normalized_name": "test three",
                        "ott_genus": "Wrong",
                        "ott_family": "Testidae",
                        "sourceinfo": "",
                    },
                    {
                        "tree_tip": "tip4",
                        "taxon_rank": "species",
                        "normalized_name": "test four",
                        "ott_genus": "Test",
                        "ott_family": "Testidae",
                        "sourceinfo": "",
                    },
                    {
                        "tree_tip": "tip5",
                        "taxon_rank": "species",
                        "normalized_name": "test duplicate",
                        "ott_genus": "Test",
                        "ott_family": "Testidae",
                        "sourceinfo": "",
                    },
                    {
                        "tree_tip": "tip6",
                        "taxon_rank": "species",
                        "normalized_name": "test duplicate",
                        "ott_genus": "Test",
                        "ott_family": "Testidae",
                        "sourceinfo": "",
                    },
                ]
            )
            rows = [
                {
                    "iucn_sis_id": sis,
                    "iucn_scientific_name": f"Test {sis}",
                    "canonical_ncbi_taxid": str(sis),
                    "tree_tip": None,
                    "join_status": "ncbi_not_in_clade",
                }
                for sis in range(1, 7)
            ]
            rows[4]["join_status"] = "unsafe_crosswalk"
            rows.append({"iucn_sis_id": 7, "tree_tip": "tip4", "join_status": "joined"})
            names = {
                "1": {"test one"},
                "2": {"test two"},
                "3": {"test three"},
                "4": {"test four"},
                "5": {"test one"},
                "6": {"test duplicate"},
            }
            improved, audit = improve_matches(rows, crosswalk, tips, names)
            self.assertEqual(
                [
                    row["iucn_sis_id"]
                    for row in improved
                    if row["join_method"] == "exact_ncbi_name_genus_family"
                ],
                [1],
            )
            self.assertEqual(improved[4]["join_status"], "unsafe_crosswalk")
            self.assertIn("ott_identifier_conflict", set(audit.reason))
            self.assertIn("lineage_conflict_or_missing", set(audit.reason))
            self.assertIn("reserved_tip_or_multiple_proposals", set(audit.reason))
            self.assertIn("ambiguous_exact_name", set(audit.reason))

    def test_competing_species_cannot_share_recovered_tip(self):
        with tempfile.TemporaryDirectory() as tmp:
            crosswalk = Path(tmp) / "cw.parquet"
            pd.DataFrame(
                [
                    {"iucn_sis_id": sis, "ncbi_genus": "Test", "ncbi_family": "Testidae"}
                    for sis in [1, 2]
                ]
            ).to_parquet(crosswalk)
            tips = pd.DataFrame(
                [
                    {
                        "tree_tip": "tip",
                        "taxon_rank": "species",
                        "normalized_name": "test one",
                        "ott_genus": "Test",
                        "ott_family": "Testidae",
                        "sourceinfo": "",
                    }
                ]
            )
            rows = [
                {
                    "iucn_sis_id": sis,
                    "iucn_scientific_name": "Test one",
                    "canonical_ncbi_taxid": str(sis),
                    "tree_tip": None,
                    "join_status": "ncbi_not_in_clade",
                }
                for sis in [1, 2]
            ]
            improved, audit = improve_matches(
                rows, crosswalk, tips, {"1": {"test one"}, "2": {"test one"}}
            )
            self.assertTrue(all(row["join_status"] == "ncbi_not_in_clade" for row in improved))
            self.assertFalse(audit.accepted.any())

    def test_stability_rejects_changed_cohorts_and_excludes_non_threatened(self):
        species = pd.DataFrame(
            [
                {
                    "iucn_sis_id": sis,
                    "iucn_scientific_name": f"Test {sis}",
                    "join_status": "scored",
                    "redlist_category": category,
                    "ark_edge2_myr_median": score,
                    "terminal_branch_myr": 1,
                }
                for sis, category, score in [(1, "LC", 10), (2, "VU", 5)]
            ]
        )
        baseline = rank_tree(species, "base", "base")
        alternatives = pd.concat(
            [rank_tree(species, "tree1", "dates"), rank_tree(species, "tree2", "dates")]
        )
        summary, stats = stability_summary(alternatives, baseline)
        self.assertEqual(stats["dates"]["always_top100_threatened"], 1)
        self.assertEqual(summary[summary.iucn_sis_id == 1].iloc[0].top100_threatened_trees, 0)
        with self.assertRaisesRegex(ValueError, "same assessed species"):
            stability_summary(alternatives.iloc[1:], baseline)


if __name__ == "__main__":
    unittest.main()
