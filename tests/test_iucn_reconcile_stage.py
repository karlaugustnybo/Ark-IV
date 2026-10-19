import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import duckdb

from ark_pipeline.cli.iucn_reconcile_stage import (
    cache_covers,
    include_synonym_candidates,
    reconcile,
    reconcile_stage,
    reconciliation_queue,
)
from tests import test_iucn_metadata as fixtures


class ReconcileStageTests(unittest.TestCase):
    def fixture(self, root):
        dimension = root / "species"
        dimension.mkdir()
        with duckdb.connect() as db:
            db.execute("""CREATE TABLE species AS SELECT '1' gbif_accepted_id,
                NULL::VARCHAR iucn_assessment_id, 'Verified species' species_name,
                'FAMILY' AS family, NULL::VARCHAR redlist_category,
                'spatial_only' metadata_status, false has_dna_species_level,
                false genus_has_dna, false family_has_dna, true goat_data_deficient""")
            db.execute("CREATE TABLE species_systems(gbif_accepted_id VARCHAR,system VARCHAR)")
            for name in ("species", "species_systems"):
                db.execute(
                    f"COPY {name} TO ? (FORMAT PARQUET)", [str(dimension / f"{name}.parquet")]
                )
        (dimension / "build-report.json").write_text(json.dumps({"inputs": {}, "species": 1}))
        return dimension

    def test_cache_reconciliation_is_shared_and_network_free(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dimension = self.fixture(root)
            cache = root / "details"
            cache.mkdir()
            fixtures.IucnMetadataTests().prepare(cache)
            with patch("ark_pipeline.cli.iucn_reconcile_stage.fetch_details") as fetch:
                report = reconcile_stage(root, dimension, {}, details_dir=cache, mode="cache-only")
            fetch.assert_not_called()
            self.assertEqual(report["ecosystem_gaps_after"], 0)
            self.assertEqual(report["spatial_only_after"], 0)
            self.assertTrue(all(report["checks"].values()))
            with duckdb.connect() as db:
                self.assertEqual(
                    db.execute(
                        "SELECT iucn_assessment_id FROM read_parquet(?)",
                        [str(dimension / "species.parquet")],
                    ).fetchone(),
                    ("10",),
                )
            self.assertEqual(
                json.loads((dimension / "build-report.json").read_text())[
                    "species_without_ecosystem_metadata"
                ],
                0,
            )

    def test_auto_intake_uses_exact_ids_and_rolls_back_failed_publication(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dimension = self.fixture(root)
            cache = root / "details"

            class Api:
                calls = []
                def get(self, endpoint):
                    self.calls.append(endpoint)
                    if endpoint == "information/red_list_version":
                        return {"red_list_version": "2026-1"}, {}
                    if endpoint == "taxa/sis/1":
                        return {"taxon": {"sis_id": 1}, "assessments": [{"sis_taxon_id": 1, "assessment_id": 10, "latest": True, "scopes": [{"code": "1"}]}]}, {}
                    if endpoint == "assessment/10":
                        return fixtures.assessment(systems=[{"description": {"en": "Terrestrial"}}]), {}
                    raise AssertionError(endpoint)

            api = Api()
            baseline = {name: (dimension / name).read_bytes() for name in ("species.parquet", "species_systems.parquet", "build-report.json")}
            original = Path.replace

            def fail_second(path, target):
                if path.name == "species_systems.parquet" and path.parent.name == "api-reconciled":
                    raise OSError("synthetic publication fault")
                return original(path, target)

            with patch("ark_pipeline.cli.iucn_reconcile_stage.read_iucn_token", return_value="synthetic"), patch("ark_pipeline.cli.iucn_reconcile_stage.IucnApi", return_value=api), patch.object(Path, "replace", fail_second):
                with self.assertRaisesRegex(OSError, "publication fault"):
                    reconcile_stage(root, dimension, {}, details_dir=cache)
            self.assertEqual({name: (dimension / name).read_bytes() for name in baseline}, baseline)
            self.assertIn("assessment/10", api.calls)
            with patch("ark_pipeline.cli.iucn_reconcile_stage.fetch_details") as fetch:
                result = reconcile_stage(root, dimension, {}, details_dir=cache, mode="cache-only")
            fetch.assert_not_called()
            self.assertEqual((result["species_count"], result["ecosystem_gaps_before"], result["ecosystem_gaps_after"]), (1, 1, 0))

    def test_selected_evidence_change_prevents_publication(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dimension = self.fixture(root)
            cache = root / "details"
            cache.mkdir()
            fixtures.IucnMetadataTests().prepare(cache)
            evidence = root / "selected-reviewed.json"
            evidence.write_text('{"entries": []}')
            baseline = {name: (dimension / name).read_bytes() for name in
                        ("species.parquet", "species_systems.parquet", "build-report.json")}

            def changed(*args, **kwargs):
                result = reconcile(*args, **kwargs)
                evidence.write_text('{"entries": [], "fixture": "changed"}')
                return result

            with patch("ark_pipeline.cli.iucn_reconcile_stage.reconcile", side_effect=changed):
                with self.assertRaisesRegex(ValueError, "Reviewed ecosystem evidence changed"):
                    reconcile_stage(root, dimension, {}, details_dir=cache,
                                    mode="cache-only", reviewed_evidence_path=evidence)
            self.assertEqual({name: (dimension / name).read_bytes() for name in baseline}, baseline)

    def test_cache_rejects_endpoint_release_and_incomplete_records(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp)
            fixtures.IucnMetadataTests().prepare(cache)
            path = cache / "assessment/10.json"
            saved = json.loads(path.read_text())
            path.write_text(json.dumps({**saved, "endpoint": "assessment/11"}))
            with self.assertRaisesRegex(ValueError, "identity mismatch"):
                cache_covers(cache, {"sis_ids": [1], "assessment_ids": []}, "2026-1")
            path.write_text(json.dumps({**saved, "status": "fetching"}))
            self.assertFalse(cache_covers(cache, {"sis_ids": [1], "assessment_ids": []}, "2026-1"))

    def test_missing_cache_requires_token_and_off_is_explicit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dimension = self.fixture(root)
            with patch("ark_pipeline.cli.iucn_reconcile_stage.read_iucn_token", return_value=None):
                with self.assertRaisesRegex(ValueError, "IUCN_REDLIST_API_KEY"):
                    reconcile_stage(root, dimension, {})
            self.assertEqual(reconcile_stage(root, dimension, {}, mode="off")["status"], "disabled")

    def test_queue_contains_both_identity_candidates_and_pinned_gap(self):
        with tempfile.TemporaryDirectory() as tmp:
            dimension = self.fixture(Path(tmp))
            with duckdb.connect() as db:
                db.execute(
                    "CREATE TABLE species AS SELECT * FROM read_parquet(?)",
                    [str(dimension / "species.parquet")],
                )
                db.execute(
                    "INSERT INTO species SELECT '2','20','Verified species','FAMILY','Least Concern','assessed',false,false,false,true"
                )
                db.execute(
                    "COPY species TO ? (FORMAT PARQUET)", [str(dimension / "species.parquet")]
                )
            self.assertEqual(
                reconciliation_queue(dimension), {"sis_ids": [1, 2], "assessment_ids": [20]}
            )

    def test_wrong_release_and_missing_latest_detail_cannot_reuse(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp)
            fixtures.IucnMetadataTests().prepare(cache)
            queue = {"sis_ids": [1], "assessment_ids": []}
            self.assertTrue(cache_covers(cache, queue, "2026-1"))
            with self.assertRaisesRegex(ValueError, "release differs"):
                cache_covers(cache, queue, "2027-1")
            (cache / "assessment/10.json").unlink()
            self.assertFalse(cache_covers(cache, queue, "2026-1"))

    def test_official_nested_synonym_selects_candidate_without_merging(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dimension = self.fixture(root)
            with duckdb.connect() as db:
                db.execute(
                    "CREATE TABLE species AS SELECT * FROM read_parquet(?)",
                    [str(dimension / "species.parquet")],
                )
                db.execute(
                    "INSERT INTO species SELECT '2','20','Old species','FAMILY','Least Concern','assessed',false,false,false,true"
                )
                db.execute(
                    "COPY species TO ? (FORMAT PARQUET)", [str(dimension / "species.parquet")]
                )
            cache = root / "details"
            fixtures.IucnMetadataTests().save(
                cache,
                "taxa/sis/1",
                {
                    "taxon": {
                        "sis_id": 1,
                        "synonyms": [{"genus_name": "Old", "species_name": "species"}],
                    }
                },
            )
            expanded = include_synonym_candidates(
                {"sis_ids": [1], "assessment_ids": []}, dimension, cache
            )
            self.assertEqual(expanded, {"sis_ids": [1, 2], "assessment_ids": [20]})


if __name__ == "__main__":
    unittest.main()
