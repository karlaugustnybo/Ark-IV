import json
import tempfile
import unittest
from pathlib import Path

import duckdb

from ark_pipeline.builders.iucn_metadata import (
    apply_iucn_metadata,
    collect_iucn_metadata,
    ecosystem_evidence,
    evidence_identity,
)
from ark_pipeline.cli.reconcile_species_metadata import reconcile


def assessment(sid=1, aid=10, category="Vulnerable", systems=None):
    return {
        "sis_taxon_id": sid,
        "assessment_id": aid,
        "latest": True,
        "taxon": {"sis_id": sid, "scientific_name": "Verified species", "family_name": "FAMILY"},
        "scopes": [{"code": "1"}],
        "red_list_category": {"description": {"en": category}},
        "systems": systems or [],
        "habitats": [],
    }


class IucnMetadataTests(unittest.TestCase):
    def save(self, root, endpoint, body):
        path = root / (endpoint + ".json")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {"release": "2026-1", "endpoint": endpoint, "status": "retrieved", "body": body}
            )
        )

    def prepare(self, root):
        (root / "report.json").write_text(json.dumps({"status": "retrieved", "release": "2026-1"}))
        self.save(
            root,
            "taxa/sis/1",
            {
                "taxon": {"sis_id": 1},
                "assessments": [
                    {
                        "sis_taxon_id": 1,
                        "assessment_id": 10,
                        "latest": True,
                        "scopes": [{"code": "1"}],
                    }
                ],
            },
        )
        self.save(
            root,
            "assessment/10",
            assessment(systems=[{"description": {"en": "Freshwater (=Inland waters)"}}]),
        )

    def test_habitats_need_positive_suitability_and_unambiguous_realm(self):
        for code, suitability in [
            ("17", "Suitable"),
            ("1_6", "Unsuitable"),
            ("15_1", "Suitable"),
            ("7", "Suitable"),
        ]:
            self.assertEqual(
                ecosystem_evidence({"habitats": [{"code": code, "suitability": suitability}]})[0],
                [],
            )
        self.assertEqual(
            ecosystem_evidence({"habitats": [{"code": "1_6", "suitability": "Suitable"}]}),
            (["Terrestrial"], "suitable_terrestrial_habitat"),
        )

    def test_reviewed_synthetic_evidence_requires_exact_ids_text_and_provenance(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = root / "private-evidence.json"
            rule = {"sis_id": "1", "assessment_id": "10", "systems": ["Terrestrial"],
                    "habitat_text": "Synthetic fixture ecology account."}
            path.write_text(json.dumps({"entries": [rule]}))
            body = {"sis_taxon_id": 1, "assessment_id": 10,
                    "documentation": {"habitats": rule["habitat_text"]}}
            self.assertEqual(ecosystem_evidence(body, reviewed_evidence_path=path),
                             (["Terrestrial"], "reviewed_assessment_habitat_account"))
            for change in ({"assessment_id": 11}, {"sis_taxon_id": 2},
                           {"documentation": {"habitats": "Changed synthetic account."}}):
                self.assertEqual(ecosystem_evidence({**body, **change}, reviewed_evidence_path=path)[0], [])
            rule.update(external_url="https://example.org/synthetic-ecology",
                        taxon_name="Verified species", taxon_authority="Fixture",
                        external_iucn_assessment_url="https://www.iucnredlist.org/species/1/10",
                        external_snapshot_sha256="a" * 64, retrieved_at="2099-01-01")
            path.write_text(json.dumps({"entries": [rule]}))
            body["taxon"] = {"scientific_name": rule["taxon_name"], "authority": rule["taxon_authority"]}
            self.assertEqual(ecosystem_evidence(body, reviewed_evidence_path=path)[1], "reviewed_external_taxon_ecology")
            body["taxon"]["authority"] = "Changed fixture"
            self.assertEqual(ecosystem_evidence(body, reviewed_evidence_path=path)[0], [])
            missing = root / "absent.json"
            self.assertEqual(ecosystem_evidence(body, reviewed_evidence_path=missing), ([], "no_affirmative_evidence"))
            from unittest.mock import patch
            with patch("ark_pipeline.builders.iucn_metadata.DEFAULT_EVIDENCE_PATH", path):
                self.assertEqual(evidence_identity(), evidence_identity(path))
            self.assertIsNone(evidence_identity(missing)["sha256"])

    def test_reviewed_private_intake_and_source_change_are_checked(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            self.prepare(root)
            body = assessment()
            body["documentation"] = {"habitats": "Synthetic fixture habitat."}
            self.save(root, "assessment/10", body)
            path = root / "private-evidence.json"
            path.write_text(json.dumps({"entries": [{"sis_id": "1", "assessment_id": "10", "systems": ["Terrestrial"], "habitat_text": body["documentation"]["habitats"]}]}))
            item = {"gbif_accepted_id": "1", "species_name": "Old label", "iucn_assessment_id": None, "redlist_category": "Not Assessed"}
            updates, report = collect_iucn_metadata(root, [item], reviewed_evidence_path=path)
            self.assertEqual(updates[0]["systems"], ["Terrestrial"])
            self.assertIn(evidence_identity(path), json.loads(updates[0]["provenance"]))
            self.assertEqual(report["reviewed_habitat_accounts"], 1)
            original = ecosystem_evidence

            def changed(body, **kwargs):
                result = original(body, **kwargs)
                self.save(root, "assessment/10", {**body, "latest": False})
                return result

            with patch("ark_pipeline.builders.iucn_metadata.ecosystem_evidence", side_effect=changed):
                with self.assertRaisesRegex(ValueError, "IUCN evidence changed"):
                    collect_iucn_metadata(root, [item], reviewed_evidence_path=path)

    def test_selected_evidence_change_during_read_is_rejected(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            self.prepare(root)
            evidence = root / "selected-reviewed.json"
            evidence.write_text('{"entries": []}')
            item = {"gbif_accepted_id": "1", "species_name": "Old label",
                    "iucn_assessment_id": None, "redlist_category": "Not Assessed"}
            original = ecosystem_evidence

            def changed(body, **kwargs):
                result = original(body, **kwargs)
                evidence.write_text('{"entries": [], "fixture": "changed"}')
                return result

            with patch("ark_pipeline.builders.iucn_metadata.ecosystem_evidence", side_effect=changed):
                with self.assertRaisesRegex(ValueError, "Reviewed ecosystem evidence changed"):
                    collect_iucn_metadata(root, [item], reviewed_evidence_path=evidence)

    def test_same_name_never_substitutes_a_different_id_or_ambiguous_assessment(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            self.prepare(root)
            row = {
                "gbif_accepted_id": "1",
                "iucn_assessment_id": None,
                "species_name": "Verified species",
                "redlist_category": "Not Assessed",
            }
            self.save(root, "assessment/10", assessment(sid=2))
            updates, report = collect_iucn_metadata(root, [row])
            self.assertEqual(updates, [])
            self.assertEqual(
                report["rejected"][0]["reason"], "assessment_identity_or_scope_mismatch"
            )
            self.save(
                root,
                "taxa/sis/1",
                {
                    "taxon": {"sis_id": 1},
                    "assessments": [
                        {
                            "sis_taxon_id": 1,
                            "assessment_id": aid,
                            "latest": True,
                            "scopes": [{"code": "1"}],
                        }
                        for aid in [10, 11]
                    ],
                },
            )
            self.assertEqual(collect_iucn_metadata(root, [row])[0], [])

    def test_pinned_assessment_is_not_replaced_by_latest_category(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            self.prepare(root)
            historical = assessment(aid=9, category="Least Concern")
            historical["latest"] = False
            self.save(root, "assessment/9", historical)
            updates, report = collect_iucn_metadata(
                root,
                [
                    {
                        "gbif_accepted_id": "1",
                        "species_name": "Verified species",
                        "iucn_assessment_id": "9",
                        "redlist_category": "Least Concern",
                    }
                ],
            )
            self.assertEqual(updates[0]["redlist_category"], "Least Concern")
            self.assertEqual(updates[0]["iucn_assessment_id"], "9")
            self.assertEqual(report["historical_pinned_assessments"], 1)

    def test_existing_legacy_lc_normalization_is_preserved(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            self.prepare(root)
            self.save(root, "assessment/10", assessment(category="Lower Risk/least concern"))
            updates, report = collect_iucn_metadata(
                root,
                [
                    {
                        "gbif_accepted_id": "1",
                        "species_name": "Verified species",
                        "iucn_assessment_id": "10",
                        "redlist_category": "Least Concern",
                    }
                ],
            )
            self.assertEqual(updates[0]["redlist_category"], "Least Concern")
            self.assertEqual(report["rejected"], [])

    def test_enrichment_preserves_identity_and_dna_and_deduplicates_realms(self):
        with tempfile.TemporaryDirectory() as folder, duckdb.connect() as connection:
            root = Path(folder)
            self.prepare(root)
            connection.execute(
                "CREATE TABLE species AS SELECT '1' AS gbif_accepted_id,'Old label' AS species_name,NULL::VARCHAR AS iucn_assessment_id,'Not Assessed' AS redlist_category,'UNKNOWN' AS family,'spatial_only' AS metadata_status,false AS has_dna_species_level"
            )
            connection.execute(
                "CREATE TABLE species_systems AS SELECT '1' AS gbif_accepted_id,'Freshwater' AS system"
            )
            report = apply_iucn_metadata(connection, root)
            self.assertEqual(report["spatial_only_assessments_recovered"], 1)
            self.assertEqual(
                connection.execute(
                    "SELECT gbif_accepted_id,species_name,iucn_assessment_id,redlist_category,has_dna_species_level,metadata_status FROM species"
                ).fetchone(),
                ("1", "Verified species", "10", "Vulnerable", False, "assessed"),
            )
            self.assertEqual(
                connection.execute("SELECT count(*) FROM species_systems").fetchone()[0], 1
            )

    def test_incomplete_intake_cannot_be_used_by_pipeline(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "report.json").write_text(
                json.dumps({"status": "fetching", "release": "2026-1"})
            )
            with self.assertRaisesRegex(ValueError, "incomplete"):
                collect_iucn_metadata(root, [])

    def test_wrong_release_cannot_enrich_newer_spatial_data(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            self.prepare(root)
            with self.assertRaisesRegex(ValueError, "spatial source release"):
                collect_iucn_metadata(root, [], expected_release="2026-2")

    def test_metadata_only_repair_writes_a_validated_separate_dimension(self):
        with tempfile.TemporaryDirectory() as folder, duckdb.connect() as connection:
            root = Path(folder)
            intake = root / "intake"
            intake.mkdir()
            self.prepare(intake)
            source = root / "source"
            source.mkdir()
            connection.execute(
                "CREATE TABLE species AS SELECT '1' AS gbif_accepted_id,'Old label' AS species_name,NULL::VARCHAR AS iucn_assessment_id,'Not Assessed' AS redlist_category,'UNKNOWN' AS family,'spatial_only' AS metadata_status,false AS has_dna_species_level,false AS genus_has_dna,false AS family_has_dna,true AS goat_data_deficient"
            )
            connection.execute(
                "COPY species TO ? (FORMAT PARQUET)", [str(source / "species.parquet")]
            )
            connection.execute(
                "COPY (SELECT '1' AS gbif_accepted_id,'Terrestrial' AS system) TO ? (FORMAT PARQUET)",
                [str(source / "species_systems.parquet")],
            )
            output = root / "output"
            result = reconcile(source, intake, output)
            self.assertTrue(all(result["checks"].values()))
            self.assertEqual(result["spatial_only_assessments_recovered"], 1)
            self.assertEqual(
                connection.execute(
                    "SELECT count(*) FROM read_parquet(?)",
                    [str(output / "species_systems.parquet")],
                ).fetchone()[0],
                2,
            )
            self.assertEqual(
                connection.execute(
                    "SELECT iucn_assessment_id FROM read_parquet(?)",
                    [str(source / "species.parquet")],
                ).fetchone()[0],
                None,
            )
