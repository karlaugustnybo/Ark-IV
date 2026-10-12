"""SQL membership exports must describe the same prepared coarse map."""

import contextlib
import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path

import duckdb
import h3
import pyarrow as pa
import pyarrow.parquet as pq

from ark_pipeline.builders.coarse_cache import export_parquet
from ark_pipeline.cli.serving_tiles import build, file_record, read_prepared, record_prepared
from tests.test_tile_export import prepared_fixture


def prepared_exports(root, *, expanded=False, empty=False):
    args, recorded, cells = prepared_fixture(root)
    with duckdb.connect(str(recorded.build_duckdb)) as connection:
        connection.execute("""CREATE TABLE SpecInfo AS SELECT *, gbif_accepted_id AS iucn_sis_id,
            NULL::VARCHAR AS iucn_assessment_id, NULL::VARCHAR AS gbif_taxon_id,
            NULL::VARCHAR AS goat_taxon_id, 'Synthetic species' AS species_name,
            'Fixture family' AS family, NULL::VARCHAR AS edge_group_name,
            false AS has_ebp_criteria_evidence FROM read_parquet(?)""", [str(recorded.species)])
        connection.execute('CREATE TABLE SpecSystems AS SELECT * FROM read_parquet(?)', [str(recorded.species_systems)])
        connection.execute("""CREATE TABLE H3Res3Species AS SELECT lower(to_hex(h3_cell)) AS h3_index,
            list_transform(species_ids, value -> cast(value AS VARCHAR)) AS gbif_ids FROM read_parquet(?)""",
            [str(recorded.h3_root / 'h3_res3_species_global_merged.parquet')])
        if empty:
            connection.execute('UPDATE H3Res3Species SET gbif_ids=[]')
            coarse_source = recorded.h3_root / 'h3_res3_species_global_merged.parquet'
            original = pq.read_table(coarse_source)
            pq.write_table(pa.Table.from_pylist([{**row, 'species_ids': []} for row in original.to_pylist()],
                schema=original.schema), coarse_source)
            connection.execute('UPDATE h3_res3_agg_all SET total_species=0')
        export_parquet(connection, root / 'exports', resolutions=(3,), include_expanded_cell_species=expanded)
    return args, recorded, cells


class ServingExportCoherenceTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("tippecanoe"), "Tippecanoe required for publication preservation")
    def test_unbound_passage_export_rejects_record_and_old_report_publication(self):
        with tempfile.TemporaryDirectory() as temporary, contextlib.redirect_stdout(io.StringIO()):
            root = Path(temporary)
            args, recorded, _ = prepared_exports(root)
            args.static_fine = False
            report = record_prepared(recorded)
            accepted = recorded.output.read_bytes()
            build(args)
            current = (root / 'tiles/current').resolve()
            passage = root / 'exports/species_passage_cells_res3.parquet'
            pq.write_table(pa.table({'gbif_accepted_id': ['1'], 'resolution': [3],
                'h3_indexes': [[h3.str_to_int(h3.latlng_to_cell(-50, 120, 7))]]}), passage)
            with self.assertRaisesRegex(ValueError, 'receipt-verified explicit passage'):
                record_prepared(recorded)
            self.assertEqual(recorded.output.read_bytes(), accepted)
            # Reports accepted by the former intake must also fail on consumption.
            report['exports'][passage.name] = file_record(passage)
            recorded.output.write_text(json.dumps(report))
            with self.assertRaisesRegex(ValueError, 'receipt-verified explicit passage'):
                read_prepared(recorded.output)
            with self.assertRaisesRegex(ValueError, 'receipt-verified explicit passage'):
                build(args)
            self.assertEqual((root / 'tiles/current').resolve(), current)
            self.assertFalse((current / 'exports' / passage.name).exists())

    def test_invalid_inverse_rejects_preparation_without_overwriting_report(self):
        with tempfile.TemporaryDirectory() as temporary, contextlib.redirect_stdout(io.StringIO()):
            root = Path(temporary)
            _, recorded, _ = prepared_exports(root)
            record_prepared(recorded)
            accepted = recorded.output.read_bytes()
            inverse = root / 'exports/species_cells_res3.parquet'
            original = pq.read_table(inverse)
            row = original.to_pylist()[0]
            cases = {
                'unrelated-cell': [{**row, 'h3_indexes': [h3.str_to_int(h3.latlng_to_cell(-50, 120, 3))]}],
                'fine-cell': [{**row, 'h3_indexes': [h3.str_to_int(h3.latlng_to_cell(55, 12, 7))]}],
                'wrong-resolution': [{**row, 'resolution': 7}],
                'unknown-identifier': [{**row, 'gbif_accepted_id': 'unknown'}],
                'empty': [{**row, 'h3_indexes': []}],
                'null': [{**row, 'h3_indexes': None}],
                'null-cell': [{**row, 'h3_indexes': row['h3_indexes'] + [None]}],
                'duplicate-cell': [{**row, 'h3_indexes': row['h3_indexes'] + row['h3_indexes'][:1]}],
                'duplicate-row': [row, row],
                'missing-row': [],
                'extra-empty-species': [row, {**row, 'gbif_accepted_id': 'extra', 'h3_indexes': []}],
            }
            for name, rows in cases.items():
                with self.subTest(name=name):
                    pq.write_table(pa.Table.from_pylist(rows, schema=original.schema), inverse)
                    with self.assertRaisesRegex(ValueError, 'species_cells_res3.parquet'):
                        record_prepared(recorded)
                    self.assertEqual(recorded.output.read_bytes(), accepted)
            # Membership order is not a scientific difference.
            pq.write_table(pa.Table.from_pylist([{**row, 'h3_indexes': list(reversed(row['h3_indexes']))}], schema=original.schema), inverse)
            self.assertEqual(record_prepared(recorded)['status'], 'passed')
            inverse.rename(inverse.with_suffix('.optional-absent'))
            self.assertEqual(record_prepared(recorded)['status'], 'passed')

    def test_empty_inverse_and_optional_expanded_export_contracts(self):
        for empty in (False, True):
            with self.subTest(empty=empty), tempfile.TemporaryDirectory() as temporary, contextlib.redirect_stdout(io.StringIO()):
                root = Path(temporary)
                _, recorded, _ = prepared_exports(root, expanded=True, empty=empty)
                self.assertEqual(record_prepared(recorded)['status'], 'passed')
                inverse = pq.read_table(root / 'exports/species_cells_res3.parquet')
                self.assertEqual(inverse.num_rows, 0 if empty else 1)
                expanded = root / 'exports/cell_species.parquet'
                original = pq.read_table(expanded)
                pq.write_table(pa.Table.from_pylist([{'h3_index': h3.str_to_int(h3.latlng_to_cell(-50, 120, 3)),
                    'resolution': 3, 'gbif_accepted_id': '1'}], schema=original.schema), expanded)
                with self.assertRaisesRegex(ValueError, 'cell_species.parquet'):
                    record_prepared(recorded)
                expanded.rename(expanded.with_suffix('.optional-absent'))
                self.assertEqual(record_prepared(recorded)['status'], 'passed')
