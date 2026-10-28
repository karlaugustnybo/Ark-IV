from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ark_pipeline.cli.serving_prepare import main, parser, preparation_plan
from ark_pipeline.cli.spatial_pairs import DEFAULT_PROFILE
from ark_pipeline.runtime.dashboard import Dashboard
from tests.test_pipeline_dashboard import prior


def monitored_runner(callback):
    """Adapt harmless stage stand-ins to the monitored runner boundary."""
    def run(stage, output, environment, dashboard, cwd):
        callback(stage['command'], cwd=cwd, env=environment, check=True)
        return {'exit_code': 0}
    return run


class PrepareServingTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('just'), 'Recipe rendering requires just')
    def test_actual_recipes_keep_outputs_isolated_and_do_not_refresh_frozen_sources(self):
        with tempfile.TemporaryDirectory(prefix='ark isolated ') as temporary:
            root = Path(temporary).resolve()
            _, environment = preparation_plan(parser().parse_args([
                '--root', str(root / 'inputs'), '--h3-root', str(root / 'memberships'),
                '--preview-root', str(root / 'output'), '--workers', '4']))
            self.assertEqual(environment['GLOBAL_BUILD_ROOT'], str(root / 'output/build'))
            result = subprocess.run(['just', '--dry-run', 'global-prepare'],
                cwd=Path(__file__).resolve().parents[1], env={**os.environ, **environment, 'DUCKDB_MEMORY_LIMIT': '8GB', 'RES7_MEMORY_LIMIT': '2GB'},
                capture_output=True, text=True, check=True)
            commands = result.stdout + result.stderr
            self.assertNotIn('data/global', commands)
            self.assertNotIn('sources_acquire update', commands)
            self.assertIn(f'--species "{root}/output/build/species/species.parquet"', commands)
            self.assertIn(f'--output-root "{root}/output/adm2"', commands)
            self.assertIn(f'--static-data "{root}/output/static/data"', commands)
            self.assertIn(f'--metadata-template "{root}/output/build/tiles/map-metadata.json"', commands)
            # Execute the recipe against a harmless CLI stand-in to exercise
            # shell parameter expansion without running aggregation.
            binary = root / 'bin'
            binary.mkdir()
            executable = binary / 'uv'
            executable.write_text('#!/bin/sh\nprintf \'%s\\n\' "$@"\n')
            executable.chmod(0o755)
            result = subprocess.run(['just', 'global-res7-aggregate'],
                cwd=Path(__file__).resolve().parents[1],
                env={**os.environ, **environment, 'PATH': f"{binary}:{os.environ['PATH']}",
                     'DUCKDB_MEMORY_LIMIT': '8GB', 'RES7_MEMORY_LIMIT': '2GB'},
                capture_output=True, text=True, check=True)
            arguments = result.stdout.splitlines()
            self.assertEqual(arguments[arguments.index('--memory-limit') + 1], '2GB')

    def test_registered_tree_and_stewardship_inputs_reach_optional_stages(self):
        args = parser().parse_args(['--root', str(Path('/tmp/source').resolve()), '--h3-root', str(Path('/tmp/lists').resolve()),
            '--spatial-analytics', '--spatial-jurisdictions', str(Path('/tmp/boundaries.parquet').resolve()),
            '--spatial-weights', str(Path('/tmp/weights.parquet').resolve()), '--phylogenetic',
            '--phylogenetic-links-dir', str(Path('/tmp/links').resolve()), '--phylogenetic-ott-index', str(Path('/tmp/index.parquet').resolve()),
            '--phylogenetic-tree-tips', str(Path('/tmp/tips.parquet').resolve()), '--phylogenetic-aggregates',
            '--phylogenetic-scope', 'global'])
        with patch.dict('os.environ', {'DUCKDB_MEMORY_LIMIT': '256MB'}):
            commands, _ = preparation_plan(args)
        spatial, tree, aggregates = commands[1:]
        self.assertEqual(spatial[spatial.index('--jurisdictions') + 1], str(Path('/tmp/boundaries.parquet').resolve()))
        self.assertEqual(spatial[spatial.index('--weights') + 1], str(Path('/tmp/weights.parquet').resolve()))
        self.assertEqual(spatial[spatial.index('--memory-limit') + 1], '256MB')
        self.assertNotIn('--input-dir', tree)
        self.assertEqual(tree[tree.index('--ott-index') + 1], str(Path('/tmp/index.parquet').resolve()))
        self.assertEqual(tree[tree.index('--tree-tips') + 1], str(Path('/tmp/tips.parquet').resolve()))
        self.assertEqual(tree[tree.index('--scope') + 1], 'global')
        self.assertEqual(aggregates[2], 'ark_pipeline.cli.analytical_prepare')
        for flags in (['--phylogenetic'], ['--phylogenetic-aggregates'], ['--phylogenetic-scope', 'global'],
                      ['--spatial-weights', str(Path('/tmp/weights.parquet').resolve())]):
            with self.subTest(flags=flags), self.assertRaises(ValueError):
                preparation_plan(parser().parse_args(flags))

    def test_boundary_catalogues_are_forwarded_and_ambiguous_options_fail_early(self):
        flags = ['--spatial-analytics', '--workers', '4', '--spatial-workers', '2',
                 '--spatial-boundary-catalogue', f"admin0={Path('/tmp/country.geojson').resolve()}"]
        commands, _ = preparation_plan(parser().parse_args(flags))
        spatial = commands[-1]
        self.assertEqual(spatial[spatial.index('--boundary-catalogue') + 1], f"admin0={Path('/tmp/country.geojson').resolve()}")
        self.assertEqual(spatial[spatial.index('--workers') + 1], '2')
        for extra in (['--spatial-jurisdictions', str(Path('/tmp/prepared.parquet').resolve())],
                      ['--spatial-boundary-catalogue', 'admin0=/tmp/other.geojson']):
            with self.assertRaises(ValueError):
                preparation_plan(parser().parse_args(flags + extra))

    def test_optional_analyses_keep_pinned_crosswalk_and_membership_generations(self):
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(io.StringIO()):
            root = Path(tmp).resolve()
            for name in ('first', 'second'):
                (root / name).mkdir()
                (root / name / 'crosswalk.parquet').touch()
            current = root / 'current'
            current.symlink_to('first')
            received = []
            def run(command, **kwargs):
                received.append(command)
                if command == ['just', 'global-prepare']:
                    current.unlink()
                    current.symlink_to('second')
            with patch('ark_pipeline.cli.serving_prepare.load_manifest', return_value={}), \
                 patch('ark_pipeline.cli.serving_prepare.run_command', side_effect=monitored_runner(run)):
                self.assertEqual(main(['--root', str(root), '--h3-root', str(current),
                    '--crosswalk', str(current / 'crosswalk.parquet'), '--preview-root', str(root / 'out'),
                    '--dna-evidence', '--spatial-analytics', '--ui', 'plain']), 0)
            spatial, dna = received[1:]
            self.assertEqual(spatial[spatial.index('--h3-root')+1], str(root / 'first'))
            self.assertEqual(dna[dna.index('--crosswalk')+1], str(root / 'first/crosswalk.parquet'))

    def test_rich_build_tracks_nested_stages_and_drops_stale_prior_after_acquisition(self):
        with tempfile.TemporaryDirectory() as temporary, contextlib.redirect_stdout(io.StringIO()):
            root = Path(temporary).resolve()
            h3_root = root / "lists"
            h3_root.mkdir()
            crosswalk = root / "crosswalk.parquet"
            crosswalk.touch()
            displays, calls = [], []

            def make_display(*args, **kwargs):
                display = Dashboard(*args, **kwargs)
                displays.append(display)
                return display

            def run_stage(stage, output, environment, dashboard, cwd):
                calls.append(stage["name"])
                self.assertEqual(environment["SPATIAL_WORKERS"], "2")
                names = ["boundaries", "metadata", "coarse_db", "coarse_cache", "fine_metrics", "prepared_inputs"] if stage["name"] == "serving" else [stage["name"]]
                for name in names:
                    dashboard.accept({"kind": "stage_start", "stage": name, "time": 1})
                    dashboard.accept({"kind": "stage_end", "stage": name, "time": 2, "elapsed": 1, "status": "passed"})
                return {"exit_code": 0}

            with patch("ark_pipeline.cli.serving_prepare.Dashboard", side_effect=make_display), \
                 patch("ark_pipeline.cli.serving_prepare.load_prior", side_effect=[({**prior(), "path": "old"}, "Old benchmark"), (None, "Sources changed")]), \
                 patch("ark_pipeline.cli.serving_prepare.load_manifest", return_value={}), \
                 patch("ark_pipeline.cli.serving_prepare.run_command", side_effect=run_stage):
                result = main(["--root", str(root), "--h3-root", str(h3_root), "--crosswalk", str(crosswalk),
                               "--preview-root", str(root / "preview"), "--workers", "2", "--ui", "rich", "--acquire", "update", "--tiles"])
            self.assertEqual(result, 0)
            self.assertEqual(calls, ["acquisition", "serving", "tiles"])
            display = displays[0]
            self.assertTrue(all(state["status"] == "passed" for state in display.states.values()))
            self.assertIsNone(display.forecast.prior)
            self.assertIn("acquisition", display.forecast.finished)
            self.assertEqual(display.forecast.total(display.names, 3), (0, 0))

    def test_automatic_refresh_precedes_pairs_and_pins_matches_through_tiles(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, contextlib.redirect_stdout(io.StringIO()):
            root = Path(temporary).resolve()
            crosswalk_root = root / "derived/iucn-goat-crosswalk"
            first, second = crosswalk_root / "generations/first", crosswalk_root / "generations/second"
            spatial = root / "spatial"
            received = []

            def run(command, *, cwd, env, check):
                received.append((command, dict(env)))
                if command[1:3] == ["-m", "ark_pipeline.cli.crosswalk_refresh"]:
                    first.mkdir(parents=True)
                    second.mkdir()
                    (first / "iucn_goat_crosswalk.parquet").touch()
                    (second / "iucn_goat_crosswalk.parquet").touch()
                    (crosswalk_root / "current").symlink_to("generations/first")
                elif command[1:3] == ["-m", "ark_pipeline.cli.spatial_aggregate"]:
                    (spatial / "serving/current").mkdir(parents=True)
                elif command == ["just", "global-prepare"]:
                    (crosswalk_root / "current").unlink()
                    (crosswalk_root / "current").symlink_to("generations/second")

            args = ["--root", str(root), "--preview-root", str(root / "preview"),
                    "--spatial-root", str(spatial), "--crosswalk", str(root / "old-missing.parquet"),
                    "--acquire", "download", "--crosswalk-mode", "refresh", "--build-pairs", "--tiles"]
            with patch("ark_pipeline.cli.serving_prepare.run_command", side_effect=monitored_runner(run)), \
                 patch("ark_pipeline.cli.serving_prepare.load_manifest", return_value={}):
                self.assertEqual(main(args), 0)
            self.assertEqual([command[2] for command, _ in received[:4]],
                             ["ark_pipeline.cli.sources_sync", "ark_pipeline.cli.crosswalk_refresh", "ark_pipeline.cli.spatial_pairs", "ark_pipeline.cli.spatial_aggregate"])
            self.assertTrue(all(env["GLOBAL_CROSSWALK_PATH"] == str((first / "iucn_goat_crosswalk.parquet").resolve())
                                for _, env in received[2:]))

            received.clear()

            def fail_refresh(command, **kwargs):
                received.append(command)
                if command[1:3] == ["-m", "ark_pipeline.cli.crosswalk_refresh"]:
                    raise subprocess.CalledProcessError(1, command)

            with patch("ark_pipeline.cli.serving_prepare.run_command", side_effect=monitored_runner(fail_refresh)):
                self.assertEqual(main(args), 1)
            self.assertEqual(len(received), 2)

    def test_complete_build_checks_updated_crosswalk_before_pairs_and_can_resume(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, contextlib.redirect_stdout(io.StringIO()):
            root = Path(temporary).resolve()
            crosswalk = root / "crosswalk.parquet"
            crosswalk.touch()
            summary = root / "match_summary.json"
            summary.write_text(json.dumps({"sources": {"goat_species": {"sha256": "old"}}}))
            spatial = root / "spatial"
            generation = spatial / "serving/generations/new"
            received = []

            def run(command, *, cwd, env, check):
                received.append((command, dict(env)))
                if command[1:3] == ["-m", "ark_pipeline.cli.sources_sync"]:
                    manifest = root / "acquisition/current.json"
                    manifest.parent.mkdir(exist_ok=True)
                    manifest.write_text(json.dumps({"schema_version": 1, "sources": {
                        "goat-species": {"files": [{
                            "logical_name": "tol_species_all_ranks.tsv", "sha256": "new",
                        }]},
                    }}))
                elif command[1:3] == ["-m", "ark_pipeline.cli.spatial_aggregate"]:
                    generation.mkdir(parents=True)
                    (spatial / "serving/current").symlink_to("generations/new")

            args = ["--root", str(root), "--spatial-root", str(spatial),
                    "--crosswalk", str(crosswalk), "--preview-root", str(root / "preview"),
                    "--acquire", "update", "--build-pairs", "--tiles"]
            with patch("ark_pipeline.cli.serving_prepare.run_command", side_effect=monitored_runner(run)):
                self.assertEqual(main(args), 1)
                self.assertEqual(len(received), 1)
                report = json.loads((root / "preview/prepare-report.json").read_text())
                self.assertIn("crosswalk is stale for goat_species", report["error"])
                summary.write_text(json.dumps({"sources": {"goat_species": {"sha256": "new"}}}))
                received.clear()
                self.assertEqual(main(args), 0)
            self.assertEqual([command[2] for command, _ in received[:3]],
                             ["ark_pipeline.cli.sources_sync", "ark_pipeline.cli.spatial_pairs", "ark_pipeline.cli.spatial_aggregate"])
            self.assertEqual([command for command, _ in received[3:]],
                             [["just", "global-prepare"], ["just", "data-tiles"]])
            self.assertTrue(all(env["GLOBAL_H3_ROOT"] == str(generation.resolve()) for _, env in received[3:]))
            report = json.loads((root / "preview/prepare-report.json").read_text())
            self.assertEqual(report["status"], "passed")
            self.assertEqual(len(report["completed_commands"]), 5)

    def test_complete_build_preserves_acquisition_action_required_and_dry_run_is_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, contextlib.redirect_stdout(io.StringIO()):
            root = Path(temporary).resolve()
            preview = root / "preview"
            args = ["--root", str(root), "--preview-root", str(preview),
                    "--acquire", "download", "--crosswalk-mode", "refresh", "--build-pairs", "--tiles"]
            with patch("ark_pipeline.cli.serving_prepare.run_command") as run:
                self.assertEqual(main([*args, "--dry-run"]), 0)
                run.assert_not_called()
                self.assertFalse(preview.exists())
                run.side_effect = subprocess.CalledProcessError(2, ["source-acquisition"])
                self.assertEqual(main(args), 2)
                self.assertEqual(run.call_count, 1)
            report = json.loads((preview / "prepare-report.json").read_text())
            self.assertEqual(report["status"], "action-required")
            self.assertEqual(report["completed_commands"], [])

    def test_new_profile_is_selected_even_when_environment_still_points_at_legacy_pack(self) -> None:
        with patch.dict("os.environ", {"GLOBAL_H3_ROOT": "/previous/pack"}):
            args = parser().parse_args(["--root", str(Path('/tmp/new release').resolve()), "--profile", str(DEFAULT_PROFILE)])
            commands, environment = preparation_plan(args)
        self.assertEqual(commands[0][1:4], ["-m", "ark_pipeline.cli.spatial_aggregate", "run"])
        self.assertEqual(commands[-1], ["just", "global-prepare"])
        self.assertEqual(environment["GLOBAL_H3_ROOT"], str(
            Path(str(Path('/tmp/new release/derived/iucn-richness-any-touch-v3/serving/current').resolve())).resolve()
        ))
        self.assertEqual(commands[0][5], str(Path(str(Path('/tmp/new release').resolve())).resolve()))

    def test_explicit_legacy_pack_skips_export(self) -> None:
        args = parser().parse_args(["--h3-root", str(Path('/tmp/old lists').resolve())])
        commands, environment = preparation_plan(args)
        self.assertEqual(commands, [["just", "global-prepare"]])
        self.assertEqual(environment["GLOBAL_H3_ROOT"], str(Path(str(Path('/tmp/old lists').resolve())).resolve()))

    def test_preparation_pins_the_generation_created_by_export(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            crosswalk = root / "crosswalk.parquet"
            crosswalk.touch()
            spatial = root / "spatial"
            generation = spatial / "serving/generations/new"
            generation.mkdir(parents=True)
            received = []

            def run(command, *, cwd, env, check):
                received.append((command, env["GLOBAL_H3_ROOT"]))
                if len(received) == 1:
                    (spatial / "serving/current").symlink_to("generations/new")

            with patch("ark_pipeline.cli.serving_prepare.run_command", side_effect=monitored_runner(run)), \
                 patch("ark_pipeline.cli.serving_prepare.load_manifest", return_value={}), \
                 contextlib.redirect_stdout(io.StringIO()):
                status = main([
                    "--root", str(root), "--spatial-root", str(spatial),
                    "--crosswalk", str(crosswalk), "--preview-root", str(root / "preview"),
                ])
            self.assertEqual(status, 0)
            self.assertEqual(len(received), 2)
            self.assertEqual(received[-1][1], str(generation.resolve()))
            self.assertTrue((root / "preview/prepare-report.json").is_file())
            report = json.loads((root / 'preview/prepare-report.json').read_text())
            recording = Path(report['progress_directory'])
            self.assertTrue((recording / 'dashboard-state.json').is_file())
            self.assertEqual(len(report['stage_measurements']), len(received))

    def test_missing_crosswalk_stops_before_export_and_dry_run_does_not_build(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, \
             patch("ark_pipeline.cli.serving_prepare.run_command") as run, \
             contextlib.redirect_stdout(io.StringIO()):
            args = ["--root", temporary, "--crosswalk", str(Path(temporary) / "absent"),
                    "--preview-root", str(Path(temporary) / "preview")]
            self.assertEqual(main(args), 1)
            self.assertEqual(main([*args, "--dry-run"]), 0)
            run.assert_not_called()

    def test_tiles_continue_with_the_same_pinned_generation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, contextlib.redirect_stdout(io.StringIO()):
            root = Path(temporary).resolve()
            first, second = root / "first", root / "second"
            first.mkdir()
            second.mkdir()
            current = root / "current"
            current.symlink_to(first)
            crosswalk = root / "crosswalk"
            crosswalk.touch()
            received = []

            def run(command, *, cwd, env, check):
                received.append((command, env["GLOBAL_H3_ROOT"]))
                if command == ["just", "global-prepare"]:
                    current.unlink()
                    current.symlink_to(second)

            with patch("ark_pipeline.cli.serving_prepare.run_command", side_effect=monitored_runner(run)), \
                 patch("ark_pipeline.cli.serving_prepare.load_manifest", return_value={}):
                status = main(["--root", str(root), "--h3-root", str(current),
                               "--crosswalk", str(crosswalk), "--preview-root", str(root / "preview"), "--tiles"])
            self.assertEqual(status, 0)
            self.assertEqual(received, [(["just", "global-prepare"], str(first.resolve())),
                                        (["just", "data-tiles"], str(first.resolve()))])


if __name__ == "__main__":
    unittest.main()
