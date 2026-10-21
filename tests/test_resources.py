from __future__ import annotations

import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import duckdb
import pyarrow as pa

from ark_pipeline.builders.fine_metrics import configure_connection
from ark_pipeline.builders.fine_metrics import parser as metric_parser
from ark_pipeline.cli.serving_prepare import main, parser, preparation_plan
from ark_pipeline.cli.serving_tiles import parser as tile_parser
from ark_pipeline.cli.spatial_pairs import build_parser as spatial_parser
from ark_pipeline.runtime.resources import (
    configure_duckdb,
    configured_count,
    effective_limits,
    resolve_resources,
)


class PipelineResourceTests(unittest.TestCase):
    def test_effective_limits_respect_cgroup_quota_and_memory(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'cpu.max').write_text('200000 100000')
            (root / 'memory.max').write_text(str(8 * 1024**3))
            (root / 'memory.current').write_text(str(3 * 1024**3))
            limits = effective_limits(root)
            self.assertLessEqual(limits['cpus'], 2)
            self.assertLessEqual(limits['memory_bytes'], 8 * 1024**3)
            self.assertLessEqual(limits['available_memory_bytes'], 5 * 1024**3)

    def test_clean_inactive_file_cache_does_not_disable_workers_after_staging(self):
        gib = 1024**3
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'memory.max').write_text(str(32 * gib))
            (root / 'memory.current').write_text(str(31 * gib))
            (root / 'memory.stat').write_text(
                f'inactive_file {20*gib}\nfile_dirty {2*gib}\nfile_writeback {gib}\n')
            with patch('psutil.virtual_memory', return_value=SimpleNamespace(total=64*gib, available=40*gib)):
                limits = effective_limits(root)
            self.assertEqual(limits['available_memory_bytes'], 18 * gib)
            # Malformed or missing counters retain the conservative fallback.
            (root / 'memory.stat').write_text('inactive_file unavailable\n')
            self.assertLessEqual(effective_limits(root)['available_memory_bytes'], gib)

    def test_auto_uses_one_memory_aware_budget_and_keeps_helper_threads_small(self) -> None:
        with patch("ark_pipeline.runtime.resources.automatic_workers", return_value=2):
            resources = resolve_resources(environ={})
        self.assertEqual(resources.report(), {
            "workers": 2, "spatial_workers": 2, "metric_workers": 2, "duckdb_threads": 2,
            "metric_threads": 1, "tile_threads": 2, "tile_duckdb_threads": 1,
        })

    def test_shared_cli_overrides_old_environment_and_stage_cli_remains_specific(self) -> None:
        old = {"PIPELINE_WORKERS": "8", "SPATIAL_WORKERS": "2", "RES7_WORKERS": "2",
               "DUCKDB_THREADS": "1", "RES7_THREADS": "3", "TIPPECANOE_MAX_THREADS": "2"}
        resources = resolve_resources(workers=4, environ=old)
        self.assertEqual((resources.spatial_workers, resources.metric_workers, resources.duckdb_threads, resources.tile_threads), (4, 4, 4, 4))
        self.assertEqual(resources.metric_threads, 1)
        specific = resolve_resources(workers=4, metric_workers=2, tile_threads=3, environ=old)
        self.assertEqual((specific.spatial_workers, specific.metric_workers, specific.duckdb_threads, specific.tile_threads), (4, 2, 4, 3))
        saved = resolve_resources(environ=old)
        self.assertEqual((saved.workers, saved.spatial_workers, saved.duckdb_threads), (8, 2, 1))

    def test_actual_stage_parsers_receive_shared_settings_without_squared_metric_threads(self) -> None:
        with patch.dict("os.environ", {"PIPELINE_WORKERS": "auto", "DUCKDB_THREADS": "1"}, clear=True):
            _, environment = preparation_plan(parser().parse_args(["--workers", "4"]))
            with patch.dict("os.environ", environment):
                spatial = spatial_parser().parse_args(["build"])
                metric = metric_parser().parse_args([
                    "aggregate", "--parts-dir", "/tmp/parts", "--species", "/tmp/species",
                    "--species-systems", "/tmp/systems", "--output-dir", "/tmp/out", "--scratch-dir", "/tmp/scratch",
                ])
                tiles = tile_parser().parse_args([
                    "build", "--prepared-inputs", "/tmp/prepared", "--output-dir", "/tmp/tiles", "--scratch-dir", "/tmp/scratch",
                ])
                with duckdb.connect() as connection:
                    configure_duckdb(connection)
                    self.assertEqual(connection.execute("SELECT current_setting('threads')").fetchone()[0], 4)
        self.assertEqual(spatial.workers, 4)
        self.assertEqual((metric.workers, metric.threads), (4, 1))
        self.assertEqual((tiles.tile_threads, tiles.threads), (4, 1))

    def test_standalone_commands_honor_shared_environment_and_stage_overrides(self) -> None:
        with patch.dict("os.environ", {"PIPELINE_WORKERS": "3"}, clear=True):
            self.assertEqual(configured_count("SPATIAL_WORKERS"), 3)
            self.assertEqual(configured_count("DUCKDB_THREADS"), 3)
            self.assertEqual(configured_count("RES7_THREADS", default=1), 1)
            with patch.dict("os.environ", {"SPATIAL_WORKERS": "2"}):
                self.assertEqual(spatial_parser().parse_args(["build"]).workers, 2)

    def test_metric_arrow_pool_obeys_per_worker_thread_limit(self) -> None:
        previous = pa.cpu_count()
        try:
            with tempfile.TemporaryDirectory() as temporary, duckdb.connect() as connection:
                configure_connection(connection, scratch_dir=Path(temporary), memory_limit="128MB", threads=1)
                self.assertEqual(pa.cpu_count(), 1)
                self.assertEqual(connection.execute("SELECT current_setting('threads')").fetchone()[0], 1)
        finally:
            pa.set_cpu_count(previous)

    def test_shared_connection_applies_configured_memory_and_isolates_spill_paths(self):
        with tempfile.TemporaryDirectory() as temporary, patch.dict('os.environ', {
            'DUCKDB_MEMORY_LIMIT': '128MB', 'DUCKDB_SCRATCH_DIR': temporary,
        }), duckdb.connect() as first, duckdb.connect() as second:
            configure_duckdb(first, threads=1)
            configure_duckdb(second, threads=1, memory_limit='256MB')
            self.assertEqual(first.execute("SELECT current_setting('memory_limit')").fetchone()[0], '122.0 MiB')
            self.assertEqual(second.execute("SELECT current_setting('memory_limit')").fetchone()[0], '244.1 MiB')
            paths = [Path(c.execute("SELECT current_setting('temp_directory')").fetchone()[0])
                     for c in (first, second)]
            self.assertNotEqual(*paths)
            self.assertTrue(all(path.is_dir() and path.is_relative_to(temporary) for path in paths))

    def test_invalid_counts_fail_before_acquisition_or_builds(self) -> None:
        with patch("ark_pipeline.cli.serving_prepare.subprocess.run") as run, \
             contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            for flag in ("--workers", "--spatial-workers", "--metric-workers", "--duckdb-threads", "--metric-threads", "--tile-threads", "--tile-duckdb-threads"):
                with self.subTest(flag=flag), self.assertRaises(SystemExit):
                    main([flag, "0", "--acquire", "download"])
            with patch.dict("os.environ", {"PIPELINE_WORKERS": "-1"}):
                self.assertEqual(main(["--acquire", "download"]), 1)
            run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
