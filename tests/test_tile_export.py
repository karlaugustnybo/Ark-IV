from __future__ import annotations

import contextlib
import io
import json
import math
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import duckdb
import h3
import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import shapely
from shapely.geometry import MultiPolygon, box, mapping

from ark_pipeline.aggregation.metrics import aggregate_species_lists
from ark_pipeline.aggregation.pairs import LIST_SCHEMA
from ark_pipeline.builders.coarse_cache import METRICS, SYSTEMS, wide_feature
from ark_pipeline.builders.fine_metrics import (
    aggregate_part,
    prepare_species,
    record_aggregate_receipt,
)
from ark_pipeline.cli.serving_tiles import (
    BOUNDARIES,
    build,
    compile_queries,
    compile_shard,
    nonzero_metric_columns,
    parallel_stream,
    parser,
    read_prepared,
    record_prepared,
    split_balanced_jobs,
)
from ark_pipeline.spatial.boundaries import JurisdictionIndex
from ark_pipeline.tiles import (
    METRIC_COLUMNS,
    PROPERTY_KEYS,
    BoundaryBatchIndex,
    feature_batch,
    stream_query,
)


def boundary_fixture(path: Path) -> None:
    def polygon(code, coordinates):
        return {"type": "Feature", "properties": {"code": code, "name": code},
                "geometry": {"type": "Polygon", "coordinates": coordinates}}
    path.write_text(json.dumps({"type": "FeatureCollection", "features": [
        polygon("B", [[[-180, -80], [180, -80], [180, 80], [-180, 80], [-180, -80]],
                      [[-5, -5], [-5, 5], [5, 5], [5, -5], [-5, -5]]]),
        polygon("A", [[[0, -80], [180, -80], [180, 80], [0, 80], [0, -80]]]),
    ]}))


