from __future__ import annotations

import hashlib
import io
import json
import tarfile
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import pandas as pd

from ark_pipeline.cli.edge_link import run
from ark_pipeline.edge import assessment_join
from ark_pipeline.edge_links import (
    build_tip_index,
    direct_identifiers,
    exact_name_candidates,
    newick_tips,
)
from ark_pipeline.edge_matching import improve_matches, tip_taxonomy
from ark_pipeline.runtime.provenance import sha256


class GlobalTreeLinkTests(unittest.TestCase):
    def test_parser_retains_promoted_and_placeholder_leaves_and_lengths(self):
        rows = list(newick_tips("((A_ott1:1e-3,B_ott02:0)internal:9,C_sp._ott0000:3)root:8;"))
        self.assertEqual([r["ott_id"] for r in rows], ["1", "2", "0"])
        self.assertEqual([r["terminal_myr"] for r in rows], [0.001, 0, 3])
        for bad in [
            "(A_ott1:1,B_ott2);",
            "(A_ott1:-1,B_ott2:1);",
            "(A:1,B_ott2:1);",
            "('A_ott1':1,B_ott2:1);",
            "(A_ott1:1,B_ott2:1));",
        ]:
            with self.subTest(newick=bad), self.assertRaises(ValueError):
                list(newick_tips(bad))

    def fixture(self, root):
        tree = root / "tree.tre"
        tree.write_text(
            "(Test_one_ott11:1,Test_two_ott22:2,Test_three_ott33:3,Repeated_ott44:1,Repeated_ott44:1)root:0;"
        )
        taxonomy = root / "taxonomy.parquet"
        rows = [
            ["1", "", "Life", "no rank", ""],
            ["5", "1", "Testidae", "family", ""],
            ["6", "5", "Test", "genus", ""],
            ["11", "6", "Test one", "species", "ncbi:old"],
            ["22", "6", "Test two", "species", ""],
            ["33", "6", "Test three", "subspecies", "ncbi:3"],
            ["44", "6", "Repeated", "species", "ncbi:4"],
        ]
        pd.DataFrame(
            rows, columns=["ott_id", "parent_id", "taxon_name", "taxon_rank", "sourceinfo"]
        ).assign(flags="").to_parquet(taxonomy, index=False)
        tips = build_tip_index(tree, root / "tips.parquet")
        return tree, taxonomy, tips

    def test_index_is_hash_checked_and_does_not_hide_repeated_labels(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            tree, taxonomy, tips = self.fixture(root)
            original = tree.read_bytes()
            frame = pd.read_parquet(tips)
            self.assertEqual(len(frame), 5)
            self.assertTrue(frame.leaf_index.is_unique)
            self.assertEqual(
                frame[frame.tree_tip == "Repeated_ott44"].label_occurrences.tolist(), [2, 2]
            )
            ids, repeated = direct_identifiers(tips, taxonomy, {"1", "3", "4"}, {"old": "1"})
            self.assertEqual(ids, {"1": {"Test_one_ott11"}, "4": {"Repeated_ott44"}})
            self.assertEqual(repeated, {"4"})
            self.assertEqual(build_tip_index(tree, tips), tips)
            self.assertEqual(tree.read_bytes(), original)
            tips.write_bytes(b"changed")
            with self.assertRaisesRegex(ValueError, "checksum changed"):
                build_tip_index(tree, tips)

    def test_scoped_lineages_keep_exact_name_conflicts_visible(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _, taxonomy, tips = self.fixture(root)
            candidates = exact_name_candidates(
                tips, taxonomy, {"2": {"test two"}, "4": {"repeated"}}
            )
            metadata = tip_taxonomy(candidates, taxonomy)
            self.assertEqual(set(metadata.ott_family), {"Testidae"})
            self.assertEqual(set(metadata.ott_genus), {"Test"})
            cw = root / "cw.parquet"
            pd.DataFrame(
                {
                    "iucn_sis_id": [2, 4],
                    "ncbi_genus": ["stale", "stale"],
                    "ncbi_family": ["stale", "stale"],
                }
            ).to_parquet(cw, index=False)
            rows = [
                {
                    "iucn_sis_id": i,
                    "iucn_scientific_name": n,
                    "canonical_ncbi_taxid": str(i),
                    "tree_tip": None,
                    "join_status": "ncbi_not_in_clade",
                }
                for i, n in [(2, "Test two"), (4, "Repeated")]
            ]
            current = {str(i): {"ncbi_genus": "Test", "ncbi_family": "Testidae"} for i in [2, 4]}
            improved, audit = improve_matches(
                rows, cw, metadata, {"2": {"test two"}, "4": {"repeated"}}, current_lineages=current
            )
            self.assertEqual(improved[0]["tree_tip"], "Test_two_ott22")
            self.assertEqual(improved[1]["join_status"], "ncbi_not_in_clade")
            self.assertEqual(audit[audit.iucn_sis_id == 4].iloc[0].reason, "repeated_tree_label")

    def test_global_cli_links_classes_without_scoring_or_changing_source(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            tree, taxonomy, _ = self.fixture(root)
            (root / "TOL").mkdir()
            (root / "IUCN_Red_List").mkdir()
            (root / "TOL/merged.dmp").write_text("old | 1 |\n")
            (root / "TOL/nodes.dmp").write_text(
                "1 | 6 | species |\n2 | 6 | species |\n4 | 6 | species |\n6 | 5 | genus |\n5 | 9 | family |\n9 | 9 | no rank |\n"
            )
            (root / "TOL/names.dmp").write_text(
                "1 | Test one | | scientific name |\n2 | Test two | | scientific name |\n4 | Repeated | | scientific name |\n6 | Test | | scientific name |\n5 | Testidae | | scientific name |\n"
            )
            assessments = root / "IUCN_Red_List/assessments.csv"
            assessments.write_text(
                "assessmentId,internalTaxonId,redlistCategory,scopes,possiblyExtinct,possiblyExtinctInTheWild,yearPublished,assessmentDate\n101,1,CR,Global,false,false,2026,2026-01-01\n102,2,EX,Global,false,false,2026,2026-01-01\n104,4,LC,Global,false,false,2026,2026-01-01\n"
            )
            cw = root / "cw.parquet"
            pd.DataFrame(
                {
                    "iucn_sis_id": [1, 2, 4],
                    "iucn_assessment_id": [101, 102, 104],
                    "iucn_scientific_name": ["Test one", "Test two", "Repeated"],
                    "iucn_class": ["MAMMALIA", "INSECTA", "INSECTA"],
                    "matched_ncbi_species_taxid": ["1", "2", "4"],
                    "safe_for_automatic_species_trait_transfer": [True] * 3,
                    "match_status": ["accepted"] * 3,
                    "ncbi_genus": ["Test"] * 3,
                    "ncbi_family": ["Testidae"] * 3,
                }
            ).to_parquet(cw, index=False)
            (root / "match_summary.json").write_text(
                json.dumps({"sources": {"iucn_assessments": {"sha256": sha256(assessments)}}})
            )
            ott = root / "ott.tgz"
            table = pd.read_parquet(taxonomy)
            text = "uid\t|\tparent_uid\t|\tname\t|\trank\t|\tsourceinfo\t|\tuniqname\t|\tflags\t|\n"
            text += "".join(
                "\t|\t".join(
                    [r.ott_id, r.parent_id, r.taxon_name, r.taxon_rank, r.sourceinfo, "", ""]
                )
                + "\t|\n"
                for r in table.itertuples()
            )
            with tarfile.open(ott, "w:gz") as archive:
                content = text.encode()
                info = tarfile.TarInfo("ott/taxonomy.tsv")
                info.size = len(content)
                archive.addfile(info, io.BytesIO(content))
            config = root / "config.toml"
            config.write_text(
                "schema_version=1\n"
                + "".join(
                    f'\n[[sources]]\nid="{name}"\nfilename="{p.name}"\nurl="https://example.invalid/unused"\nrelease="fixture"\nsha256="{sha256(p)}"\n'
                    for name, p in [("dated-tree", tree), ("ott-taxonomy", ott)]
                )
            )
            original_hash = sha256(tree)
            report = run(
                SimpleNamespace(
                    config=config,
                    input_dir=root,
                    root=root,
                    crosswalk=cw,
                    output_dir=root / "results",
                    download=False,
                )
            )
            self.assertEqual(report["linked"], 2)
            self.assertEqual(report["direct_links"], 1)
            self.assertEqual(report["recovered_links"], 1)
            self.assertEqual(report["extant_candidates"], 1)
            self.assertEqual(report["join_status_counts"]["ambiguous_repeated_tree_label"], 1)
            self.assertEqual(len(assessment_join(cw, assessments)), 1)
            self.assertEqual(sha256(tree), original_hash)
            links = pd.read_parquet(root / "results/species_tree_links.parquet")
            self.assertFalse(any("edge2" in col for col in links))
            (root / "match_summary.json").write_text(
                json.dumps(
                    {
                        "sources": {
                            "iucn_assessments": {"sha256": hashlib.sha256(b"wrong").hexdigest()}
                        }
                    }
                )
            )
            with self.assertRaisesRegex(ValueError, "snapshot is stale"):
                run(
                    SimpleNamespace(
                        config=config,
                        input_dir=root,
                        root=root,
                        crosswalk=cw,
                        output_dir=root / "results",
                        download=False,
                    )
                )


if __name__ == "__main__":
    unittest.main()
