from __future__ import annotations

import io
import tarfile
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from ark_pipeline.edge_synonyms import build_synonym_index, extend_synonym_links, prepare_candidates
from tests.test_edge_link_extension import baseline_row, tax_row, tip


class DocumentedNameTests(unittest.TestCase):
    def prepare(self, name="Newgenus minor", accepted="Oldgenus minor", other_taxa=(), ncbi=()):
        candidate = tip(accepted, 10)
        candidate.update(taxon_name=accepted, sourceinfo="ncbi:99", taxon_rank="species")
        evidence = pd.DataFrame(
            [
                dict(
                    normalized_name=name.lower(),
                    tree_tip=candidate["tree_tip"],
                    identity_basis="ott_synonym",
                    alias_sourceinfo="gbif:123",
                )
            ]
        )
        taxonomy = pd.DataFrame(
            [tax_row(1, name), *[tax_row(i + 2, n) for i, n in enumerate(other_taxa)]]
        )
        names = pd.DataFrame(
            [dict(normalized_name=name.lower(), ncbi_taxid=t) for t in ncbi],
            columns=["normalized_name", "ncbi_taxid"],
        )
        return prepare_candidates(
            evidence, pd.DataFrame([candidate]), taxonomy, names, {}
        ), taxonomy

    def extend(self, candidates, taxonomy):
        original = baseline_row(2, "Known original", "linked", "Known_original_ott2")
        unresolved = baseline_row(1, taxonomy.iloc[0].scientificName)
        baseline = pd.DataFrame([original, unresolved])
        baseline["tree_identity_verified"] = [True, False]
        baseline["ncbi_to_tree_link_verified"] = False
        baseline["original_join_status"] = "unsafe_crosswalk"
        cw = pd.DataFrame({"iucn_sis_id": [1, 2], "match_status": ["NO_GOAT_NCBI_CANDIDATE"] * 2})
        return extend_synonym_links(baseline, cw, taxonomy, candidates)

    def test_documented_genus_change_needs_three_higher_ranks_and_preserves_flags(self):
        candidates, taxonomy = self.prepare()
        result, audit = self.extend(candidates, taxonomy)
        self.assertTrue(result.set_index("iucn_sis_id").loc[1, "tree_identity_verified"])
        self.assertTrue(audit.iloc[0].documented_genus_change)
        self.assertEqual(audit.iloc[0].agreeing_ranks, "family,order,class")
        self.assertEqual(
            result.set_index("iucn_sis_id").loc[1, "join_method"], "documented_binomial_name_change"
        )
        self.assertFalse(result.ncbi_to_tree_link_verified.any())
        self.assertEqual(result.iloc[0].original_join_status, "unsafe_crosswalk")
        candidates["ott_class"] = None
        _, audit = self.extend(candidates, taxonomy)
        self.assertEqual(audit.iloc[0].reason, "insufficient_lineage_evidence")

    def test_different_epithets_and_other_assessed_species_are_review_only(self):
        for kwargs, reason in [
            ({"accepted": "Oldgenus major"}, "species_concept_synonym_needs_review"),
            ({"other_taxa": ["Oldgenus minor"]}, "different_assessed_iucn_species_at_target"),
        ]:
            candidates, taxonomy = self.prepare(**kwargs)
            result, audit = self.extend(candidates, taxonomy)
            self.assertEqual(audit.iloc[0].reason, reason)
            self.assertFalse(result.set_index("iucn_sis_id").loc[1, "tree_identity_verified"])

    def test_ncbi_contradictions_and_ambiguity_are_withheld(self):
        for ids, reason in [
            (["100"], "current_ncbi_identity_conflict"),
            (["99", "100"], "ambiguous_current_ncbi_name"),
        ]:
            candidates, taxonomy = self.prepare(ncbi=ids)
            _, audit = self.extend(candidates, taxonomy)
            self.assertEqual(audit.iloc[0].reason, reason)

    def test_alias_cannot_waive_family_conflict(self):
        candidates, taxonomy = self.prepare()
        candidates["ott_family"] = "Otheridae"
        _, audit = self.extend(candidates, taxonomy)
        self.assertEqual(audit.iloc[0].reason, "lineage_conflict")
        self.assertEqual(audit.iloc[0].conflicting_ranks, "family")

    def test_missing_block_reason_in_string_column_is_not_a_rejection(self):
        candidates, taxonomy = self.prepare()
        good = candidates.iloc[0].to_dict()
        unrelated = {
            **good,
            "normalized_name": "Other major",
            "candidate_block_reason": "species_concept_synonym_needs_review",
        }
        # Pandas string columns represent the eligible row's missing reason
        # as NaN. It must not become a truthy, non-string rejection reason.
        result, audit = self.extend(pd.DataFrame([good, unrelated]), taxonomy)
        self.assertTrue(result.set_index("iucn_sis_id").loc[1, "tree_identity_verified"])
        self.assertEqual(audit.iloc[0].reason, "eligible")

    def test_all_evidence_routes_retain_competing_leaves(self):
        candidates, taxonomy = self.prepare()
        competitor = candidates.copy()
        competitor["tree_tip"] = "Other_minor_ott11"
        competitor["ott_id"] = "11"
        # Even a blocked alternate is ambiguity evidence; do not hide it.
        competitor["candidate_block_reason"] = "current_ncbi_identity_conflict"
        _, audit = self.extend(pd.concat([candidates, competitor]), taxonomy)
        self.assertTrue(audit.reason.eq("ambiguous_exact_tree_name").all())

    def test_an_ott_homonym_outside_dated_tree_still_blocks_identity(self):
        candidates, taxonomy = self.prepare()
        row = candidates.iloc[0]
        evidence = pd.DataFrame(
            [
                dict(
                    normalized_name=row.normalized_name,
                    tree_tip=row.tree_tip,
                    identity_basis="ott_synonym",
                    alias_sourceinfo="gbif:123",
                )
            ]
        )
        names = pd.DataFrame(columns=["normalized_name", "ncbi_taxid"])
        checked = prepare_candidates(
            evidence, candidates, taxonomy, names, {}, {row.normalized_name: {"10", "20"}}
        )
        _, audit = self.extend(checked, taxonomy)
        self.assertEqual(audit.iloc[0].reason, "ambiguous_ott_species_name")

    def test_synonym_index_has_source_and_output_integrity_checks(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "ott.tgz"
            output = Path(directory) / "synonyms.parquet"
            raw = b"name\t|\tuid\t|\ttype\t|\tuniqname\t|\tsourceinfo\t|\nNewgenus minor\t|\t10\t|\t\t|\t\t|\tncbi:99\t|\n"
            with tarfile.open(archive, "w:gz") as tar:
                member = tarfile.TarInfo("ott/synonyms.tsv")
                member.size = len(raw)
                tar.addfile(member, io.BytesIO(raw))
            build_synonym_index(archive, output)
            frame = pd.read_parquet(output)
            self.assertEqual(frame.iloc[0].normalized_name, "newgenus minor")
            self.assertEqual(frame.iloc[0].alias_sourceinfo, "ncbi:99")
            self.assertEqual(build_synonym_index(archive, output), output)
            output.write_bytes(b"changed")
            with self.assertRaisesRegex(ValueError, "checksum changed"):
                build_synonym_index(archive, output)
