"""SQL membership exports must describe the same prepared coarse map."""

import contextlib
import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import duckdb
import h3
import pyarrow as pa
import pyarrow.parquet as pq

from ark_pipeline.builders.coarse_cache import export_parquet
from ark_pipeline.builders.fine_metrics import (
    aggregate_part,
    prepare_species,
    record_aggregate_receipt,
)
from ark_pipeline.cli.serving_tiles import (
    build,
    file_record,
    read_prepared,
    record_prepared,
    validate_coarse_membership,
)
from tests.test_tile_export import prepared_fixture


def prepared_exports(root, *, expanded=False, empty=False):
    args, recorded, cells = prepared_fixture(root)
    with duckdb.connect(str(recorded.build_duckdb)) as connection:
        connection.execute("""CREATE OR REPLACE TABLE SpecInfo AS SELECT *, gbif_accepted_id AS iucn_sis_id,
            NULL::VARCHAR AS iucn_assessment_id, NULL::VARCHAR AS gbif_taxon_id,
            NULL::VARCHAR AS goat_taxon_id, 'Synthetic species' AS species_name,
            'Fixture family' AS family, NULL::VARCHAR AS edge_group_name,
            false AS has_ebp_criteria_evidence FROM read_parquet(?)""", [str(recorded.species)])
        connection.execute('CREATE OR REPLACE TABLE SpecSystems AS SELECT * FROM read_parquet(?)', [str(recorded.species_systems)])
        connection.execute("""CREATE OR REPLACE TABLE H3Res3Species AS SELECT lower(to_hex(h3_cell)) AS h3_index,
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


def regenerate_fine(recorded):
    metric_args = SimpleNamespace(species=recorded.species, species_systems=recorded.species_systems)
    with duckdb.connect() as connection:
        prepare_species(connection, recorded.species, recorded.species_systems)
        for source in (recorded.h3_root / 'res7_merged_parts').glob('base_*.parquet'):
            target = recorded.parts_dir / source.name
            aggregate_part(connection, source, target)
            record_aggregate_receipt(metric_args, source, target)
    return metric_args


class ServingExportCoherenceTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("tippecanoe"), "Tippecanoe required for publication preservation")
    def test_membership_substitution_rejects_with_unchanged_dimensions_and_fresh_fine(self):
        for sql_exports in (True, False):
            with self.subTest(sql_exports=sql_exports), tempfile.TemporaryDirectory() as temporary, contextlib.redirect_stdout(io.StringIO()):
                root = Path(temporary)
                args, recorded, _ = prepared_exports(root)
                species = pq.read_table(recorded.species)
                second = {**species.to_pylist()[0], 'gbif_accepted_id': '2', 'redlist_category': 'Critically Endangered'}
                pq.write_table(pa.Table.from_pylist(species.to_pylist() + [second], schema=species.schema), recorded.species)
                systems = pq.read_table(recorded.species_systems)
                pq.write_table(pa.Table.from_pylist(systems.to_pylist() + [{'gbif_accepted_id': '2', 'system': 'Terrestrial'}],
                    schema=systems.schema), recorded.species_systems)
                pq.write_table(pa.table({'iucn_sis_id': [1, 2]}), recorded.crosswalk)
                with duckdb.connect(str(recorded.build_duckdb)) as connection:
                    connection.execute("INSERT INTO SpecInfo SELECT * REPLACE ('2' AS gbif_accepted_id, '2' AS iucn_sis_id, 'Critically Endangered' AS redlist_category) FROM SpecInfo WHERE gbif_accepted_id='1'")
                    connection.execute("INSERT INTO SpecSystems VALUES ('2', 'Terrestrial')")
                    export_parquet(connection, root / 'exports', resolutions=(3,))
                if not sql_exports:
                    (root / 'exports').rename(root / 'unused-exports')
                regenerate_fine(recorded)
                report = record_prepared(recorded)
                accepted = recorded.output.read_bytes()
                args.static_fine = False
                build(args)
                current = (root / 'tiles/current').resolve()
                coarse = recorded.h3_root / 'h3_res3_species_global_merged.parquet'
                for path in (coarse, *(recorded.h3_root / 'res7_merged_parts').glob('*.parquet')):
                    original = pq.read_table(path)
                    pq.write_table(pa.Table.from_pylist([{**row, 'species_ids': [2]} for row in original.to_pylist()],
                        schema=original.schema), path)
                regenerate_fine(recorded)
                with self.assertRaisesRegex(ValueError, 'Consumed coarse membership does not match prepared build'):
                    record_prepared(recorded)
                self.assertEqual(recorded.output.read_bytes(), accepted)
                # An old intake could accept this internally consistent new source/fine
                # generation while retaining the stale coarse build and SQL exports.
                report['files']['coarse_source'] = file_record(coarse)
                for records in report['parts'].values():
                    for name in ('source', 'aggregate', 'receipt'):
                        records[name] = file_record(Path(records[name]['path']))
                recorded.output.write_text(json.dumps(report))
                with self.assertRaisesRegex(ValueError, 'Consumed coarse membership does not match prepared build'):
                    read_prepared(recorded.output)
                for static_fine in (False, True):
                    args.static_fine = static_fine
                    with self.assertRaisesRegex(ValueError, 'Consumed coarse membership does not match prepared build'):
                        build(args)
                self.assertEqual((root / 'tiles/current').resolve(), current)

    def test_explicit_nonidentity_crosswalk_matches_source_producer(self):
        from ark_pipeline.builders.source_database import build_h3_table

        for source_name, target_name in (('source_species_id', 'app_species_id'), ('iucn_sis_id', 'gbif_accepted_id')):
            with self.subTest(source_name=source_name), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                source, crosswalk, build_path = root / 'coarse.parquet', root / 'crosswalk.parquet', root / 'build.duckdb'
                pq.write_table(pa.Table.from_pylist([{'h3_cell': h3.str_to_int(h3.latlng_to_cell(55, 12, 3)),
                    'species_ids': [101, 202]}]), source)
                pq.write_table(pa.table({source_name: [101, 202], target_name: ['app-a', 'app-b']}), crosswalk)
                with duckdb.connect(str(build_path)) as connection:
                    build_h3_table(connection, 3, source, crosswalk)
                    # List ordering is not membership identity.
                    connection.execute('UPDATE H3Res3Species SET gbif_ids=list_reverse(gbif_ids)')
                report = {'files': {name: file_record(path) for name, path in
                    (('coarse_source', source), ('crosswalk', crosswalk), ('build_duckdb', build_path))}}
                validate_coarse_membership(report)
                pq.write_table(pa.table({source_name: [101, 202], target_name: ['app-a', 'app-c']}), crosswalk)
                with self.assertRaisesRegex(ValueError, 'Consumed coarse membership does not match prepared build'):
                    validate_coarse_membership(report)
                pq.write_table(pa.table({source_name: [101], target_name: ['app-a']}), crosswalk)
                with self.assertRaisesRegex(ValueError, 'unmapped source IDs'):
                    validate_coarse_membership(report)
                report['files'].pop('crosswalk')
                with self.assertRaisesRegex(ValueError, 'explicit crosswalk'):
                    validate_coarse_membership(report)

    @unittest.skipUnless(shutil.which("tippecanoe"), "Tippecanoe required for publication preservation")
    def test_consumed_metadata_changes_reject_with_fresh_fine_receipts(self):
        for field, value in (("redlist_category", "Critically Endangered"),
                             ("has_dna_species_level", True), ("system", "Marine"),
                             ("species_name", "Changed synthetic name"), ("metadata_status", "spatial-only")):
            for sql_exports in (True, False):
                with self.subTest(field=field, sql_exports=sql_exports), tempfile.TemporaryDirectory() as temporary, contextlib.redirect_stdout(io.StringIO()):
                    root = Path(temporary)
                    args, recorded, _ = prepared_exports(root)
                    if field in ("metadata_status", "species_name"):
                        initial = "matched" if field == "metadata_status" else "Synthetic species"
                        species = pq.read_table(recorded.species).append_column(field, pa.array([initial]))
                        pq.write_table(species, recorded.species)
                        with duckdb.connect(str(recorded.build_duckdb)) as connection:
                            if field == "metadata_status":
                                connection.execute("ALTER TABLE SpecInfo ADD COLUMN metadata_status VARCHAR DEFAULT 'matched'")
                            export_parquet(connection, root / 'exports', resolutions=(3,))
                        regenerate_fine(recorded)
                    if not sql_exports:
                        (root / 'exports').rename(root / 'unused-exports')
                    report = record_prepared(recorded)
                    accepted = recorded.output.read_bytes()
                    args.static_fine = False
                    build(args)
                    current = (root / 'tiles/current').resolve()
                    path = recorded.species_systems if field == "system" else recorded.species
                    original = pq.read_table(path)
                    pq.write_table(pa.Table.from_pylist([{**row, field: value} for row in original.to_pylist()],
                        schema=original.schema), path)
                    metric_args = regenerate_fine(recorded)
                    with self.assertRaisesRegex(ValueError, 'Consumed .*metadata does not match prepared build'):
                        record_prepared(recorded)
                    self.assertEqual(recorded.output.read_bytes(), accepted)
                    # Simulate an inconsistent report accepted before this validation existed;
                    # identities come from the real newly produced inputs and receipts.
                    report['files']['species'] = file_record(recorded.species)
                    report['files']['species_systems'] = file_record(recorded.species_systems)
                    report['metric_identity'] = metric_args._aggregate_metadata_identity
                    for records in report['parts'].values():
                        for name in ('aggregate', 'receipt'):
                            records[name] = file_record(Path(records[name]['path']))
                    recorded.output.write_text(json.dumps(report))
                    with self.assertRaisesRegex(ValueError, 'Consumed .*metadata does not match prepared build'):
                        read_prepared(recorded.output)
                    with self.assertRaisesRegex(ValueError, 'Consumed .*metadata does not match prepared build'):
                        build(args)
                    self.assertEqual((root / 'tiles/current').resolve(), current)

    def test_canonical_system_relation_accepts_duplicate_and_unknown_rows(self):
        with tempfile.TemporaryDirectory() as temporary, contextlib.redirect_stdout(io.StringIO()):
            _, recorded, _ = prepared_exports(Path(temporary))
            original = pq.read_table(recorded.species_systems)
            rows = original.to_pylist()
            pq.write_table(pa.Table.from_pylist(rows + rows + [{"gbif_accepted_id": "1", "system": "Unknown"}],
                schema=original.schema), recorded.species_systems)
            regenerate_fine(recorded)
            self.assertEqual(record_prepared(recorded)['status'], 'passed')

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
