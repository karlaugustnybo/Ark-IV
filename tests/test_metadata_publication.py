"""Synthetic public-entrypoint refresh and provenance failure checks."""

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import duckdb

from ark_pipeline.builders import species_metadata
from ark_pipeline.cli import reconcile_species_metadata as repair
from ark_pipeline.cli import serving_metadata
from tests import test_iucn_metadata as detail_fixtures
from tests import test_iucn_reconcile_stage as stage_fixtures
from tests import test_refresh_crosswalk as crosswalk_fixtures

NAMES = ("species.parquet", "species_systems.parquet", "build-report.json")


def contents(folder):
    return {name: (folder / name).read_bytes() for name in NAMES}


class MetadataPublicationTests(unittest.TestCase):
    def repair_fixture(self, root):
        source = stage_fixtures.ReconcileStageTests().fixture(root)
        details = root / "details"
        details.mkdir()
        detail_fixtures.IucnMetadataTests().prepare(details)
        output = root / "output"
        shutil.copytree(source, output)
        return source, details, output

    def test_standalone_stages_report_and_rolls_back_each_replacement(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, details, output = self.repair_fixture(Path(tmp))
            baseline = contents(output)
            original = Path.replace
            for failed in NAMES:
                with self.subTest(failed=failed):
                    def fail(path, target):
                        if Path(target) == output / failed:
                            raise OSError("selected replacement fault")
                        return original(path, target)
                    with patch.object(Path, "replace", fail):
                        with self.assertRaisesRegex(OSError, "replacement fault"):
                            repair.reconcile(source, details, output)
                    self.assertEqual(contents(output), baseline)
                    self.assertEqual(contents(source), baseline)
            with patch.object(repair, "atomic_json", side_effect=OSError("report fault")):
                with self.assertRaisesRegex(OSError, "report fault"):
                    repair.reconcile(source, details, output)
            self.assertEqual(contents(output), baseline)

    def test_standalone_pins_present_absent_and_detail_inputs(self):
        for mutation in ("reviewed", "absent", "detail"):
            for boundary in ("collected", "first_replaced", "last_replaced"):
                with self.subTest(mutation=mutation, boundary=boundary), tempfile.TemporaryDirectory() as tmp:
                    root = Path(tmp)
                    source, details, output = self.repair_fixture(root)
                    evidence = root / "reviewed.json"
                    if mutation != "absent":
                        evidence.write_text('{"entries": []}')
                    baseline = contents(output)
                    original_apply = repair.apply_iucn_metadata
                    original_replace = Path.replace
                    def mutate():
                        selected = details / "assessment/10.json" if mutation == "detail" else evidence
                        selected.write_text(selected.read_text() + " " if selected.exists() else '{"entries": []}')
                    def apply(*args, **kwargs):
                        result = original_apply(*args, **kwargs)
                        if boundary == "collected":
                            mutate()
                        return result
                    def replace(path, target):
                        result = original_replace(path, target)
                        selected = "species.parquet" if boundary == "first_replaced" else "build-report.json"
                        if boundary != "collected" and Path(target) == output / selected:
                            mutate()
                        return result
                    with patch.object(repair, "apply_iucn_metadata", apply), patch.object(Path, "replace", replace):
                        with self.assertRaisesRegex(ValueError, "evidence changed"):
                            repair.reconcile(source, details, output, reviewed_evidence_path=evidence)
                    self.assertEqual(contents(output), baseline)
                    self.assertEqual(contents(source), baseline)

    def serving_fixture(self, root):
        crosswalk_fixtures.fixture(root)
        crosswalk = crosswalk_fixtures.RefreshCrosswalkTests().run_matcher(root)
        legacy = root / "legacy"
        (legacy / "IUCN_Red_List").mkdir(parents=True)
        (legacy / "TOL").mkdir()
        shutil.copyfile(root / "sources/assessments.csv", legacy / "IUCN_Red_List/assessments.csv")
        for name in ("tol_species_all_ranks.tsv", "names.dmp", "nodes.dmp"):
            shutil.copyfile(root / "sources" / name, legacy / "TOL" / name)
        h3 = root / "h3"
        h3.mkdir()
        output = root / "output"
        return crosswalk, legacy, h3, output

    def write_h3(self, folder, ids):
        with duckdb.connect() as db:
            db.execute("CREATE TABLE h3 AS SELECT 1::UBIGINT h3_cell, ?::BIGINT[] species_ids", [ids])
            db.execute("COPY h3 TO ? (FORMAT PARQUET)",
                       [str(folder / "h3_res3_species_global_merged.parquet")])

    def test_actual_serving_cli_legacy_and_failed_refresh(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            crosswalk, legacy, h3, output = self.serving_fixture(root)
            common = ["--root", str(legacy), "--h3-root", str(h3), "--crosswalk", str(crosswalk), "--output-dir", str(output)]
            for ids in ([101], [101, 999]):
                self.write_h3(h3, ids)
                self.assertEqual(serving_metadata.main(common + ["--iucn-metadata-mode", "off"]), 0)
            self.assertFalse((legacy / "acquisition/current.json").exists())
            with duckdb.connect() as db:
                self.assertEqual(db.execute("SELECT species_name FROM read_parquet(?) WHERE gbif_accepted_id='999'", [str(output / "species.parquet")]).fetchone(), ("IUCN taxon 999",))
            baseline = contents(output)
            self.assertEqual(serving_metadata.main(common + ["--iucn-metadata-mode", "cache-only", "--iucn-details-dir", str(root / "missing")]), 1)
            self.assertEqual(contents(output), baseline)
            from ark_pipeline.cli import iucn_reconcile_stage
            with patch.object(iucn_reconcile_stage, "reconcile", side_effect=ValueError("during reconciliation")), patch.object(iucn_reconcile_stage, "cache_covers", return_value=True):
                self.assertEqual(serving_metadata.main(common + ["--iucn-metadata-mode", "cache-only"]), 1)
            self.assertEqual(contents(output), baseline)
            original_json = __import__("ark_pipeline.runtime.provenance", fromlist=["atomic_json"]).atomic_json
            def final_report(path, report):
                if "iucn_reconciliation" in report:
                    raise OSError("final report fault")
                return original_json(path, report)
            with patch("ark_pipeline.runtime.provenance.atomic_json", final_report):
                self.assertEqual(serving_metadata.main(common + ["--iucn-metadata-mode", "off"]), 1)
            self.assertEqual(contents(output), baseline)
            original = Path.replace
            def fail(path, target):
                if Path(target) == output / "species_systems.parquet":
                    raise OSError("second publication fault")
                return original(path, target)
            with patch.object(Path, "replace", fail):
                self.assertEqual(serving_metadata.main(common + ["--iucn-metadata-mode", "off"]), 1)
            self.assertEqual(contents(output), baseline)

    def test_direct_builder_evidence_guard_and_report_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            crosswalk, legacy, h3, output = self.serving_fixture(root)
            self.write_h3(h3, [101, 999])
            kwargs = dict(crosswalk_path=crosswalk, assessments_path=legacy / "IUCN_Red_List/assessments.csv",
                          goat_species_path=legacy / "TOL/tol_species_all_ranks.tsv", h3_paths=[h3 / "h3_res3_species_global_merged.parquet"], output_dir=output, spatial_source_root=legacy)
            species_metadata.build_global_species(**kwargs)
            baseline = contents(output)
            details = root / "details"
            details.mkdir()
            detail_fixtures.IucnMetadataTests().prepare(details)
            evidence = root / "absent-reviewed.json"
            from ark_pipeline.builders import iucn_metadata
            original = iucn_metadata.apply_iucn_metadata
            def apply(*args, **kw):
                report = original(*args, **kw)
                evidence.write_text('{"entries": []}')
                return report
            with patch.object(iucn_metadata, "apply_iucn_metadata", apply):
                with self.assertRaisesRegex(ValueError, "evidence changed"):
                    species_metadata.build_global_species(**kwargs, iucn_details_dir=details, reviewed_evidence_path=evidence)
            self.assertEqual(contents(output), baseline)
            original_write = Path.write_text
            def write(path, *args, **kw):
                if path.name == "build-report.json" and path.parent.name.startswith(".metadata-build-"):
                    raise OSError("builder report fault")
                return original_write(path, *args, **kw)
            with patch.object(Path, "write_text", write):
                with self.assertRaisesRegex(OSError, "builder report fault"):
                    species_metadata.build_global_species(**kwargs)
            self.assertEqual(contents(output), baseline)
