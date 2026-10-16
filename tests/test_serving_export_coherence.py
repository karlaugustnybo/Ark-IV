"""SQL membership exports must describe the same prepared coarse map."""

import contextlib
import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import duckdb
import h3
import pyarrow as pa
import pyarrow.ipc as ipc
import pyarrow.parquet as pq

from ark_pipeline.aggregation.metrics import aggregate_species_lists
from ark_pipeline.builders.coarse_cache import (
    METRICS,
    SYSTEMS,
    export_coarse_snapshot,
    export_parquet,
    materialize_aggregates,
    sql_path,
)
from ark_pipeline.builders.coarse_cache import (
    main as coarse_main,
)
from ark_pipeline.builders.fine_metrics import (
    aggregate_part,
    prepare_species,
    record_aggregate_receipt,
)
from ark_pipeline.builders.source_database import build_h3_table, build_species_tables
from ark_pipeline.cli.map_serving import validate_config
from ark_pipeline.cli.serving_tiles import (
    build,
    file_record,
    read_prepared,
    record_prepared,
    validate_coarse_membership,
    validate_coarse_metrics,
    validate_exports,
    validate_fine_membership,
)
from backend.config import Settings
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
            # This isolated empty SQL relation has no scored cell rows; it is
            # not a publishable empty-fine preparation.
            for system in SYSTEMS:
                connection.execute(f'DELETE FROM h3_res3_agg_{system}')
            for path in (recorded.h3_root / 'res7_merged_parts').glob('*.parquet'):
                original = pq.read_table(path)
                pq.write_table(pa.Table.from_pylist([], schema=original.schema), path)
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
    def test_nonidentity_mapping_rejects_genuine_fresh_preparation(self):
        from backend.app import _external_res7_species_ids

        for source_name, target_name in (('source_species_id', 'app_species_id'), ('iucn_sis_id', 'gbif_accepted_id')):
            with self.subTest(source_name=source_name), tempfile.TemporaryDirectory() as temporary, contextlib.redirect_stdout(io.StringIO()):
                root = Path(temporary)
                args, recorded, cells = prepared_exports(root)
                with duckdb.connect(str(recorded.build_duckdb), read_only=True) as connection:
                    species = connection.execute('SELECT * FROM SpecInfo').to_arrow_table()
                first = species.to_pylist()[0]
                pq.write_table(pa.Table.from_pylist([{**first, 'iucn_sis_id': '2'},
                    {**first, 'gbif_accepted_id': '2', 'iucn_sis_id': '1',
                     'redlist_category': 'Critically Endangered'}], schema=species.schema), recorded.species)
                pq.write_table(pa.table({'gbif_accepted_id': ['1', '2'], 'system': ['Terrestrial', 'Terrestrial']}), recorded.species_systems)
                pq.write_table(pa.table({source_name: [1, 2], target_name: ['1', '2']}), recorded.crosswalk)

                def rebuild():
                    metric_args = regenerate_fine(recorded)
                    source = root / f'source-{len(list(root.glob("source-*.duckdb")))}.duckdb'
                    with duckdb.connect(str(source)) as connection:
                        build_species_tables(connection, recorded.species, recorded.species_systems)
                        build_h3_table(connection, 3, recorded.h3_root / 'h3_res3_species_global_merged.parquet', recorded.crosswalk)
                    with duckdb.connect(str(recorded.build_duckdb)) as connection:
                        connection.execute(f"ATTACH {sql_path(source)} AS source (READ_ONLY)")
                        materialize_aggregates(connection, (3,))
                        export_parquet(connection, root / 'exports', resolutions=(3,))
                        snapshot = export_coarse_snapshot(connection, recorded.metadata_template.parent)
                    metadata = json.loads(recorded.metadata_template.read_text())
                    metadata['coarse_snapshot'] = snapshot
                    recorded.metadata_template.write_text(json.dumps(metadata))
                    return metric_args

                rebuild()
                report = record_prepared(recorded)
                accepted = recorded.output.read_bytes()
                for mode in (False, True):
                    args.static_fine = mode
                    self.assertEqual(build(args)['status'], 'passed')
                pointers = [(root / path).resolve() for path in ('tiles/current', 'tiles/static/current')]
                pq.write_table(pa.table({source_name: [1, 2], target_name: ['2', '1']}), recorded.crosswalk)
                metric_args = rebuild()
                # All producers completed: coarse/SQL/Arrow use mapped CR species2,
                # while native fine metrics/detail still interpret raw1 as EN species1.
                with duckdb.connect(str(recorded.build_duckdb), read_only=True) as connection:
                    self.assertEqual(connection.execute('SELECT DISTINCT crit_endangered_count, endangered_count FROM h3_res3_agg_all').fetchall(), [(1, 0)])
                self.assertEqual(pq.read_table(root / 'exports/cell_species_lists.parquet')['species_ids'].to_pylist(), [['2'], ['2']])
                snapshot = json.loads(recorded.metadata_template.read_text())['coarse_snapshot']
                with ipc.open_file(recorded.metadata_template.parent / Path(snapshot['url']).name) as reader:
                    self.assertEqual(reader.read_all().select(['a_cr_sp', 'a_en_sp']).to_pylist(),
                        [{'a_cr_sp': 1, 'a_en_sp': 0}, {'a_cr_sp': 1, 'a_en_sp': 0}])
                for path in recorded.parts_dir.glob('*.parquet'):
                    self.assertEqual(pq.read_table(path).select(['crit_endangered_count__all', 'endangered_count__all']).to_pylist(),
                        [{'crit_endangered_count__all': 0, 'endangered_count__all': 1}])
                with patch('backend.app.settings', SimpleNamespace(res7_parts_dir=recorded.h3_root / 'res7_merged_parts')):
                    self.assertEqual(_external_res7_species_ids(h3.str_to_int(cells[0])), ['1'])
                error = 'Managed fine serving requires identity source-to-serving IDs'
                with self.assertRaisesRegex(ValueError, error):
                    record_prepared(recorded)
                self.assertEqual(recorded.output.read_bytes(), accepted)
                # Simulate an old intake using genuine new file/receipt identities;
                # do not modify any producer receipt or the accepted report.
                report['files'] = {name: file_record(Path(value['path'])) for name, value in report['files'].items() if name != 'coarse_snapshot'}
                snapshot = json.loads(recorded.metadata_template.read_text())['coarse_snapshot']
                report['files']['coarse_snapshot'] = file_record(recorded.metadata_template.parent / Path(snapshot['url']).name)
                report['exports'] = {path.name: file_record(path) for path in (root / 'exports').glob('*.parquet')}
                report['metric_identity'] = metric_args._aggregate_metadata_identity
                for records in report['parts'].values():
                    for name, value in records.items():
                        records[name] = file_record(Path(value['path']))
                legacy = root / 'old-intake.json'
                legacy.write_text(json.dumps(report))
                with self.assertRaisesRegex(ValueError, error):
                    read_prepared(legacy)
                args.prepared_inputs = legacy
                for mode in (False, True):
                    args.static_fine = mode
                    with self.assertRaisesRegex(ValueError, error):
                        build(args)
                self.assertEqual(recorded.output.read_bytes(), accepted)
                self.assertEqual([(root / path).resolve() for path in ('tiles/current', 'tiles/static/current')], pointers)

    @unittest.skipUnless(shutil.which("tippecanoe"), "Tippecanoe required for identity publication")
    def test_used_identity_mappings_publish_with_unused_nonidentity_rows(self):
        for columns in ({'source_species_id': [1, 2], 'app_species_id': ['1', 'other']},
                        {'iucn_sis_id': [1, 2], 'gbif_accepted_id': ['1', 'other']},
                        {'source_species_id': [1, 2], 'app_species_id': [1, 20]},
                        {'iucn_sis_id': [1, 2]}):
            with self.subTest(columns=columns), tempfile.TemporaryDirectory() as temporary, contextlib.redirect_stdout(io.StringIO()):
                root = Path(temporary)
                args, recorded, cells = prepared_exports(root)
                pq.write_table(pa.table(columns), recorded.crosswalk)
                self.assertEqual(record_prepared(recorded)['status'], 'passed')
                self.assertEqual(read_prepared(recorded.output)['status'], 'passed')
                for mode in (False, True):
                    args.static_fine = mode
                    self.assertEqual(build(args)['status'], 'passed')
                    self.assertTrue(build(args)['reused'])
                config = validate_config(root / 'tiles/current/serving.json')
                self.assertEqual(config['status'], 'passed')
                from backend.app import _external_res7_species_ids
                with patch('backend.app.settings', SimpleNamespace(res7_parts_dir=Path(config['environment']['RES7_PARTS_DIR']))):
                    self.assertEqual(_external_res7_species_ids(h3.str_to_int(cells[0])), ['1'])

    def test_noncanonical_serving_id_is_not_fine_identity(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, crosswalk, build_path = root / 'coarse.parquet', root / 'crosswalk.parquet', root / 'build.duckdb'
            pq.write_table(pa.table({'h3_cell': [h3.str_to_int(h3.latlng_to_cell(55, 12, 3))], 'species_ids': [[1]]}), source)
            pq.write_table(pa.table({'source_species_id': [1], 'app_species_id': ['001']}), crosswalk)
            with duckdb.connect(str(build_path)) as connection:
                build_h3_table(connection, 3, source, crosswalk)
            report = {'files': {name: file_record(path) for name, path in
                (('coarse_source', source), ('crosswalk', crosswalk), ('build_duckdb', build_path))}}
            with self.assertRaisesRegex(ValueError, 'Managed fine serving requires identity source-to-serving IDs'):
                validate_coarse_membership(report)

    def test_coarse_recomputation_covers_dna_nulls_and_all_ecosystems(self):
        for field, value in (('has_dna_species_level', True), ('genus_has_dna', False),
                             ('family_has_dna', False), ('goat_data_deficient', True),
                             ('family_has_dna', None), ('system', 'Freshwater'), ('system', 'Marine')):
            with self.subTest(field=field, value=value), tempfile.TemporaryDirectory() as temporary, contextlib.redirect_stdout(io.StringIO()):
                root = Path(temporary)
                _, recorded, _ = prepared_exports(root)
                path = recorded.species_systems if field == 'system' else recorded.species
                table = pq.read_table(path)
                pq.write_table(pa.Table.from_pylist([{**row, field: value} for row in table.to_pylist()], schema=table.schema), path)
                source = root / 'source.duckdb'
                with duckdb.connect(str(source)) as connection:
                    build_species_tables(connection, recorded.species, recorded.species_systems)
                    build_h3_table(connection, 3, recorded.h3_root / 'h3_res3_species_global_merged.parquet', recorded.crosswalk)
                with duckdb.connect(str(recorded.build_duckdb)) as connection:
                    connection.execute(f'ATTACH {sql_path(source)} AS source (READ_ONLY)')

                    class InterruptedConnection:
                        def execute(self, query, *arguments):
                            if 'CREATE OR REPLACE TABLE h3_res3_agg_all' in query:
                                raise KeyboardInterrupt('fixture interruption before first aggregate replacement')
                            return connection.execute(query, *arguments)

                    with self.assertRaises(KeyboardInterrupt):
                        materialize_aggregates(InterruptedConnection(), (3,))
                    with self.assertRaisesRegex(ValueError, 'Coarse scored metrics do not match current dimensions and membership'):
                        validate_coarse_metrics(connection)
                    materialize_aggregates(connection, (3,))
                    validate_coarse_metrics(connection)

    @unittest.skipUnless(shutil.which("tippecanoe"), "Tippecanoe required for publication preservation")
    def test_interrupted_coarse_refresh_rejects_matching_new_arrow_and_fine(self):
        for sql_exports, snapshot in ((True, True), (False, False)):
            with self.subTest(sql_exports=sql_exports, snapshot=snapshot), tempfile.TemporaryDirectory() as temporary, contextlib.redirect_stdout(io.StringIO()):
                root = Path(temporary)
                args, recorded, _ = prepared_exports(root)
                # The real source builder copies the full supplied schema.
                with duckdb.connect(str(recorded.build_duckdb), read_only=True) as connection:
                    species = connection.execute('SELECT * FROM SpecInfo').to_arrow_table()
                pq.write_table(species, recorded.species)
                regenerate_fine(recorded)
                settings = Settings(_env_file=None, build_duckdb_path=recorded.build_duckdb,
                    source_duckdb_path=root / 'source.duckdb', export_dir=root / 'exports',
                    tile_dir=recorded.metadata_template.parent, map_metadata_path=recorded.metadata_template,
                    validation_report_path=root / 'producer-validation.json', jurisdictions_path=args.admin0,
                    admin1_boundaries_path=args.admin1, municipality_boundaries_path=args.municipality,
                    eez_boundaries_path=args.eez, conservation_boundaries_path=args.conservation_framework)

                def export(rebuild=False):
                    command = ['coarse_cache', '--resolutions', '3', '--skip-tiles']
                    if rebuild:
                        command.append('--rebuild-aggregates')
                    with patch('ark_pipeline.builders.coarse_cache.get_settings', return_value=settings), patch('sys.argv', command):
                        coarse_main()
                    self.assertEqual(json.loads(settings.validation_report_path.read_text())['status'], 'ok')
                    if not snapshot:
                        metadata = json.loads(recorded.metadata_template.read_text())
                        metadata.pop('coarse_snapshot')
                        recorded.metadata_template.write_text(json.dumps(metadata))
                    if not sql_exports:
                        settings.export_dir.rename(root / ('unused-exports-' + str(len(list(root.glob('unused-exports-*'))))))

                export()
                report = record_prepared(recorded)
                for mode in (False, True):
                    args.static_fine = mode
                    build(args)
                pointers = [(root / path).resolve() for path in ('tiles/current', 'tiles/static/current')]
                accepted = recorded.output.read_bytes()
                pq.write_table(pa.Table.from_pylist([{**row, 'redlist_category': 'Critically Endangered'} for row in species.to_pylist()],
                    schema=species.schema), recorded.species)
                metric_args = regenerate_fine(recorded)
                with duckdb.connect(str(settings.source_duckdb_path)) as connection:
                    build_species_tables(connection, recorded.species, recorded.species_systems)
                    build_h3_table(connection, 3, recorded.h3_root / 'h3_res3_species_global_merged.parquet', recorded.crosswalk)
                with duckdb.connect(str(recorded.build_duckdb)) as connection:
                    connection.execute(f'ATTACH {sql_path(settings.source_duckdb_path)} AS source (READ_ONLY)')

                    class InterruptedConnection:
                        def execute(self, query, *arguments):
                            if 'CREATE OR REPLACE TABLE h3_res3_agg_all' in query:
                                raise KeyboardInterrupt('fixture interruption before first aggregate replacement')
                            return connection.execute(query, *arguments)

                    with self.assertRaises(KeyboardInterrupt):
                        materialize_aggregates(InterruptedConnection(), (3,))
                # Supported reuse exports genuinely new SQL/Arrow from stale
                # metric tables; neither scores nor producer receipts are edited.
                export()
                with duckdb.connect(str(recorded.build_duckdb), read_only=True) as connection:
                    self.assertEqual(connection.execute('SELECT DISTINCT redlist_category FROM SpecInfo').fetchall(), [('Critically Endangered',)])
                    self.assertEqual(connection.execute('SELECT DISTINCT crit_endangered_count, endangered_count FROM h3_res3_agg_all').fetchall(), [(0, 1)])
                with self.assertRaisesRegex(ValueError, 'Coarse scored metrics do not match current dimensions and membership'):
                    record_prepared(recorded)
                self.assertEqual(recorded.output.read_bytes(), accepted)
                # A pre-fix intake pins these real new outputs. Consumption must
                # reject it before either publishing or reusing an existing pack.
                report['files'] = {name: file_record(Path(value['path'])) for name, value in report['files'].items() if name != 'coarse_snapshot'}
                metadata = json.loads(recorded.metadata_template.read_text())
                if snapshot:
                    report['files']['coarse_snapshot'] = file_record(recorded.metadata_template.parent / Path(metadata['coarse_snapshot']['url']).name)
                report['exports'] = {path.name: file_record(path) for path in settings.export_dir.glob('*.parquet')}
                report['metric_identity'] = metric_args._aggregate_metadata_identity
                for records in report['parts'].values():
                    for name, value in records.items():
                        records[name] = file_record(Path(value['path']))
                legacy = root / 'old-intake.json'
                legacy.write_text(json.dumps(report))
                with self.assertRaisesRegex(ValueError, 'Coarse scored metrics do not match current dimensions and membership'):
                    read_prepared(legacy)
                args.prepared_inputs = legacy
                for mode in (False, True):
                    args.static_fine = mode
                    with self.assertRaisesRegex(ValueError, 'Coarse scored metrics do not match current dimensions and membership'):
                        build(args)
                self.assertEqual(recorded.output.read_bytes(), accepted)
                self.assertEqual([(root / path).resolve() for path in ('tiles/current', 'tiles/static/current')], pointers)
                # A genuine completed producer refresh, with the same inputs,
                # is accepted and can select both new publication modes.
                export(rebuild=True)
                self.assertEqual(record_prepared(recorded)['status'], 'passed')
                args.prepared_inputs = recorded.output
                for mode in (False, True):
                    args.static_fine = mode
                    self.assertEqual(build(args)['status'], 'passed')
                    self.assertTrue(build(args)['reused'])
                self.assertEqual(validate_config(root / 'tiles/current/serving.json')['status'], 'passed')
                self.assertTrue(all((root / path).resolve() != previous for path, previous in
                    zip(('tiles/current', 'tiles/static/current'), pointers, strict=True)))

    @unittest.skipUnless(shutil.which("tippecanoe"), "Tippecanoe required for publication preservation")
    def test_fine_only_refresh_rejects_with_genuine_fresh_metrics(self):
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
                for mode in (False, True):
                    args.static_fine = mode
                    build(args)
                pointers = [(root / path).resolve() for path in ('tiles/current', 'tiles/static/current')]
                accepted = recorded.output.read_bytes()
                for path in (recorded.h3_root / 'res7_merged_parts').glob('*.parquet'):
                    original = pq.read_table(path)
                    replacement = path.with_suffix('.replacement')
                    pq.write_table(pa.Table.from_pylist([{**row, 'species_ids': [2]} for row in original.to_pylist()],
                        schema=original.schema), replacement)
                    replacement.replace(path)
                regenerate_fine(recorded)
                with self.assertRaisesRegex(ValueError, 'Coarse source does not match fine parent membership'):
                    record_prepared(recorded)
                self.assertEqual(recorded.output.read_bytes(), accepted)
                # Reconstruct an old-intake report using genuine new files, never
                # altering producer receipts, to exercise consumption/reuse gates.
                for records in report['parts'].values():
                    for name, value in records.items():
                        records[name] = file_record(Path(value['path']))
                recorded.output.write_text(json.dumps(report))
                with self.assertRaisesRegex(ValueError, 'Coarse source does not match fine parent membership'):
                    read_prepared(recorded.output)
                for mode in (False, True):
                    args.static_fine = mode
                    with self.assertRaisesRegex(ValueError, 'Coarse source does not match fine parent membership'):
                        build(args)
                self.assertEqual([(root / path).resolve() for path in ('tiles/current', 'tiles/static/current')], pointers)

    @unittest.skipUnless(shutil.which("tippecanoe"), "Tippecanoe required for publication preservation")
    def test_stale_real_arrow_rejects_after_consistent_metadata_and_metrics_refresh(self):
        for sql_exports in (True, False):
            with self.subTest(sql_exports=sql_exports), tempfile.TemporaryDirectory() as temporary, contextlib.redirect_stdout(io.StringIO()):
                root = Path(temporary)
                args, recorded, _ = prepared_exports(root)
                with duckdb.connect(str(recorded.build_duckdb), read_only=True) as connection, patch(
                    'ark_pipeline.builders.coarse_cache.TILE_FEATURE_BATCH_SIZE', 1
                ):
                    descriptor = export_coarse_snapshot(connection, recorded.metadata_template.parent)
                with ipc.open_file(recorded.metadata_template.parent / Path(descriptor['url']).name) as reader:
                    self.assertEqual(reader.num_record_batches, 2)
                metadata = json.loads(recorded.metadata_template.read_text())
                metadata['coarse_snapshot'] = descriptor
                recorded.metadata_template.write_text(json.dumps(metadata))
                if not sql_exports:
                    (root / 'exports').rename(root / 'unused-exports')
                report = record_prepared(recorded)
                for mode in (False, True):
                    args.static_fine = mode
                    build(args)
                pointers = [(root / path).resolve() for path in ('tiles/current', 'tiles/static/current')]
                accepted = recorded.output.read_bytes()
                species = pq.read_table(recorded.species)
                pq.write_table(pa.Table.from_pylist([{**row, 'redlist_category': 'Critically Endangered'} for row in species.to_pylist()],
                    schema=species.schema), recorded.species)
                metric_args = regenerate_fine(recorded)
                with duckdb.connect(str(recorded.build_duckdb)) as connection:
                    connection.execute("UPDATE SpecInfo SET redlist_category='Critically Endangered'")
                    prepare_species(connection, recorded.species, recorded.species_systems)
                    coarse_metrics = root / 'refreshed-coarse.parquet'
                    aggregate_species_lists(connection, recorded.h3_root / 'h3_res3_species_global_merged.parquet', coarse_metrics)
                    for system in SYSTEMS:
                        projection = ', '.join(f'"{metric}__{system.lower()}" AS "{metric}"' for metric in METRICS)
                        connection.execute(f'CREATE OR REPLACE TABLE h3_res3_agg_{system} AS SELECT h3_index, {projection} FROM read_parquet(?)',
                            [str(coarse_metrics)])
                    if sql_exports:
                        export_parquet(connection, root / 'exports', resolutions=(3,))
                with self.assertRaisesRegex(ValueError, 'Coarse snapshot does not match prepared build'):
                    record_prepared(recorded)
                self.assertEqual(recorded.output.read_bytes(), accepted)
                for name in ('build_duckdb', 'species'):
                    report['files'][name] = file_record(Path(report['files'][name]['path']))
                for name, value in report['exports'].items():
                    report['exports'][name] = file_record(Path(value['path']))
                report['metric_identity'] = metric_args._aggregate_metadata_identity
                for records in report['parts'].values():
                    for name, value in records.items():
                        records[name] = file_record(Path(value['path']))
                recorded.output.write_text(json.dumps(report))
                with self.assertRaisesRegex(ValueError, 'Coarse snapshot does not match prepared build'):
                    read_prepared(recorded.output)
                for mode in (False, True):
                    args.static_fine = mode
                    with self.assertRaisesRegex(ValueError, 'Coarse snapshot does not match prepared build'):
                        build(args)
                self.assertEqual([(root / path).resolve() for path in ('tiles/current', 'tiles/static/current')], pointers)
                # Completing the interrupted Arrow/template refresh accepts the
                # same new preparation without changing biological scores.
                with duckdb.connect(str(recorded.build_duckdb), read_only=True) as connection:
                    metadata['coarse_snapshot'] = export_coarse_snapshot(connection, recorded.metadata_template.parent)
                recorded.metadata_template.write_text(json.dumps(metadata))
                self.assertEqual(record_prepared(recorded)['status'], 'passed')

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
                # Standalone source construction retains arbitrary string targets;
                # managed fine serving cannot apply that mapping downstream.
                with duckdb.connect(str(build_path), read_only=True) as connection:
                    self.assertEqual(set(connection.execute('SELECT unnest(gbif_ids) FROM H3Res3Species').fetchall()), {('app-a',), ('app-b',)})
                with self.assertRaisesRegex(ValueError, 'Managed fine serving requires identity source-to-serving IDs'):
                    validate_coarse_membership(report)
                fine = root / 'fine.parquet'
                # Multiple child cells and reversed/repeated source IDs still
                # produce the same distinct parent relation before ID mapping.
                children = list(h3.cell_to_children(h3.latlng_to_cell(55, 12, 3), 7))[:2]
                pq.write_table(pa.Table.from_pylist([{'h3_cell': h3.str_to_int(cell),
                    'species_ids': [202, 101, 101]} for cell in children]), fine)
                report['parts'] = {'fixture': {'source': file_record(fine)}}
                with duckdb.connect() as connection:
                    validate_fine_membership(connection, report)
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

                def validate():
                    if not empty:
                        self.assertEqual(record_prepared(recorded)['status'], 'passed')
                        return
                    # Fine aggregate production rejects empty partitions. Check
                    # the valid empty SQL relation independently of publication,
                    # with an actually empty fine/coarse source relation.
                    report = json.loads(recorded.output.read_text())
                    report['exports'] = {path.name: file_record(path) for path in (root / 'exports').glob('*.parquet')}
                    validate_exports(report)

                validate()
                inverse = pq.read_table(root / 'exports/species_cells_res3.parquet')
                self.assertEqual(inverse.num_rows, 0 if empty else 1)
                expanded = root / 'exports/cell_species.parquet'
                original = pq.read_table(expanded)
                pq.write_table(pa.Table.from_pylist([{'h3_index': h3.str_to_int(h3.latlng_to_cell(-50, 120, 3)),
                    'resolution': 3, 'gbif_accepted_id': '1'}], schema=original.schema), expanded)
                with self.assertRaisesRegex(ValueError, 'cell_species.parquet'):
                    validate()
                expanded.rename(expanded.with_suffix('.optional-absent'))
                validate()
