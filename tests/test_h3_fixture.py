from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pyarrow as pa
import pyarrow.parquet as pq
import shapely
from shapely.geometry import box

from ark_pipeline.cli.h3_cover_pilot import run as cover_sample
from ark_pipeline.cli.h3_fixture_partitions import run as produce
from ark_pipeline.cli.h3_fixture_validate import run as validate
from ark_pipeline.cli.h3_tree_pilot import run as compare_base

PROFILE = Path(__file__).resolve().parents[1] / "config/spatial_semantics_any_touch_v2.toml"


class H3FixtureTests(unittest.TestCase):
    def fixtures(self, root: Path) -> tuple[Path, Path, Path]:
        fixture, species, systems = (root / f"{name}.parquet" for name in ("fixture", "species", "systems"))
        rows = []
        for sample, identifier, logical in ((1, 101, "synthetic-a"), (2, 101, "synthetic-b"), (3, 202, "synthetic-c")):
            rows.append({
                "sample_id": sample, "logical_name": logical, "source_sha256": "a" * 64,
                "source_layer": "range", "source_row": 0, "iucn_sis_id": identifier,
                "presence": 1, "origin": 1, "seasonal": 1, "size_bin": 1,
                "geometry_wkb": shapely.to_wkb(box(12.4, 55.5, 12.43 if identifier == 101 else 12.44, 55.53)),
            })
        pq.write_table(pa.Table.from_pylist(rows), fixture)
        pq.write_table(pa.Table.from_pylist([
            {"gbif_accepted_id": "101", "redlist_category": "Endangered",
             "has_dna_species_level": True, "genus_has_dna": True,
             "family_has_dna": True, "goat_data_deficient": False},
            {"gbif_accepted_id": "202", "redlist_category": "Data Deficient",
             "has_dna_species_level": False, "genus_has_dna": None,
             "family_has_dna": True, "goat_data_deficient": True},
        ]), species)
        pq.write_table(pa.Table.from_pylist([
            {"gbif_accepted_id": "101", "system": "Terrestrial"},
            {"gbif_accepted_id": "202", "system": "Marine"},
            {"gbif_accepted_id": "202", "system": "Freshwater"},
        ]), systems)
        return fixture, species, systems

    def test_complete_comparison_resume_and_partial_generation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixture, species, systems = self.fixtures(root)
            source, output = root / "source", root / "validation"
            report = produce(fixture, source, PROFILE, chunk_size=2)
            self.assertEqual(report["species_count"], 2)
            self.assertEqual(report["cross_source_species"], 1)
            with patch("ark_pipeline.cli.h3_fixture_partitions.exact_intersecting_cells_native", side_effect=AssertionError("recomputed")):
                produce(fixture, source, PROFILE, chunk_size=2)
            result = validate(source, species, systems, output)
            self.assertTrue(result["complete_fixture"])
            self.assertGreater(result["fine_cells"], 1)
            self.assertEqual(result["fine_cells"], result["metric_rows"])
            self.assertTrue((output / "pilot_generation.json").is_file())
            with patch("ark_pipeline.cli.h3_fixture_validate.aggregate_cover_base", side_effect=AssertionError("recomputed")):
                self.assertTrue(validate(source, species, systems, output)["complete_fixture"])
            partial = root / "partial"
            base = int(next(iter(report["by_base"])))
            self.assertFalse(validate(source, species, systems, partial, base_cell=base)["complete_fixture"])
            self.assertFalse((partial / "pilot_generation.json").exists())
            with self.assertRaisesRegex(ValueError, "dictionary"):
                produce(fixture, source, PROFILE, chunk_size=1)

    def test_corruption_unreceipted_inputs_and_failed_generation_are_refused(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixture, species, systems = self.fixtures(root)
            source = root / "source"
            produce(fixture, source, PROFILE, chunk_size=2)
            failed = root / "failed"
            with patch("ark_pipeline.cli.h3_fixture_validate.aggregate_cover_base", side_effect=RuntimeError("interrupted")):
                with self.assertRaisesRegex(RuntimeError, "interrupted"):
                    validate(source, species, systems, failed)
            self.assertFalse((failed / "pilot_generation.json").exists())
            with self.assertRaisesRegex(ValueError, "incomplete base"):
                validate(source, species, systems, failed)
            extra = source / "chunk_0000" / "extra.parquet"
            pq.write_table(pa.table({"unexpected": [1]}), extra)
            with self.assertRaisesRegex(ValueError, "inventory"):
                validate(source, species, systems, root / "extra-output")
            # Repairing source artifacts is intentionally not automatic; use a fresh generation.
            fresh = root / "fresh"
            produce(fixture, fresh, PROFILE, chunk_size=2)
            pair = next(fresh.glob("chunk_*/*_pairs.parquet"))
            pair.write_bytes(b"damaged synthetic partition")
            with self.assertRaisesRegex(ValueError, "damaged chunk"):
                produce(fixture, fresh, PROFILE, chunk_size=2)
            with self.assertRaisesRegex(ValueError, "chunk file differs"):
                validate(fresh, species, systems, root / "damaged-output")

    def test_single_base_comparison_checks_both_orders_and_forced_duplicate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixture, species, systems = self.fixtures(root)
            a, b = root / "sample-a", root / "sample-b"
            first = cover_sample(fixture, 1, a, PROFILE, None)
            cover_sample(fixture, 3, b, PROFILE, first["base_cell"])
            for order in ("baseline", "candidate"):
                result = compare_base(
                    [a, b], species, systems, root / order,
                    base_cell=first["base_cell"], duplicate_sample=1,
                    first=order, memory_limit="512MB",
                )
                self.assertGreater(result["compared_rows"]["fine"], 1)
                self.assertEqual(result["compared_rows"]["fine"], result["compared_rows"]["metrics"])
                self.assertGreater(result["summaries"]["baseline"]["fine"]["input_relationships"],
                                   result["summaries"]["candidate"]["fine_relationships"])

    def test_failed_receipt_is_not_reused(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixture, _, _ = self.fixtures(root)
            source = root / "source"
            produce(fixture, source, PROFILE, chunk_size=2)
            receipt_path = source / "chunk_0000" / "receipt.json"
            receipt = json.loads(receipt_path.read_text())
            receipt["status"] = "failed"
            receipt_path.write_text(json.dumps(receipt))
            with self.assertRaisesRegex(ValueError, "stale chunk"):
                produce(fixture, source, PROFILE, chunk_size=2)

    def test_producer_reuse_preserves_validated_identity_and_original_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixture, species, systems = self.fixtures(root)
            source, output = root / "source", root / "validation"
            original = produce(fixture, source, PROFILE, chunk_size=2)
            validate(source, species, systems, output)
            report_path = source / "producer_report.json"
            report_bytes = report_path.read_bytes()
            with patch("ark_pipeline.cli.h3_fixture_partitions.runtime_identity", return_value={"different": "reuse runtime"}):
                reused = produce(fixture, source, PROFILE, chunk_size=2)
            self.assertEqual(reused, original)
            self.assertEqual(report_path.read_bytes(), report_bytes)
            with patch("ark_pipeline.cli.h3_fixture_validate.aggregate_cover_base", side_effect=AssertionError("recomputed")):
                self.assertTrue(validate(source, species, systems, output)["complete_fixture"])
            table = pq.read_table(fixture)
            rows = table.to_pylist()
            rows[0]["source_sha256"] = "b" * 64
            pq.write_table(pa.Table.from_pylist(rows, schema=table.schema), fixture)
            with self.assertRaisesRegex(ValueError, "dictionary"):
                produce(fixture, source, PROFILE, chunk_size=2)
            self.assertEqual(report_path.read_bytes(), report_bytes)

    def test_completed_generation_survives_partial_and_failed_reuse(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixture, species, systems = self.fixtures(root)
            source, output = root / "source", root / "validation"
            producer = produce(fixture, source, PROFILE, chunk_size=2)
            base = int(next(iter(producer["by_base"])))
            partial = validate(source, species, systems, output, base_cell=base)
            self.assertFalse(partial["complete_fixture"])
            self.assertFalse((output / "pilot_generation.json").exists())
            self.assertTrue(validate(source, species, systems, output)["complete_fixture"])
            before = {path: path.read_bytes() for path in output.rglob("*") if path.is_file()}
            with self.assertRaisesRegex(ValueError, "partial.*completed"):
                validate(source, species, systems, output, base_cell=base)
            self.assertEqual({path: path.read_bytes() for path in output.rglob("*") if path.is_file()}, before)
            with patch("ark_pipeline.cli.h3_fixture_validate.prepare_species", side_effect=RuntimeError("interrupted")):
                with self.assertRaisesRegex(RuntimeError, "interrupted"):
                    validate(source, species, systems, output)
            self.assertEqual({path: path.read_bytes() for path in output.rglob("*") if path.is_file()}, before)
            rows = pq.read_table(species).to_pylist()
            rows[0]["has_dna_species_level"] = False
            pq.write_table(pa.Table.from_pylist(rows), species)
            with self.assertRaisesRegex(ValueError, "stale base receipt"):
                validate(source, species, systems, output)
            self.assertEqual({path: path.read_bytes() for path in output.rglob("*") if path.is_file()}, before)

    def test_completed_producer_refuses_modified_receipt_without_replacing_report(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixture, species, systems = self.fixtures(root)
            source = root / "source"
            produce(fixture, source, PROFILE, chunk_size=2)
            report_path = source / "producer_report.json"
            before = report_path.read_bytes()
            receipt_path = source / "chunk_0000" / "receipt.json"
            receipt = json.loads(receipt_path.read_text())
            receipt["kernel_seconds"] += 1
            receipt_path.write_text(json.dumps(receipt))
            with self.assertRaisesRegex(ValueError, "completed producer report"):
                produce(fixture, source, PROFILE, chunk_size=2)
            self.assertEqual(report_path.read_bytes(), before)
            output = root / "validation"
            with self.assertRaisesRegex(ValueError, "chunk receipt hash differs"):
                validate(source, species, systems, output)
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
