from __future__ import annotations

import unittest

import pandas as pd

from ark_pipeline.edge_link_extension import extend_iucn_links


def baseline_row(sis, name, status="unsafe_crosswalk", tip=None, safe=False, category="LC"):
    return dict(
        iucn_sis_id=sis,
        iucn_scientific_name=name,
        assessment_sis_id=str(sis),
        scopes="Global",
        join_status=status,
        tree_tip=tip,
        join_method="ncbi_identifier" if status == "linked" else None,
        safe_for_automatic_species_trait_transfer=safe,
        redlist_category=category,
    )


def tax_row(sis, name):
    return dict(
        internalTaxonId=str(sis),
        scientificName=name,
        genusName=name.split()[0],
        familyName="Testidae",
        orderName="Testales",
        className="Testopsida",
        infraType=None,
        subpopulationName=None,
    )


def tip(name, identifier, **changes):
    return dict(
        tree_tip=name.replace(" ", "_") + "_ott" + str(identifier),
        ott_id=str(identifier),
        normalized_name=name.lower(),
        ott_genus=name.split()[0],
        ott_family="Testidae",
        ott_order="Testales",
        ott_class="Testopsida",
        label_occurrences=1,
        **changes,
    )


class IndependentTreeIdentityTests(unittest.TestCase):
    def run_links(self, rows, taxa, candidates, status=None):
        cw = pd.DataFrame(
            {
                "iucn_sis_id": [r["iucn_sis_id"] for r in rows],
                "match_status": status or ["NO_GOAT_NCBI_CANDIDATE"] * len(rows),
            }
        )
        return extend_iucn_links(
            pd.DataFrame(rows), cw, pd.DataFrame(taxa), pd.DataFrame(candidates)
        )

    def test_iucn_link_is_independent_of_genomic_transfer_and_preserves_baseline(self):
        original = baseline_row(1, "Test original", "linked", "original_ott1", True)
        missing = baseline_row(2, "Test new")
        result, audit = self.run_links(
            [original, missing],
            [tax_row(1, "Test original"), tax_row(2, "Test new")],
            [tip("Test new", 2)],
        )
        accepted = result.set_index("iucn_sis_id")
        self.assertEqual(accepted.loc[1, "tree_tip"], "original_ott1")
        self.assertTrue(accepted.loc[1, "ncbi_to_tree_link_verified"])
        self.assertTrue(accepted.loc[2, "tree_identity_verified"])
        self.assertFalse(accepted.loc[2, "ncbi_to_tree_link_verified"])
        self.assertFalse(accepted.loc[2, "safe_for_automatic_species_trait_transfer"])
        self.assertTrue(audit.iloc[0].accepted)

    def test_complete_baseline_produces_an_empty_typed_audit(self):
        row = baseline_row(1, "Test original", "linked", "original_ott1", True)
        result, audit = self.run_links(
            [row], [tax_row(1, "Test original")],
            pd.DataFrame(columns=["normalized_name", "tree_tip", "ott_id"]),
        )
        self.assertEqual(result.tree_tip.tolist(), ["original_ott1"])
        self.assertTrue(audit[audit.accepted].empty)
        self.assertIn("ancestor_membership_ranks", audit)

    def test_two_ranks_are_not_sufficient_and_conflicting_optional_rank_blocks(self):
        for mode in ["missing", "conflicting"]:
            candidate = tip("Test new", 2)
            candidate["ott_order"] = None
            candidate["ott_class"] = None if mode == "missing" else "Otheropsida"
            result, audit = self.run_links(
                [baseline_row(2, "Test new")], [tax_row(2, "Test new")], [candidate]
            )
            self.assertFalse(result.iloc[0].tree_identity_verified)
            self.assertEqual(
                audit.iloc[0].reason,
                "insufficient_lineage_evidence" if mode == "missing" else "lineage_conflict",
            )

    def test_homonyms_repeated_labels_and_reserved_tips_cannot_be_linked(self):
        for mode in ["homonym", "repeat", "reserved"]:
            candidate = tip("Test new", 2)
            rows = [baseline_row(2, "Test new")]
            taxa = [tax_row(2, "Test new")]
            candidates = [candidate]
            if mode == "homonym":
                candidates.append(tip("Test new", 3))
            elif mode == "repeat":
                candidate["label_occurrences"] = 2
            else:
                rows.append(baseline_row(1, "Test original", "linked", candidate["tree_tip"], True))
                taxa.append(tax_row(1, "Test original"))
            result, audit = self.run_links(rows, taxa, candidates)
            self.assertFalse(result.set_index("iucn_sis_id").loc[2, "tree_identity_verified"])
            self.assertTrue(
                audit.reason.isin(
                    [
                        "ambiguous_exact_tree_name",
                        "repeated_tree_label",
                        "reserved_existing_tree_tip",
                    ]
                ).all()
            )

    def test_competing_assessments_are_both_rejected(self):
        result, audit = self.run_links(
            [baseline_row(1, "Test new"), baseline_row(2, "Test new")],
            [tax_row(1, "Test new"), tax_row(2, "Test new")],
            [tip("Test new", 3)],
        )
        self.assertFalse(result.tree_identity_verified.any())
        self.assertTrue(audit.reason.eq("competing_iucn_species").all())

    def test_source_conflicts_regional_infraspecies_and_unsafe_concepts_are_rejected(self):
        for mode in ["assessment", "taxonomy", "regional", "infraspecies", "unsafe_matched"]:
            row = baseline_row(1, "Test new")
            tax = tax_row(1, "Test new")
            status = ["NO_GOAT_NCBI_CANDIDATE"]
            if mode == "assessment":
                row["assessment_sis_id"] = "999"
            if mode == "taxonomy":
                tax["scientificName"] = "Test other"
            if mode == "regional":
                row["scopes"] = "Europe"
            if mode == "infraspecies":
                tax["infraType"] = "subspecies"
            if mode == "unsafe_matched":
                status = ["MATCHED"]
            result, audit = self.run_links([row], [tax], [tip("Test new", 3)], status)
            self.assertFalse(result.tree_identity_verified.any())
            self.assertFalse(audit.accepted.any())

    def test_extinct_identity_is_retained_without_extant_score_flag(self):
        row = baseline_row(1, "Test new", category="EX")
        result, _ = self.run_links([row], [tax_row(1, "Test new")], [tip("Test new", 3)])
        self.assertTrue(result.iloc[0].tree_identity_verified)
        self.assertFalse(result.iloc[0].extant_edge_input_candidate)

    def test_exact_ancestor_membership_handles_class_rank_differences(self):
        candidate = tip("Test new", 3)
        candidate["ott_class"] = "NarrowerClass"
        candidate["ott_ancestor_names"] = [
            "Test",
            "Testidae",
            "Testales",
            "NarrowerClass",
            "Testopsida",
        ]
        result, audit = self.run_links(
            [baseline_row(1, "Test new")], [tax_row(1, "Test new")], [candidate]
        )
        self.assertTrue(result.iloc[0].tree_identity_verified)
        self.assertEqual(audit.iloc[0].ancestor_membership_ranks, "class")
        # Higher clade membership cannot waive a conflicting nearest genus.
        candidate["ott_genus"] = "Other"
        result, audit = self.run_links(
            [baseline_row(1, "Test new")], [tax_row(1, "Test new")], [candidate]
        )
        self.assertFalse(result.iloc[0].tree_identity_verified)
        self.assertEqual(audit.iloc[0].reason, "lineage_conflict")


if __name__ == "__main__":
    unittest.main()
