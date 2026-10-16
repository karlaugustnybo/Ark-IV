from __future__ import annotations

import itertools
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import duckdb
import h3
import pyarrow as pa
import pyarrow.parquet as pq

from ark_pipeline.aggregation.metrics import METRIC_SCHEMA, aggregate_species_lists
from ark_pipeline.aggregation.species_lists import LIST_SCHEMA
from ark_pipeline.builders import fine_metrics
from ark_pipeline.builders.fine_metrics import aggregate_columns, aggregate_part


class ServingMetricsTests(unittest.TestCase):
    def test_native_matches_sql_for_all_traits_nulls_systems_and_batch_boundaries(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, duckdb.connect() as connection:
            root = Path(temporary)
            categories = [
                "Critically Endangered", "Endangered", "Vulnerable", "Near Threatened",
                "Data Deficient", "Least Concern", "Extinct", None,
            ]
            rows = []
            for identifier, traits in enumerate(itertools.product(
                categories, (True, False, None), (True, False, None),
                (True, False, None), (True, False, None), range(8),
            ), start=1):
                category, species, genus, family, deficient, mask = traits
                rows.append({
                    "gbif_accepted_id": str(identifier), "redlist_category": category,
                    "has_dna_species_level": species, "genus_has_dna": genus,
                    "family_has_dna": family, "goat_data_deficient": deficient,
                    "is_terrestrial": bool(mask & 1), "is_freshwater": bool(mask & 2),
                    "is_marine": bool(mask & 4),
                })
            # These must not alias numeric species 1 or 2 when the join becomes numeric.
            rows.extend({**rows[0], "gbif_accepted_id": value} for value in ("01", "1.5", "other"))
            connection.register("metadata", pa.Table.from_pylist(rows))
            connection.execute("CREATE TEMP TABLE species AS SELECT * FROM metadata")
            ids = list(range(1, len(rows) - 2))
            cells = sorted(h3.grid_disk(h3.latlng_to_cell(55.67, 12.56, 7), 1))
            source = root / "lists.parquet"
            target = root / "metrics.parquet"
            pq.write_table(pa.Table.from_pylist([
                {"h3_cell": h3.str_to_int(cell), "species_ids": values}
                # A cell above 65,535 relationships must not carry between categories.
                for cell, values in zip(cells, [ids * 13, ids[::2], [1], ids[1::2], [2, 4], ids, [3]])
            ], schema=LIST_SCHEMA), source, row_group_size=3)
            connection.execute(f"""
                CREATE TEMP TABLE expected AS
                WITH cells AS (
                    SELECT lower(to_hex(h3_cell)) AS h3_index,
                           cast(ids.species_id AS VARCHAR) AS gbif_accepted_id
                    FROM read_parquet(?), unnest(species_ids) AS ids(species_id)
                )
                SELECT h3_index, {', '.join(aggregate_columns())}
                FROM cells JOIN species USING (gbif_accepted_id) GROUP BY h3_index
            """, [str(source)])
            aggregate_species_lists(
                connection, source, target, batch_cells=2, relationship_chunk=7
            )
            self.assertEqual(pq.read_schema(target), METRIC_SCHEMA)
            difference = connection.execute("""
                (SELECT * FROM expected EXCEPT ALL SELECT * FROM read_parquet($target))
                UNION ALL
                (SELECT * FROM read_parquet($target) EXCEPT ALL SELECT * FROM expected)
            """, {"target": str(target)}).fetchall()
            self.assertEqual(difference, [])

    def test_missing_null_and_noncanonical_ids_do_not_acquire_metric_flags(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, duckdb.connect() as connection:
            root = Path(temporary)
            connection.execute("""
                CREATE TEMP TABLE species AS SELECT '01' AS gbif_accepted_id,
                    'Endangered' AS redlist_category, true AS has_dna_species_level,
                    true AS genus_has_dna, true AS family_has_dna,
                    false AS goat_data_deficient, true AS is_terrestrial,
                    false AS is_freshwater, false AS is_marine
            """)
            source, target = root / "lists.parquet", root / "metrics.parquet"
            cell = h3.str_to_int(h3.latlng_to_cell(0, 0, 7))
            pq.write_table(pa.Table.from_pylist([
                {"h3_cell": cell, "species_ids": [1, None, 999]},
            ], schema=LIST_SCHEMA), source)
            aggregate_species_lists(connection, source, target)
            self.assertEqual(pq.read_table(target)["total_species__all"].to_pylist(), [0])

    def test_interrupted_native_write_preserves_previous_output_and_removes_temporary(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, duckdb.connect() as connection:
            root = Path(temporary)
            target = root / "base_1.parquet"
            target.write_bytes(b"previous output")

            def interrupted(connection, source, temporary, **kwargs):
                temporary.write_bytes(b"incomplete")
                raise RuntimeError("interrupted")

            with patch("ark_pipeline.builders.fine_metrics.aggregate_species_lists", side_effect=interrupted):
                with self.assertRaisesRegex(RuntimeError, "interrupted"):
                    aggregate_part(connection, root / "source.parquet", target)
            self.assertEqual(target.read_bytes(), b"previous output")
            self.assertFalse(target.with_suffix(".building.parquet").exists())


class PinnedMetricInputsTests(unittest.TestCase):
    def fixture(self, root: Path, *, workers=1):
        species = root / "species.parquet"
        systems = root / "systems.parquet"
        pq.write_table(pa.Table.from_pylist([{
            "gbif_accepted_id": "101", "species_name": "Synthetic species",
            "family": "Synthetic family", "redlist_category": "Endangered",
            "has_dna_species_level": False, "genus_has_dna": True,
            "family_has_dna": True, "goat_data_deficient": False,
            "edge_group_name": None, "has_ebp_criteria_evidence": False,
        }]), species)
        pq.write_table(pa.Table.from_pylist([
            {"gbif_accepted_id": "101", "system": "Terrestrial"},
        ]), systems)
        parts = root / "lists"
        parts.mkdir()
        for lat, lng in ((56, 10), (-40, 150)):
            cell = h3.latlng_to_cell(lat, lng, 7)
            base = h3.get_base_cell_number(cell)
            pq.write_table(pa.Table.from_pylist([
                {"h3_cell": h3.str_to_int(cell), "species_ids": [101]},
            ], schema=LIST_SCHEMA), parts / f"base_{base}.parquet")
        return SimpleNamespace(
            parts_dir=parts, species=species, species_systems=systems,
            output_dir=root / "output", scratch_dir=root / "scratch",
            memory_limit="128MB", threads=1, workers=workers, no_progress=True,
            overwrite=False, base_cell=None, limit=None,
        )

    def test_serial_reader_mutations_preserve_prior_outputs_and_resume(self):
        for input_name in ("species", "species_systems", "partition"):
            with self.subTest(input=input_name), tempfile.TemporaryDirectory() as temporary:
                args = self.fixture(Path(temporary))
                fine_metrics.build_parts(args)
                before = {p.name: p.read_bytes() for p in args.output_dir.glob("base_*")}
                source = next(args.parts_dir.glob("base_*.parquet"))
                changed = source if input_name == "partition" else getattr(args, input_name)
                original = changed.read_bytes()
                real_prepare = fine_metrics.prepare_species

                def mutate_before_read(connection, species, systems):
                    table = pq.read_table(changed)
                    if input_name == "species":
                        rows = table.to_pylist()
                        rows[0]["has_dna_species_level"] = True
                        table = pa.Table.from_pylist(rows, schema=table.schema)
                    elif input_name == "species_systems":
                        table = pa.Table.from_pylist([
                            {"gbif_accepted_id": "101", "system": "Marine"},
                        ], schema=table.schema)
                    else:
                        rows = table.to_pylist()
                        rows[0]["species_ids"] = [101, 101]
                        table = pa.Table.from_pylist(rows, schema=table.schema)
                    pq.write_table(table, changed)
                    real_prepare(connection, species, systems)

                args.overwrite = True
                with patch.object(fine_metrics, "prepare_species", side_effect=mutate_before_read):
                    with self.assertRaisesRegex(RuntimeError, "input changed|snapshotting"):
                        fine_metrics.build_parts(args)
                self.assertEqual(before, {p.name: p.read_bytes() for p in args.output_dir.glob("base_*")})
                changed.write_bytes(original)
                args.overwrite = False
                with patch.object(fine_metrics, "aggregate_part", wraps=aggregate_part) as rebuilt:
                    fine_metrics.build_parts(args)
                    self.assertEqual(rebuilt.call_count, 0)
                for target in args.output_dir.glob("base_*.parquet"):
                    self.assertEqual(pq.read_table(target)["priority_en_sp_count__all"].to_pylist(), [1])
                self.assertEqual(list(args.output_dir.glob(".metric-inputs-*")), [])

    def test_parallel_snapshots_share_identity_and_reject_changed_metadata(self):
        with tempfile.TemporaryDirectory() as temporary:
            args = self.fixture(Path(temporary), workers=2)
            fine_metrics.build_parts(args)
            before = {p.name: p.read_bytes() for p in args.output_dir.glob("base_*")}
            for source in args.parts_dir.glob("base_*.parquet"):
                self.assertTrue(fine_metrics.aggregate_receipt_matches(args, source, args.output_dir / source.name))
            original = args.species.read_bytes()
            parallel = fine_metrics._build_parts_parallel

            def changed_before_workers(pinned, sources, pending, display):
                rows = pq.read_table(args.species).to_pylist()
                rows[0]["has_dna_species_level"] = True
                pq.write_table(pa.Table.from_pylist(rows), args.species)
                return parallel(pinned, sources, pending, display)

            args.overwrite = True
            with patch.object(fine_metrics, "_build_parts_parallel", side_effect=changed_before_workers):
                with self.assertRaisesRegex(RuntimeError, "input changed"):
                    fine_metrics.build_parts(args)
            self.assertEqual(before, {p.name: p.read_bytes() for p in args.output_dir.glob("base_*")})
            args.species.write_bytes(original)
            args.overwrite = False
            fine_metrics.build_parts(args)

    def test_receipt_boundary_failure_preserves_prior_pair(self):
        with tempfile.TemporaryDirectory() as temporary:
            args = self.fixture(Path(temporary))
            fine_metrics.build_parts(args)
            before = {p.name: p.read_bytes() for p in args.output_dir.glob("base_*")}
            args.overwrite = True
            record = fine_metrics.record_aggregate_receipt

            def changed_at_receipt(*values, **kwargs):
                rows = pq.read_table(args.species).to_pylist()
                rows[0]["has_dna_species_level"] = True
                pq.write_table(pa.Table.from_pylist(rows), args.species)
                record(*values, **kwargs)

            with patch.object(fine_metrics, "record_aggregate_receipt", side_effect=changed_at_receipt):
                with self.assertRaisesRegex(RuntimeError, "input changed"):
                    fine_metrics.build_parts(args)
            self.assertEqual(before, {p.name: p.read_bytes() for p in args.output_dir.glob("base_*")})

    def test_handled_receipt_replace_failure_rolls_back_prior_pair(self):
        with tempfile.TemporaryDirectory() as temporary:
            args = self.fixture(Path(temporary))
            fine_metrics.build_parts(args)
            before = {p.name: p.read_bytes() for p in args.output_dir.glob("base_*")}
            args.overwrite = True
            replace = Path.replace
            failed = False

            def fail_receipt_replace(path, target):
                nonlocal failed
                if path.suffix == ".json" and path.parent.name.startswith(".metric-inputs-") and not failed:
                    failed = True
                    raise OSError("injected receipt replace failure")
                return replace(path, target)

            with patch.object(Path, "replace", fail_receipt_replace):
                with self.assertRaisesRegex(OSError, "injected receipt replace"):
                    fine_metrics.build_parts(args)
            self.assertTrue(failed)
            self.assertEqual(before, {p.name: p.read_bytes() for p in args.output_dir.glob("base_*")})
            for source in args.parts_dir.glob("base_*.parquet"):
                self.assertTrue(fine_metrics.aggregate_receipt_matches(args, source, args.output_dir / source.name))

    def test_identity_is_fresh_when_namespace_is_reused(self):
        with tempfile.TemporaryDirectory() as temporary:
            args = self.fixture(Path(temporary))
            source = next(args.parts_dir.glob("base_*.parquet"))
            old = fine_metrics.aggregate_input_identity(args, source)
            args.species.write_bytes(b"replacement")
            self.assertNotEqual(old, fine_metrics.aggregate_input_identity(args, source))


if __name__ == "__main__":
    unittest.main()
