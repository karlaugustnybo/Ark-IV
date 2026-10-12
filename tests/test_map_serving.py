import contextlib
import io
import json
import math
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import duckdb
import h3

from ark_pipeline.cli.map_serving import validate_config
from ark_pipeline.cli.serving_tiles import build, record_prepared
from backend.res7_tiles import METRICS, render_tile
from tests.test_tile_export import prepared_fixture


class MapServingTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("tippecanoe"), "Tippecanoe is required for the native serving fixture")
    def test_static_archive_and_dynamic_serving_have_independent_publications(self):
        from ark_pipeline.cli.map_serving import global_preview_config
        from ark_pipeline.cli.serving_tiles import valid_archive

        for first_mode in (False, True):
            with self.subTest(static_first=first_mode), tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(io.StringIO()):
                root = Path(tmp)
                args, recorded, _ = prepared_fixture(root)
                original_output = args.output_dir
                args.static_fine = first_mode
                first = build(args)
                self.assertEqual(first['status'], 'passed')
                self.assertEqual(args.output_dir, original_output)
                if first_mode:
                    self.assertFalse((root / 'tiles/current').exists())
                    self.assertTrue(valid_archive(root / 'tiles/static/current/priorities.pmtiles'))
                    # A standalone archive does not claim to provide managed dynamic startup.
                    with patch('ark_pipeline.cli.map_serving.GLOBAL_TILE_DIR', root / 'absent-global'):
                        with self.assertRaisesRegex(ValueError, 'run just data-tiles'):
                            global_preview_config(root)
                args.static_fine = not first_mode
                self.assertEqual(build(args)['status'], 'passed')
                dynamic = (root / 'tiles/current').resolve()
                static = (root / 'tiles/static/current').resolve()
                self.assertNotEqual(dynamic, static)
                self.assertEqual(global_preview_config(root), validate_config(dynamic / 'serving.json'))
                self.assertFalse((static / 'serving.json').exists())
                self.assertTrue(valid_archive(static / 'priorities.pmtiles'))
                for mode in (False, True):
                    args.static_fine = mode
                    self.assertTrue(build(args)['reused'])
                    self.assertEqual((root / 'tiles/current').resolve(), dynamic)
                    self.assertEqual((root / 'tiles/static/current').resolve(), static)
                # An interrupted archive refresh cannot replace either publication.
                args.static_fine = True
                metadata = json.loads(recorded.metadata_template.read_text())
                metadata['release'] = 'archive-refresh'
                recorded.metadata_template.write_text(json.dumps(metadata))
                record_prepared(recorded)
                with patch('ark_pipeline.cli.serving_tiles.stream_query', side_effect=ValueError('archive failed')):
                    with self.assertRaisesRegex(ValueError, 'archive failed'):
                        build(args)
                self.assertEqual((root / 'tiles/current').resolve(), dynamic)
                self.assertEqual((root / 'tiles/static/current').resolve(), static)
                validate_config(dynamic / 'serving.json')

    def test_global_preview_prefers_published_build_and_rejects_benchmarks(self):
        from ark_pipeline.cli.map_serving import global_preview_config, reject_benchmark_bundle
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            (root / "map-metadata.json").write_text(json.dumps({
                "res7_base_cells": [15, 121], "tile_layout": "wide-v2-joint-priority",
                "score_domains": {"all": {}}, "species_normalized_score_domains": {"all": {}},
                "boundary_assignment": "cell-intersection",
            }))
            with patch("ark_pipeline.cli.serving_tiles.valid_archive", return_value=True):
                legacy = global_preview_config(root)
            self.assertEqual(legacy["environment"]["PMTILES_PATH"], str(root / "priorities-res3-res7.pmtiles"))
            self.assertEqual(legacy["environment"]["RES7_AGGREGATE_PARTS_DIR"], "")
            generation = root / "tiles/generations/full"
            generation.mkdir(parents=True)
            (generation / "serving.json").write_text('{}')
            (root / "tiles/current").symlink_to(generation)
            with patch("ark_pipeline.cli.map_serving.validate_config", return_value={"new": True}):
                self.assertEqual(global_preview_config(root), {"new": True})
            with patch("ark_pipeline.cli.map_serving.validate_config", side_effect=ValueError("invalid publication")):
                with self.assertRaisesRegex(ValueError, "invalid publication"):
                    global_preview_config(root)
            with self.assertRaisesRegex(ValueError, "benchmark"):
                reject_benchmark_bundle(root, {"environment": {"TILE_DIR": str(root / "benchmarks/run")}})
            benchmark = root / "benchmarks/run"
            benchmark.mkdir(parents=True)
            alias = root / "innocent-name"
            alias.symlink_to(benchmark)
            with self.assertRaisesRegex(ValueError, "benchmark"):
                reject_benchmark_bundle(alias / "serving.json", {})

    def test_outdated_preview_uses_compatible_local_map(self):
        from ark_pipeline.cli.map_serving import global_preview_config
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            legacy, local = root / "legacy", root / "local"
            legacy.mkdir()
            local.mkdir()
            (legacy / "map-metadata.json").write_text('{"tile_layout":"wide-v1"}')
            (local / "map-metadata.json").write_text(json.dumps({
                "tile_layout": "wide-v2-joint-priority", "available_resolutions": [3],
                "score_domains": {"all": {}}, "species_normalized_score_domains": {"all": {}},
                "boundary_assignment": "cell-intersection",
                "coarse_snapshot": {"url": "/tiles/coarse.arrow"},
            }))
            with patch("ark_pipeline.cli.map_serving.GLOBAL_TILE_DIR", local), \
                 patch("ark_pipeline.cli.serving_tiles.valid_archive", return_value=True):
                with self.assertRaisesRegex(ValueError, "snapshot is missing"):
                    global_preview_config(legacy)
                (local / "coarse.arrow").touch()
                config = global_preview_config(legacy)
                self.assertEqual(config["environment"]["PMTILES_PATH"], str(local / "priorities.pmtiles"))
                self.assertEqual(config["environment"]["RES7_PARTS_DIR"], "")
                (local / "map-metadata.json").write_text('{}')
                with self.assertRaisesRegex(ValueError, "frontend-compatible"):
                    global_preview_config(legacy)

    def test_missing_config_has_actionable_error(self):
        from ark_pipeline.cli.map_serving import main
        with tempfile.TemporaryDirectory() as tmp:
            missing = str(Path(tmp) / "tiles/current/serving.json")
            stderr = io.StringIO()
            with patch("sys.argv", ["map_serving", "start", "--config", missing]), \
                 contextlib.redirect_stderr(stderr), \
                 patch("ark_pipeline.cli.map_serving.subprocess.call") as launch:
                with self.assertRaises(SystemExit) as raised:
                    main()
            self.assertEqual(raised.exception.code, 2)
            self.assertIn("GLOBAL_SERVING_CONFIG", stderr.getvalue())
            self.assertIn("just data-tiles", stderr.getvalue())
            self.assertNotIn("Traceback", stderr.getvalue())
            launch.assert_not_called()

    @unittest.skipUnless(shutil.which("tippecanoe"), "Tippecanoe is required for the native serving fixture")
    def test_dynamic_bundle_metrics_pinning_and_failed_publication(self):
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(io.StringIO()):
            root = Path(tmp)
            args, recorded, cells = prepared_fixture(root)
            args.static_fine = False
            (root / "exports").mkdir()
            (root / "exports/._species.parquet").write_bytes(b"AppleDouble")
            with patch('ark_pipeline.cli.serving_tiles.subprocess.run', side_effect=AssertionError('No tile join')):
                report = build(args)
            current = (root / 'tiles/current').resolve()
            config = validate_config(current / 'serving.json')
            self.assertEqual(report['features'], 2)
            self.assertFalse(list((root / 'tiles').rglob('._species.parquet')))
            self.assertFalse(list((root / 'tiles').rglob('*.fgb')))
            self.assertEqual(len(list((root / 'tiles').rglob('*.pmtiles'))), 1)
            parts = Path(config['environment']['RES7_AGGREGATE_PARTS_DIR'])
            for cell in cells:
                lat, lon = h3.cell_to_latlng(cell)
                z = 8
                x = int((lon + 180) / 360 * 2**z)
                y = int((1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * 2**z)
                for system in ('all', 'terrestrial', 'freshwater', 'marine'):
                    payload = json.loads(render_tile(str(parts), z, x, y, system, 1))['cells']
                    with duckdb.connect() as con:
                        expected = con.execute('SELECT ' + ','.join(f'"{m}__{system}"' for m in METRICS) +
                            ' FROM read_parquet(?) WHERE h3_index=?', [str(recorded.parts_dir / f'base_{h3.get_base_cell_number(cell)}.parquet'), cell]).fetchone()
                    actual = next((row[1:] for row in payload if row[0] == cell), None)
                    self.assertEqual(actual, list(expected) if expected[0] > 0 else None)
            source = next(recorded.parts_dir.glob('*.parquet'))
            pinned = parts / source.name
            self.assertEqual(source.stat().st_ino, pinned.stat().st_ino)
            source.rename(source.with_suffix('.old'))
            source.write_bytes(b'broken replacement')
            validate_config(current / 'serving.json')
            with self.assertRaises(ValueError):
                build(args)
            self.assertEqual((root / 'tiles/current').resolve(), current)

    @unittest.skipUnless(shutil.which("tippecanoe"), "Tippecanoe is required for the native serving fixture")
    def test_exports_are_recorded_pinned_and_reject_mismatched_metadata(self):
        from ark_pipeline.builders.coarse_cache import export_parquet
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(io.StringIO()):
            root = Path(tmp)
            args, recorded, _ = prepared_fixture(root)
            with duckdb.connect(str(recorded.build_duckdb)) as connection:
                connection.execute("""CREATE TABLE SpecInfo AS SELECT *, gbif_accepted_id AS iucn_sis_id,
                    NULL::VARCHAR AS iucn_assessment_id, NULL::VARCHAR AS gbif_taxon_id,
                    NULL::VARCHAR AS goat_taxon_id, 'Synthetic species' AS species_name,
                    'Fixture family' AS family, NULL::VARCHAR AS edge_group_name,
                    false AS has_ebp_criteria_evidence, 'assessed' AS metadata_status,
                    NULL::VARCHAR AS spatial_redlist_category, true AS iucn_assessment_latest
                    FROM read_parquet(?)""", [str(recorded.species)])
                connection.execute('CREATE TABLE SpecSystems AS SELECT * FROM read_parquet(?)', [str(recorded.species_systems)])
                connection.execute("""CREATE TABLE H3Res3Species AS SELECT lower(to_hex(h3_cell)) AS h3_index,
                    list_transform(species_ids, value -> cast(value AS VARCHAR)) AS gbif_ids
                    FROM read_parquet(?)""", [str(recorded.h3_root / 'h3_res3_species_global_merged.parquet')])
                export_parquet(connection, root / 'exports', resolutions=(3,), include_expanded_cell_species=False)
            import pyarrow as pa
            import pyarrow.parquet as pq

            from ark_pipeline.runtime.provenance import sha256
            passage = root / 'species_passage_cells_res3.parquet'
            pq.write_table(pa.table({'gbif_accepted_id':['1'], 'resolution':[3], 'h3_indexes':[[h3.str_to_int(h3.latlng_to_cell(55,12,3))]]}), passage)
            passage.with_suffix('.report.json').write_text(json.dumps({'status':'passed', 'scope':'species-range-overlay', 'resolution':3, 'output_sha256':sha256(passage), 'species_sha256':sha256(recorded.species)}))
            report = record_prepared(recorded)
            self.assertIn('species.parquet', report['exports'])
            args.static_fine = False
            build(args)
            current = (root / 'tiles/current').resolve()
            config = validate_config(current / 'serving.json')
            exported = Path(config['environment']['EXPORT_DIR']) / 'species.parquet'
            self.assertTrue(exported.is_file())
            self.assertTrue((exported.parent / 'species_passage_cells_res3.parquet').is_file())
            self.assertTrue((exported.parent / 'species_passage_cells_res3.report.json').is_file())
            self.assertTrue((current / 'boundaries/admin0.geojson').is_file())
            original = root / 'exports/species.parquet'
            original.rename(original.with_suffix('.saved'))
            original.write_bytes(b'replacement')
            validate_config(current / 'serving.json')
            with self.assertRaises(ValueError):
                build(args)
            self.assertEqual((root / 'tiles/current').resolve(), current)

    def test_dateline_delivery_and_boundary_filters(self):
        from collections import defaultdict

        import pyarrow as pa
        import pyarrow.parquet as pq
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            groups = defaultdict(list)
            cells = [h3.latlng_to_cell(20, lon, 7) for lon in (-179.99, 179.99)]
            for cell in cells:
                groups[h3.get_base_cell_number(cell)].append({"h3_index": cell, **{
                    f"{metric}__{system}": int(metric == "total_species")
                    for metric in METRICS for system in ("all", "terrestrial", "freshwater", "marine")}})
            for base, rows in groups.items():
                pq.write_table(pa.Table.from_pylist(rows), root / f"base_{base}.parquet")
            boundary = root / "boundary.geojson"
            boundary.write_text(json.dumps({"type": "FeatureCollection", "features": [{
                "type": "Feature", "properties": {"code": "TEST", "name": "Test"},
                "geometry": {"type": "MultiPolygon", "coordinates": [
                    [[[179, 19], [180, 19], [180, 21], [179, 21], [179, 19]]],
                    [[[-180, 19], [-179, 19], [-179, 21], [-180, 21], [-180, 19]]]]}}]}))
            for cell in cells:
                lat, lon = h3.cell_to_latlng(cell)
                x = int((lon + 180) / 360 * 256)
                y = int((1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * 256)
                included = json.loads(render_tile(str(root), 8, x, y, "all", 1, str(boundary), ("TEST",)))
                self.assertIn(cell, [row[0] for row in included["cells"]])
                excluded = json.loads(render_tile(str(root), 8, x, y, "all", 1, str(boundary), ("OTHER",)))
                self.assertEqual(excluded["cells"], [])