class TileFeatureTests(unittest.TestCase):
    def test_zero_column_pruning_requires_proof_from_every_row_group(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'metrics.parquet'
            columns = {name: pa.array([0, 0, 0, 0], type=pa.int64()) for name in METRIC_COLUMNS}
            columns[METRIC_COLUMNS[1]] = pa.array([0, 0, 0, 2], type=pa.int64())
            columns[METRIC_COLUMNS[2]] = pa.array([0, 0, None, 0], type=pa.int64())
            for statistics in (True, False):
                pq.write_table(pa.table(columns), path, row_group_size=2, write_statistics=statistics)
                self.assertEqual(nonzero_metric_columns(pq.ParquetFile(path)),
                                 METRIC_COLUMNS[1:3] if statistics else METRIC_COLUMNS)

    def test_batch_envelope_checks_preserve_holes_edges_and_overlapping_codes(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'boundaries.json'
            boundary_fixture(path)
            index = JurisdictionIndex(path)
            batched = BoundaryBatchIndex(index)
            # Fully covered, fully in a hole, touching a hole, straddling a
            # boundary, and empty. Repeat enough cells to exercise batch proofs.
            for geometry in (box(10, 10, 11, 11), box(-3, -3, -2, -2),
                             box(-6, -1, -5, 1), box(-1, -1, 1, 1),
                             box(181, 0, 182, 1)):
                expected = tuple(code for code, boundary in zip(index.codes, index._geometries, strict=True)
                                 if shapely.intersects(boundary, geometry))
                cells = np.asarray([geometry] * 64)
                self.assertEqual(batched.codes_for_geometries(cells), [expected] * len(cells))

    def test_sparse_metrics_preserve_values_geometry_and_all_zero_cells(self):
        cells = [h3.latlng_to_cell(0, 179.9999, 7), *h3.get_pentagons(7)]
        rows = [(cell, *([0] * len(METRIC_COLUMNS) if i == 0 else
                        [i if j % 5 == 0 else 0 for j in range(len(METRIC_COLUMNS))]))
                for i, cell in enumerate(cells)]
        with duckdb.connect() as connection:
            connection.execute('CREATE TABLE metrics (h3_index VARCHAR, ' +
                               ', '.join(f'"{name}" BIGINT' for name in METRIC_COLUMNS) + ')')
            connection.executemany('INSERT INTO metrics VALUES (' +
                                   ','.join('?' for _ in rows[0]) + ')', rows)
            output = io.StringIO()
            self.assertEqual(stream_query(connection, 'SELECT * FROM metrics', output, 7, {},
                                          batch_size=3, omit_zero_metrics=True), len(rows))
            # A narrow projection must preserve metric names, including when
            # the first metric is omitted and when every metric is zero.
            for projection in ('h3_index', f'h3_index, "{METRIC_COLUMNS[5]}"'):
                narrow = io.StringIO()
                stream_query(connection, f'SELECT {projection} FROM metrics', narrow, 7, {}, omit_zero_metrics=True)
                for row, feature in zip(rows, map(json.loads, narrow.getvalue().splitlines()), strict=True):
                    expected = row[6] if ',' in projection else 0
                    self.assertEqual(feature['properties'].get(PROPERTY_KEYS[5], 0), expected)
        actual = [json.loads(line) for line in output.getvalue().splitlines()]
        for original, sparse in zip(feature_batch(rows, 7, {}), actual, strict=True):
            self.assertTrue(all(sparse['properties'].get(key) != 0 for key in PROPERTY_KEYS))
            for key in PROPERTY_KEYS:
                sparse['properties'].setdefault(key, 0)
            self.assertEqual(original, sparse)

    def test_stream_matches_feature_encoding_with_cache_hits_and_eviction(self):
        # Exercise integer conversion, non-ASCII/escaped boundary codes, unusual
        # geometry and >4096 distinct metric vectors without changing JSON bytes.
        cells = [h3.latlng_to_cell(55, 12, 7), h3.latlng_to_cell(0, 179.9999, 7),
                 *h3.get_pentagons(7)]
        rows = [(cells[i % len(cells)], *([i % 4200] * len(METRIC_COLUMNS)))
                for i in range(4300)]
        rows.extend(rows[-5:])
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "boundary.json"
            boundary_fixture(path)
            index = BoundaryBatchIndex.from_path(path)
            index.codes = ['a"\\é', 'B']
            indexes = {"admin0": index}
            expected = "".join(json.dumps(f, separators=(",", ":")) + "\n"
                               for f in feature_batch(rows, 7, indexes))
            for batch_size in (127, 2048):
                connection = Mock()
                connection.execute.return_value.fetchmany.side_effect = [
                    rows[i:i + batch_size] for i in range(0, len(rows), batch_size)
                ] + [[]]
                output = io.StringIO()
                progress = []
                self.assertEqual(stream_query(connection, "query", output, 7, indexes,
                                              batch_size, progress.append), len(rows))
                self.assertEqual(output.getvalue(), expected)
                self.assertEqual(progress[-1], len(rows))

    def test_parts_preserve_catalogue_order_without_repeating_one_boundary(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "parts.json"
            geometries = [MultiPolygon([box(-2, -1, -1, 1), box(1, -1, 2, 1)]),
                          box(-3, -2, 3, 2), box(-3, -2, 3, 2)]
            # Duplicate codes on distinct entries keep the original behavior;
            # touching two parts of the same entry must not repeat that entry.
            path.write_text(json.dumps({"type": "FeatureCollection", "features": [
                {"type": "Feature", "properties": {"code": code, "name": code},
                 "geometry": mapping(geometry)}
                for code, geometry in zip(("Z", "A", "A"), geometries, strict=True)
            ]}))
            index = BoundaryBatchIndex.from_path(path)
            cells = np.asarray([box(-2, -.5, 2, .5), box(-.1, -.1, .1, .1), box(20, 20, 21, 21)])
            self.assertEqual(index.codes_for_geometries(cells), [("Z", "A", "A"), ("A", "A"), ()])
            self.assertEqual(index.codes_for_geometries(np.empty(0, dtype=object)), [])

    def test_batch_matches_legacy_for_dateline_pentagons_holes_and_overlaps(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "boundaries.json"
            boundary_fixture(path)
            index = JurisdictionIndex(path)
            batched = BoundaryBatchIndex(index)
            for resolution in (3, 7):
                cells = sorted({
                    h3.latlng_to_cell(lat, lon, resolution)
                    for lat in (-89, -80, -45, 0, 5, 55, 80, 89)
                    for lon in (-179.9999, -120, -5, 0, 5, 12, 120, 179.9999)
                } | set(h3.get_pentagons(resolution)))
                rows = [(cell, *range(len(SYSTEMS) * len(METRICS))) for cell in cells]
                expected = [wide_feature(row, resolution, {"admin0": index.codes_for_cell(row[0])}) for row in rows]
                for batch_size in (1, 7, 2048):
                    actual = []
                    for offset in range(0, len(rows), batch_size):
                        actual.extend(feature_batch(rows[offset:offset + batch_size], resolution, {"admin0": batched}))
                    self.assertEqual(actual, expected)


class TileCompilerUnitTests(unittest.TestCase):
    def test_binary_batches_match_dense_geometry_membership_and_all_metrics(self):
        from ark_pipeline.tile_stream_worker import encode
        for resolution in (3, 7):
            cells = [h3.latlng_to_cell(lat, lon, resolution) for lat, lon in
                     ((55, 12), (0, 179.9999), (89, 0), (-89, 0))]
            cells += list(h3.get_pentagons(resolution))
            rows = [(cell, *(0 if i == 0 or j % 3 else 2**40 + i + j
                             for j in range(len(PROPERTY_KEYS))))
                    for i, cell in enumerate(cells)]
            with tempfile.TemporaryDirectory() as temporary:
                path = Path(temporary) / "boundary.json"
                boundary_fixture(path)
                indexes = {"admin0": BoundaryBatchIndex.from_path(path)}
                with patch("ark_pipeline.tile_worker_state.INDEXES", indexes):
                    batch, count, _, _, _ = encode((resolution, PROPERTY_KEYS, rows, True))
                    for dense, binary in zip(feature_batch(rows, resolution, indexes), batch.to_pylist(), strict=True):
                        self.assertEqual(binary.pop("h3_index"), dense["properties"]["h3_index"])
                        self.assertTrue(shapely.equals_exact(
                            shapely.from_wkb(binary.pop("geometry")),
                            shapely.from_geojson(json.dumps(dense["geometry"])), 0))
                        for key in PROPERTY_KEYS:
                            self.assertEqual(binary[key] or 0, dense["properties"][key])
                        for key in set(binary) - set(PROPERTY_KEYS):
                            self.assertEqual(binary[key], dense["properties"][key])
                    self.assertEqual(count, len(rows))
                    invalid = [(cells[0], None)]
                    with self.assertRaisesRegex(ValueError, "Null metric"):
                        encode((resolution, PROPERTY_KEYS[:1], invalid, True))
                    with self.assertRaises(TypeError):
                        encode((resolution, PROPERTY_KEYS[:1], invalid, False))

    def test_binary_narrow_and_zero_only_projections_preserve_column_names(self):
        from ark_pipeline.tile_stream_worker import encode
        cell = h3.latlng_to_cell(55, 12, 7)
        for keys, rows in (((), [(cell,)]), ((PROPERTY_KEYS[5],), [(cell, 2**40)])):
            with patch("ark_pipeline.tile_worker_state.INDEXES", {}):
                batch, count, _, _, _ = encode((7, keys, rows, True))
            feature = batch.to_pylist()[0]
            self.assertEqual(count, 1)
            self.assertEqual(feature.get(PROPERTY_KEYS[5], 0), 2**40 if keys else 0)
            self.assertTrue(all(key not in feature for key in set(PROPERTY_KEYS) - set(keys)))

    def test_producer_interruption_stops_only_tracked_process_tree(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "out.pmtiles"
            report = target.with_suffix(".stream-report.json")
            def start_producer(*_, **__):
                report.write_text(json.dumps({"a": {"features": 1}}))
                return producer
            producer = Mock(pid=123456, returncode=None)
            producer.poll.return_value = None
            descendant = Mock()
            tracked = Mock()
            tracked.children.return_value = [descendant]
            args = SimpleNamespace(_tile_database="fixture", tile_threads=3, threads=1,
                                   memory_limit="750MB", scratch_dir=root, batch_size=1,
                                   **dict.fromkeys(BOUNDARIES, root / "boundary.json"))
            with patch("ark_pipeline.cli.serving_tiles.subprocess.Popen", side_effect=start_producer), \
                 patch("ark_pipeline.cli.serving_tiles.psutil.Process", return_value=tracked) as process_lookup:
                with self.assertRaises(KeyboardInterrupt):
                    parallel_stream([("a", 7, "query", 1)], target, subprocess.DEVNULL, args, {},
                                    Mock(side_effect=KeyboardInterrupt), 1, 0)
            process_lookup.assert_called_once_with(producer.pid)
            descendant.kill.assert_called_once()
            producer.kill.assert_called_once()
            producer.wait.assert_called_once()

    def test_balancing_is_deterministic_and_keeps_every_partition(self):
        jobs = [(name, 7, name, count) for name, count in (("c", 5), ("b", 10), ("a", 10), ("d", 0))]
        first = split_balanced_jobs(jobs, 2)
        self.assertEqual(first, split_balanced_jobs(list(reversed(jobs)), 2))
        self.assertCountEqual([job for bucket in first for job in bucket], jobs)
        self.assertEqual([sum(job[3] for job in bucket) for bucket in first], [15, 10])

    def test_later_compiler_launch_failure_stops_started_compiler(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            jobs = [("a", 7, "query", 1), ("b", 7, "query", 1)]
            profiles = {}
            for name, _, _, _ in jobs:
                path = root / f"{name}.fgb"
                path.write_bytes(b"synthetic")
                profiles[name] = {"input_path": str(path), "features": 1}
            process = Mock(stdin=None, stderr=None)
            args = SimpleNamespace(_parallel_tiles=True, scratch_dir=root, tippecanoe="fixture-compiler")
            with patch("ark_pipeline.cli.serving_tiles.parallel_stream", return_value=profiles), \
                 patch("ark_pipeline.cli.serving_tiles.subprocess.Popen", side_effect=[process, OSError("launch failed")]):
                with self.assertRaisesRegex(OSError, "launch failed"):
                    compile_queries(None, jobs, root / "out.pmtiles", {}, args, {}, groups=2)
            process.kill.assert_called_once()
            process.wait.assert_called_once()
            self.assertFalse(any(root.glob("*.fgb")))

    def test_binary_inputs_removed_when_compiler_scratch_setup_fails(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            jobs = [("a", 7, "query", 1), ("b", 7, "query", 1)]
            inputs = [root / f"input-{name}.fgb" for name, _, _, _ in jobs]
            def producer(*args, **kwargs):
                for path in inputs:
                    path.write_bytes(b"completed synthetic input")
                return {name: {"input_path": str(path), "features": 1}
                        for (name, _, _, _), path in zip(jobs, inputs, strict=True)}
            (root / "compiler-0").write_bytes(b"scratch obstruction")
            args = SimpleNamespace(_parallel_tiles=True, scratch_dir=root, tippecanoe="fixture-compiler")
            with patch("ark_pipeline.cli.serving_tiles.parallel_stream", side_effect=producer), \
                 patch("ark_pipeline.cli.serving_tiles.subprocess.Popen") as compiler:
                with self.assertRaises(FileExistsError):
                    compile_queries(None, jobs, root / "out.pmtiles", {}, args, {}, groups=2)
            compiler.assert_not_called()
            self.assertFalse(any(path.exists() for path in inputs))

    def test_binary_inputs_removed_when_producer_is_interrupted_before_report(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            inputs = [root / f"input-{name}.fgb" for name in ("a", "b")]
            def interrupted(*args, **kwargs):
                inputs[0].write_bytes(b"completed synthetic input")
                inputs[1].write_bytes(b"partial synthetic input")
                raise KeyboardInterrupt
            args = SimpleNamespace(_parallel_tiles=True, scratch_dir=root, tippecanoe="fixture-compiler")
            with patch("ark_pipeline.cli.serving_tiles.parallel_stream", side_effect=interrupted), \
                 patch("ark_pipeline.cli.serving_tiles.subprocess.Popen") as compiler:
                with self.assertRaises(KeyboardInterrupt):
                    compile_queries(None, [(name, 7, "query", 1) for name in ("a", "b")],
                                    root / "out.pmtiles", {}, args, {})
            compiler.assert_not_called()
            self.assertFalse(any(path.exists() for path in inputs))

    def test_stream_interruption_stops_compiler_and_removes_partial_archive(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "out.pmtiles"
            output.write_bytes(b"partial")
            process = Mock(stdin=io.StringIO(), stderr=None)
            args = SimpleNamespace(_parallel_tiles=False, scratch_dir=root, tippecanoe="fixture-compiler", batch_size=1)
            with patch("ark_pipeline.cli.serving_tiles.subprocess.Popen", return_value=process), \
                 patch("ark_pipeline.cli.serving_tiles.stream_query", side_effect=KeyboardInterrupt):
                with self.assertRaises(KeyboardInterrupt):
                    compile_queries(None, [("a", 7, "query", 1)], output, {}, args, {})
            process.kill.assert_called_once()
            process.wait.assert_called_once()
            self.assertTrue(process.stdin.closed)
            self.assertFalse(output.exists())

    def test_mixed_binary_resolutions_fail_before_starting_producer(self):
        with tempfile.TemporaryDirectory() as temporary:
            args = SimpleNamespace(_parallel_tiles=True)
            with patch("ark_pipeline.cli.serving_tiles.parallel_stream") as producer:
                with self.assertRaisesRegex(ValueError, "one resolution"):
                    compile_queries(None, [("a", 3, "query", 1), ("b", 7, "query", 1)],
                                    Path(temporary) / "out.pmtiles", {}, args, {})
            producer.assert_not_called()


def prepared_fixture(root: Path):
    h3_root = root / "lists"
    sources = h3_root / "res7_merged_parts"
    sources.mkdir(parents=True)
    parts = root / "metrics"
    parts.mkdir()
    species, systems = root / "species.parquet", root / "systems.parquet"
    pq.write_table(pa.Table.from_pylist([{
        "gbif_accepted_id": "1", "redlist_category": "Endangered",
        "has_dna_species_level": False, "genus_has_dna": True,
        "family_has_dna": True, "goat_data_deficient": False,
    }]), species)
    pq.write_table(pa.table({"gbif_accepted_id": ["1"], "system": ["Terrestrial"]}), systems)
    # Different H3 base cells in the same web tile exercise merging shards.
    cells = ["873e9a68affffff", "875869a48ffffff"]
    coarse = sorted({h3.cell_to_parent(cell, 3) for cell in cells})
    coarse_source = h3_root / "h3_res3_species_global_merged.parquet"
    pq.write_table(pa.Table.from_pylist([
        {"h3_cell": h3.str_to_int(cell), "species_ids": [1]} for cell in coarse
    ], schema=LIST_SCHEMA), coarse_source)
    metric_args = SimpleNamespace(species=species, species_systems=systems)
    with duckdb.connect(str(root / "build.duckdb")) as connection:
        prepare_species(connection, species, systems)
        for cell in cells:
            name = f"base_{h3.get_base_cell_number(cell)}.parquet"
            pq.write_table(pa.Table.from_pylist([
                {"h3_cell": h3.str_to_int(cell), "species_ids": [1]},
            ], schema=LIST_SCHEMA), sources / name)
            aggregate_part(connection, sources / name, parts / name)
            record_aggregate_receipt(metric_args, sources / name, parts / name)
        aggregate_species_lists(connection, coarse_source, root / "coarse.parquet")
        for system in SYSTEMS:
            projection = ", ".join(f'"{metric}__{system.lower()}" AS "{metric}"' for metric in METRICS)
            connection.execute(f"CREATE TABLE h3_res3_agg_{system} AS SELECT h3_index, {projection} FROM read_parquet(?)",
                               [str(root / "coarse.parquet")])
    template = root / "metadata.json"
    template.write_text(json.dumps({key: {system.lower(): {"min": 0, "max": 1} for system in SYSTEMS}
                                    for key in ("score_domains", "species_normalized_score_domains")}))
    record_args = SimpleNamespace(h3_root=h3_root, parts_dir=parts, species=species,
                                  species_systems=systems, build_duckdb=root / "build.duckdb",
                                  metadata_template=template, output=root / "prepared.json")
    record_prepared(record_args)
    boundaries = root / "boundaries.json"
    boundary_fixture(boundaries)
    options = ["build", "--prepared-inputs", str(record_args.output), "--output-dir", str(root / "tiles"),
               "--scratch-dir", str(root / "scratch")]
    for key in BOUNDARIES:
        options.extend(["--" + key.replace("_", "-"), str(boundaries)])
    return parser().parse_args(options), record_args, cells


class PreparedMetricIdentityTests(unittest.TestCase):
    def test_metadata_replacement_during_recording_preserves_previous_report(self):
        from ark_pipeline.cli import serving_tiles

        with tempfile.TemporaryDirectory() as temporary:
            _, args, _ = prepared_fixture(Path(temporary))
            previous = args.output.read_bytes()
            original = args.species.read_bytes()
            record = serving_tiles.file_record

            def replace_after_species_record(path):
                result = record(path)
                if path == args.species:
                    rows = pq.read_table(path).to_pylist()
                    rows[0]["has_dna_species_level"] = True
                    pq.write_table(pa.Table.from_pylist(rows), path)
                return result

            with patch.object(serving_tiles, "file_record", side_effect=replace_after_species_record):
                with self.assertRaisesRegex(ValueError, "Metric input changed"):
                    record_prepared(args)
            self.assertEqual(args.output.read_bytes(), previous)
            args.species.write_bytes(original)
            read_prepared(args.output)


@unittest.skipUnless(all(shutil.which(tool) for tool in ("tippecanoe", "tile-join", "tippecanoe-decode")),
                     "Tippecanoe binaries are required for the actual archive integration test")
class TilePublicationTests(unittest.TestCase):
    def test_dense_serial_sparse_and_binary_tiles_have_identical_numeric_metrics(self):
        import os

        from ark_pipeline.tiles import TILE_ZOOM_RANGES
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            boundary = root / "boundary.json"
            boundary_fixture(boundary)
            indexes = {key: BoundaryBatchIndex.from_path(boundary) for key in BOUNDARIES}
            for resolution in (3, 7):
                cells = [h3.latlng_to_cell(55, lon, resolution) for lon in (12, 14)]
                rows = [(cell, *(0 if i == 0 or j % 2 else 2**40 + j
                                 for j in range(len(PROPERTY_KEYS))))
                        for i, cell in enumerate(cells)]
                database = root / f"metrics-{resolution}.duckdb"
                with duckdb.connect(str(database)) as connection:
                    connection.execute('CREATE TABLE metrics (h3_index VARCHAR, ' +
                                       ', '.join(f'"{name}" BIGINT' for name in METRIC_COLUMNS) + ')')
                    connection.executemany('INSERT INTO metrics VALUES (' +
                                           ','.join('?' for _ in rows[0]) + ')', rows)
                dense_input = root / f"dense-{resolution}.geojson"
                dense_input.write_text("".join(json.dumps(feature) + "\n"
                                               for feature in feature_batch(rows, resolution, indexes)))
                zooms = TILE_ZOOM_RANGES[resolution]
                dense_archive = root / f"dense-{resolution}.pmtiles"
                subprocess.run(["tippecanoe", "--quiet", "--force", "--output", str(dense_archive),
                                "--minimum-zoom", str(zooms["min"]), "--maximum-zoom", str(zooms["max"]),
                                "--no-feature-limit", "--no-tile-size-limit", "--preserve-input-order",
                                str(dense_input)], check=True)
                archives = [dense_archive]
                for binary in (False, True):
                    target = root / f"{'binary' if binary else 'sparse'}-{resolution}.pmtiles"
                    args = SimpleNamespace(_parallel_tiles=binary, _tile_database=str(database),
                                           tile_threads=3, threads=1, memory_limit="750MB", scratch_dir=root / "scratch",
                                           batch_size=1, tippecanoe="tippecanoe",
                                           **dict.fromkeys(BOUNDARIES, boundary))
                    with duckdb.connect(str(database), read_only=True) as connection:
                        compile_queries(connection, [("fixture", resolution, "SELECT * FROM metrics ORDER BY h3_index", 2)],
                                        target, indexes, args, {**os.environ, "TIPPECANOE_MAX_THREADS": "1"})
                    archives.append(target)
                for cell, *expected in rows:
                    lat, lon = h3.cell_to_latlng(cell)
                    zoom = zooms["min"]
                    scale = 2**zoom
                    x = int((lon + 180) / 360 * scale)
                    y = int((1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * scale)
                    decoded = []
                    for archive in archives:
                        tile = json.loads(subprocess.check_output(
                            ["tippecanoe-decode", str(archive), str(zoom), str(x), str(y)], stderr=subprocess.DEVNULL))
                        found = [feature for layer in tile["features"] for feature in layer["features"]
                                 if feature["properties"]["h3_index"] == cell]
                        self.assertEqual(len(found), 1)
                        props = found[0]["properties"]
                        actual = [props.get(key, 0) for key in PROPERTY_KEYS]
                        self.assertEqual(actual, expected)
                        self.assertTrue(all(isinstance(value, int) for value in actual))
                        for key in PROPERTY_KEYS:
                            props.setdefault(key, 0)
                        decoded.append(found[0])
                    self.assertEqual(decoded[0], decoded[1])
                    self.assertEqual(decoded[0], decoded[2])

    def test_real_tiles_resume_and_publish_metadata_together(self):
        with tempfile.TemporaryDirectory() as temporary, contextlib.redirect_stdout(io.StringIO()):
            root = Path(temporary)
            args, record_args, cells = prepared_fixture(root)
            args.checkpoint_shards = True
            report = build(args)
            self.assertEqual(report["features"], 4)
            current = root / "tiles/current"
            first = current.resolve()
            metadata = json.loads((current / "map-metadata.json").read_text())
            self.assertEqual(metadata["complete_resolutions"], [3, 7])
            self.assertEqual(metadata['tile_metric_default'], 0)
            self.assertEqual(metadata['tile_metric_encoding'], 'sparse-zero-v1')
            for cell in cells:
                lat, lon = h3.cell_to_latlng(cell)
                zoom = 8
                x = int((lon + 180) / 360 * (1 << zoom))
                y = int((1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * (1 << zoom))
                decoded = json.loads(subprocess.check_output([
                    "tippecanoe-decode", str(current / "priorities.pmtiles"), str(zoom), str(x), str(y),
                ], stderr=subprocess.DEVNULL))
                features = [f for layer in decoded["features"] for f in layer["features"]]
                found = [f for f in features if f["properties"]["h3_index"] == cell]
                self.assertEqual(len(found), 1)
                self.assertEqual(found[0]["properties"]["a_en_sp"], 1)
                self.assertEqual(found[0]["properties"]["a_total"], 1)
                with duckdb.connect() as connection:
                    projection = ', '.join(f'"{column}"' for column in METRIC_COLUMNS)
                    metric_path = record_args.parts_dir / f'base_{h3.get_base_cell_number(cell)}.parquet'
                    expected = connection.execute(f'SELECT {projection} FROM read_parquet(?) WHERE h3_index=?',
                                                  [str(metric_path), cell]).fetchone()
                self.assertEqual(tuple(found[0]['properties'].get(key, 0) for key in PROPERTY_KEYS), expected)
                self.assertTrue(all(key not in found[0]['properties'] for key, value
                                    in zip(PROPERTY_KEYS, expected, strict=True) if value == 0))
            with patch("ark_pipeline.cli.serving_tiles.compile_shard", side_effect=AssertionError("should reuse")):
                self.assertTrue(build(args)["reused"])

            # A metadata-only refresh reuses every geometric shard.
            metadata = json.loads(record_args.metadata_template.read_text())
            metadata["release"] = "new"
            record_args.metadata_template.write_text(json.dumps(metadata))
            record_prepared(record_args)
            with patch("ark_pipeline.cli.serving_tiles.subprocess.run", side_effect=RuntimeError("interrupted merge")):
                with self.assertRaisesRegex(RuntimeError, "interrupted merge"):
                    build(args)
            self.assertEqual(current.resolve(), first)
            with patch("ark_pipeline.cli.serving_tiles.compile_shard", side_effect=AssertionError("should reuse shards")):
                resumed = build(args)
            self.assertTrue(all(part["reused"] for part in resumed["shards"].values()))
            self.assertNotEqual(current.resolve(), first)
            self.assertEqual(json.loads((current / "map-metadata.json").read_text())["release"], "new")
            # Corruption is detected even when the output length is unchanged.
            archive = current / "priorities.pmtiles"
            with archive.open("r+b") as stream:
                stream.write(b"broken!")
            repaired = build(args)
            self.assertFalse(repaired["reused"])
            self.assertTrue(build(args)["reused"])

            # A failed shard leaves the current bundle intact and its completed
            # predecessor reusable when the build resumes.
            previous = current.resolve()
            args.admin0.write_text(args.admin0.read_text() + "\n")
            calls = 0

            def interrupted(*positional, **keywords):
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise RuntimeError("interrupted shard")
                return compile_shard(*positional, **keywords)

            with patch("ark_pipeline.cli.serving_tiles.compile_shard", side_effect=interrupted):
                with self.assertRaisesRegex(RuntimeError, "interrupted shard"):
                    build(args)
            self.assertEqual(current.resolve(), previous)
            resumed = build(args)
            self.assertEqual(sum(part["reused"] for part in resumed["shards"].values()), 1)

    def test_default_build_starts_fine_at_zoom_eight_and_reuses_groups_after_failed_join(self):
        with tempfile.TemporaryDirectory() as temporary, contextlib.redirect_stdout(io.StringIO()):
            root = Path(temporary)
            args, record_args, cells = prepared_fixture(root)
            args.tile_threads = 3
            with patch("ark_pipeline.cli.serving_tiles.compile_shard", side_effect=AssertionError("no shards")), \
                 patch("ark_pipeline.cli.serving_tiles.subprocess.Popen", wraps=subprocess.Popen) as compiler:
                report = build(args)
                self.assertEqual(report["compilation"]["features"], 4)
                commands = [call.args[0] for call in compiler.call_args_list if call.args[0][0] == args.tippecanoe]
                minimums = [command[command.index('--minimum-zoom') + 1] for command in commands]
                maximums = [command[command.index('--maximum-zoom') + 1] for command in commands]
                # One coarse compiler, then concurrent fine compilers, one per group.
                self.assertEqual(minimums[0], '0')
                self.assertEqual(maximums[0], '6')
                self.assertEqual(minimums[1:], ['8'] * (len(commands) - 1))
                self.assertEqual(maximums[1:], ['12'] * (len(commands) - 1))
                self.assertGreaterEqual(len(commands) - 1, 2)
                self.assertTrue(build(args)["reused"])
            previous = (root / "tiles/current").resolve()
            # Each resolution archive is checkpointed; a failed final join
            # must not force another complete fine-cell export.
            metadata = json.loads(record_args.metadata_template.read_text())
            metadata['release'] = 'updated'
            record_args.metadata_template.write_text(json.dumps(metadata))
            record_prepared(record_args)
            with patch('ark_pipeline.cli.serving_tiles.compile_queries', side_effect=AssertionError('reuse groups')), \
                 patch('ark_pipeline.cli.serving_tiles.subprocess.run', side_effect=RuntimeError('join failed')):
                with self.assertRaisesRegex(RuntimeError, 'join failed'):
                    build(args)
            self.assertEqual((root / 'tiles/current').resolve(), previous)
            with patch('ark_pipeline.cli.serving_tiles.compile_queries', side_effect=AssertionError('reuse groups')):
                resumed = build(args)
            self.assertTrue(all(part['reused'] for part in resumed['shards'].values()))
            previous = (root / "tiles/current").resolve()
            for cell in cells:
                lat, lon = h3.cell_to_latlng(cell)
                x = int((lon + 180) / 360 * 256)
                y = int((1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * 256)
                decoded = json.loads(subprocess.check_output(
                    ['tippecanoe-decode', str(previous / 'priorities.pmtiles'), '8', str(x), str(y)],
                    stderr=subprocess.DEVNULL))
                found = [f for layer in decoded['features'] for f in layer['features']
                         if f['properties']['h3_index'] == cell]
                self.assertEqual(len(found), 1)
                self.assertEqual(found[0]['properties']['a_total'], 1)
            args.admin0.write_text(args.admin0.read_text() + "\n")
            with patch("ark_pipeline.cli.serving_tiles.parallel_stream", side_effect=ValueError("stream failed")):
                with self.assertRaisesRegex(ValueError, "stream failed"):
                    build(args)
            self.assertEqual((root / "tiles/current").resolve(), previous)

    def test_rejected_boundary_change_cannot_poison_restored_input_group_reuse(self):
        for changed_call in (1, 2):
            with self.subTest(changed_producer_call=changed_call):
                with tempfile.TemporaryDirectory() as temporary, contextlib.redirect_stdout(io.StringIO()):
                    root = Path(temporary)
                    args, _, cells = prepared_fixture(root)
                    args.tile_threads = 3
                    original = args.admin0.read_bytes()
                    calls = 0
                    def changed_producer(*positional, **keywords):
                        nonlocal calls
                        calls += 1
                        if calls == changed_call:
                            changed = json.loads(original)
                            for feature in changed["features"]:
                                feature["properties"]["code"] = "WRONG_" + feature["properties"]["code"]
                            args.admin0.write_text(json.dumps(changed))
                        return parallel_stream(*positional, **keywords)
                    with patch("ark_pipeline.cli.serving_tiles.parallel_stream", side_effect=changed_producer):
                        with self.assertRaisesRegex(ValueError, "input changed"):
                            build(args)
                    self.assertFalse((root / "tiles/current").exists())
                    args.admin0.write_bytes(original)
                    with patch("ark_pipeline.cli.serving_tiles.compile_queries", wraps=compile_queries) as compiler:
                        resumed = build(args)
                    self.assertGreater(compiler.call_count, 0)
                    self.assertFalse(resumed["shards"]["coarse"]["reused"])
                    for cell in cells:
                        lat, lon = h3.cell_to_latlng(cell)
                        x = int((lon + 180) / 360 * 256)
                        y = int((1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * 256)
                        decoded = json.loads(subprocess.check_output([
                            "tippecanoe-decode", str(root / "tiles/current/priorities.pmtiles"), "8", str(x), str(y),
                        ], stderr=subprocess.DEVNULL))
                        found = [f for layer in decoded["features"] for f in layer["features"]
                                 if f["properties"]["h3_index"] == cell]
                        self.assertEqual(len(found), 1)
                        self.assertEqual(found[0]["properties"]["j"], "B|A")
                        self.assertEqual(found[0]["properties"]["a_total"], 1)

    def test_source_changes_and_incomplete_coverage_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            args, record_args, cells = prepared_fixture(root)
            source = next((record_args.h3_root / "res7_merged_parts").glob("*.parquet"))
            original = source.read_bytes()
            source.write_bytes(original[:-1] + bytes([original[-1] ^ 1]))
            with self.assertRaisesRegex(ValueError, "Prepared input changed"):
                read_prepared(args.prepared_inputs)
            source.write_bytes(original)
            original_database = record_args.build_duckdb.read_bytes()
            with duckdb.connect(str(record_args.build_duckdb)) as connection:
                connection.execute("UPDATE h3_res3_agg_all SET total_species=99")
            with self.assertRaisesRegex(ValueError, "Coarse metrics do not match"):
                record_prepared(record_args)
            record_args.build_duckdb.write_bytes(original_database)
            target = record_args.parts_dir / source.name
            target.unlink()
            with self.assertRaisesRegex(ValueError, "coverage changed"):
                read_prepared(args.prepared_inputs)
            with self.assertRaisesRegex(ValueError, "exactly"):
                record_prepared(record_args)


if __name__ == "__main__":
    unittest.main()
