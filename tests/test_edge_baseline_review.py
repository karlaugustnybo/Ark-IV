import tempfile
import unittest
from pathlib import Path

import pandas as pd

from ark_pipeline.cli.edge_baseline_review import relocated_source
from ark_pipeline.edge_baseline_review import apply_reviewed_baseline, review_baseline_links
from ark_pipeline.runtime.provenance import sha256


class BaselineReviewTests(unittest.TestCase):
    def test_relocated_review_input_keeps_original_identity_and_rejects_changed_content(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'checked-input'
            path.write_bytes(b'original checked input')
            source = {'path': '/old/workstation/input', 'sha256': sha256(path)}
            selected = relocated_source('crosswalk', source, {'crosswalk': str(path)})
            self.assertEqual(selected['sha256'], source['sha256'])
            self.assertEqual(source['path'], '/old/workstation/input')
            path.write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError, 'checksum changed'):
                relocated_source('crosswalk', source, {'crosswalk': str(path)})

    def fixture(self):
        baseline = pd.DataFrame(
            [
                dict(
                    taxon_id="1",
                    canonical_ncbi_taxid="1",
                    scientific_name="Genus alpha",
                    genus="Genus",
                    family="Family",
                    order="Order",
                    **{"class": "Mammalia"},
                    legacy_broad_evidence=True,
                    tree_tip=None,
                    tree_join_status="ncbi_not_in_mammal_tree",
                    tree_join_route=None,
                )
            ]
        )
        candidates = pd.DataFrame(
            [
                dict(
                    subject_ncbi_taxid="1",
                    tree_tip="Genus_alpha_ott10",
                    ott_id="10",
                    label_occurrences=1,
                    taxon_name="Genus alpha",
                    taxon_rank="species",
                    sourceinfo="gbif:10",
                    identity_basis="exact_ott_name",
                    queried_names="genus alpha",
                    ott_genus="Genus",
                    ott_family="Family",
                    ott_order="Different rank",
                    ott_class="Mammalia",
                    ott_ancestor_names=["Genus", "Family", "Order", "Mammalia"],
                )
            ]
        )
        targets = pd.DataFrame(
            [dict(subject_ncbi_taxid="1", ott_id="10", taxon_rank="species", flags="")]
        )
        return baseline, candidates, targets

    def review(self, baseline=None, candidates=None, targets=None, **kwargs):
        initial = self.fixture()
        return review_baseline_links(
            baseline if baseline is not None else initial[0],
            candidates if candidates is not None else initial[1],
            targets if targets is not None else initial[2],
            kwargs.get("names", {"1": {"genus alpha"}}),
            kwargs.get("ranks", {"1": "species"}),
            {"1": {"ncbi_genus": "Genus", "ncbi_family": "Family"}},
            kwargs.get("direct", {}),
            set(),
            {},
            {"Genus_alpha_ott10", "Genus_beta_ott20"},
            kwargs.get("internal", set()),
            ncbi_name_ids=kwargs.get("name_ids", {"genus alpha": {"1"}}),
            iucn_identities=kwargs.get("iucn", {}),
        )

    def test_unique_current_name_and_three_rank_membership_recovers(self):
        result, summary, evidence = self.review()
        self.assertTrue(summary.recovered.iloc[0])
        self.assertEqual(result.tree_tip.iloc[0], "Genus_alpha_ott10")
        self.assertEqual(evidence.agreeing_ranks.iloc[0], "genus,family,order,class")
        before = self.fixture()[0]
        applied = apply_reviewed_baseline(before, result, summary)
        self.assertEqual(applied.tree_tip.iloc[0], "Genus_alpha_ott10")
        self.assertTrue(applied.legacy_broad_evidence.equals(before.legacy_broad_evidence))

    def test_fully_linked_baseline_has_no_review_candidates(self):
        baseline, candidates, targets = self.fixture()
        baseline = baseline.assign(tree_tip="Genus_alpha_ott10", tree_join_status="linked")
        result, summary, evidence = self.review(baseline=baseline)
        self.assertTrue(result.equals(baseline))
        self.assertTrue(summary[summary.recovered].empty)
        self.assertTrue(evidence[evidence.accepted].empty)
        self.assertTrue(apply_reviewed_baseline(baseline, result, summary).equals(baseline))

    def test_preferred_name_cannot_resolve_multiple_merged_source_tips(self):
        result, summary, _ = self.review(direct={"1": {"Genus_alpha_ott10", "Genus_beta_ott20"}})
        self.assertFalse(summary.recovered.iloc[0])
        self.assertEqual(
            summary.review_reason.iloc[0], "multiple_tree_tips_for_current_ncbi_species"
        )
        self.assertTrue(result.tree_tip.isna().all())

    def test_current_ncbi_homonym_absent_from_tree_still_blocks_transfer(self):
        _, summary, _ = self.review(name_ids={"genus alpha": {"1", "999"}})
        self.assertFalse(summary.recovered.iloc[0])
        self.assertEqual(summary.review_reason.iloc[0], "ambiguous_current_ncbi_name")

    def test_unrepresented_full_ott_homonym_blocks_unique_dated_candidate(self):
        _, _, targets = self.fixture()
        targets = pd.concat([targets, targets.assign(ott_id="20")], ignore_index=True)
        _, summary, _ = self.review(targets=targets)
        self.assertEqual(summary.review_reason.iloc[0], "multiple_documented_species_concepts")

    def test_existing_leaf_and_conflicting_current_identifier_are_held(self):
        baseline, candidates, _ = self.fixture()
        occupied = baseline.assign(
            taxon_id="2",
            canonical_ncbi_taxid="2",
            scientific_name="Genus beta",
            tree_tip="Genus_alpha_ott10",
            tree_join_status="linked",
            tree_join_route="ncbi_source_identifier",
        )
        _, summary, _ = self.review(baseline=pd.concat([baseline, occupied], ignore_index=True))
        self.assertEqual(summary.review_reason.iloc[0], "reserved_baseline_tip")
        candidates["sourceinfo"] = "ncbi:999"
        _, summary, _ = self.review(candidates=candidates)
        self.assertEqual(summary.review_reason.iloc[0], "current_ncbi_tree_identity_conflict")

    def test_internal_nodes_subspecies_and_extinct_flags_are_explained(self):
        _, candidates, targets = self.fixture()
        empty = candidates.iloc[:0]
        _, summary, _ = self.review(candidates=empty, internal={"10"})
        self.assertEqual(summary.review_reason.iloc[0], "named_taxon_is_internal_tree_node")
        _, summary, _ = self.review(
            candidates=empty, targets=targets.assign(taxon_rank="subspecies")
        )
        self.assertEqual(summary.review_reason.iloc[0], "only_nonspecies_ott_name")
        _, summary, _ = self.review(
            candidates=empty, targets=targets.assign(flags="extinct_inherited,incertae_sedis")
        )
        self.assertEqual(summary.review_reason.iloc[0], "ott_extinct_flag_and_no_dated_leaf")

    def test_different_epithet_and_lineage_conflicts_do_not_transfer_dna(self):
        _, candidates, _ = self.fixture()
        _, summary, _ = self.review(
            candidates=candidates.assign(taxon_name="Genus beta", identity_basis="ott_alias")
        )
        self.assertEqual(summary.review_reason.iloc[0], "species_concept_alias_needs_review")
        _, summary, _ = self.review(candidates=candidates.assign(ott_family="Another family"))
        self.assertEqual(summary.review_reason.iloc[0], "tree_lineage_conflict")

    def test_stale_dna_evidence_and_unapproved_changes_are_rejected(self):
        result, summary, _ = self.review()
        before = self.fixture()[0]
        with self.assertRaisesRegex(ValueError, "fresh GoaT evidence"):
            apply_reviewed_baseline(before.assign(legacy_broad_evidence=False), result, summary)
        with self.assertRaisesRegex(ValueError, "without approval"):
            apply_reviewed_baseline(before, result, summary.assign(recovered=False))

    def test_nonqualifying_bridge_is_added_after_historical_review_validation(self):
        from ark_pipeline.cli.phylogenetic_gap import add_rule_independent_bridges
        before = self.fixture()[0].assign(legacy_broad_evidence=False)
        summary = pd.DataFrame({'taxon_id': ['1'], 'recovered': [False]})
        checked = apply_reviewed_baseline(before, before.copy(), summary)
        bridge = dict(canonical_ncbi_taxid='1', tree_tip='Genus_alpha_ott10',
                      tree_join_status='linked', tree_join_route='reviewed_iucn_species_bridge')
        result = add_rule_independent_bridges(checked, [bridge])
        self.assertEqual(result.tree_tip.iloc[0], bridge['tree_tip'])
        self.assertFalse(result.legacy_broad_evidence.iloc[0])
        self.assertIsNone(before.tree_tip.iloc[0])
        with self.assertRaisesRegex(ValueError, 'without approval'):
            apply_reviewed_baseline(result, before, summary)
        with self.assertRaisesRegex(ValueError, 'conflicts'):
            add_rule_independent_bridges(result, [{**bridge, 'tree_tip': 'another'}])

    def test_iucn_override_is_recorded_without_inventing_missing_leaf(self):
        _, candidates, targets = self.fixture()
        _, summary, _ = self.review(
            candidates=candidates.iloc[:0],
            targets=targets.assign(flags="extinct"),
            iucn={"1": {"iucn_category": "EN", "iucn_sis_id": 100, "iucn_assessment_id": "200"}},
        )
        self.assertFalse(summary.effective_extinct.iloc[0])
        self.assertTrue(summary.ott_extinction_overridden.iloc[0])
        self.assertFalse(summary.recovered.iloc[0])
        self.assertEqual(summary.review_reason.iloc[0], "iucn_status_override_tree_leaf_missing")

    def test_ott_extinction_flag_does_not_veto_verified_iucn_species_leaf(self):
        _, _, targets = self.fixture()
        _, summary, _ = self.review(
            targets=targets.assign(flags="extinct"), iucn={"1": {"iucn_category": "EN"}}
        )
        self.assertTrue(summary.recovered.iloc[0])
        self.assertTrue(summary.ott_extinction_overridden.iloc[0])


if __name__ == "__main__":
    unittest.main()
