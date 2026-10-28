"""CoreWeave run monitor: remote telemetry reads, phase-aware ETAs and baselines."""

from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
import tarfile
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from rich.console import Console

SCRIPTS = Path(__file__).resolve().parents[1] / "sandbox/coreweave/scripts"
sys.path.insert(0, str(SCRIPTS))
import cloud_run  # noqa: E402
import watch_run  # noqa: E402

RESOURCES = {"workers": 8, "spatial_workers": 8, "metric_workers": 8, "duckdb_threads": 4,
             "metric_threads": 1, "tile_threads": 4, "tile_duckdb_threads": 4}


def local_execute(argv):
    return subprocess.run([sys.executable, "-I", *argv[1:]], capture_output=True, text=True, check=True).stdout


def baseline(**changes):
    return {"label": "fixture", "path": "fixture.json", "resources": RESOURCES, "workload": {},
            "order": ["metadata", "tiles"], "stages": {"restore": 60.0, "metadata": 100.0, "tiles": 150.0, "retention": 30.0},
            "phases": {"tiles": [["Validate", 100.0], ["Compile", 50.0]]}, **changes}


class SpatialCalibrationTests(unittest.TestCase):
    def test_monitor_uses_verified_stage_phases_without_rewriting_historical_timings(self):
        guide = baseline(mode='fast', stages={'spatial_analytics': 1000, 'pairs': 1}, phases={},
            reconstructed_stages={'pairs': {'estimated_seconds': 20}},
            spatial_calibration={'mode': 'fast', 'code_sha256': 'verified', 'estimated_seconds': 10,
                                 'source': 'full-input stage benchmark', 'limitations': 'Frozen inputs',
                                 'phases': [['Verify', 2], ['Reduce', 8]]})
        with tempfile.TemporaryDirectory() as temporary, patch(
            'ark_pipeline.runtime.benchmark_estimates.code_fingerprint', return_value='verified'
        ):
            display = watch_run.CloudDashboard(['pairs', 'spatial_analytics'], Path(temporary), RESOURCES, guide, ui='plain')
            self.assertEqual(display.forecast.base['spatial_analytics'], 10)
            self.assertEqual(display.forecast.phase_base['spatial_analytics'], [['Verify', 2], ['Reduce', 8]])
            self.assertEqual(guide['stages']['spatial_analytics'], 1000)
            self.assertEqual(display.forecast.base['pairs'], 20)
            self.assertEqual(guide['stages']['pairs'], 1)
            fallback = watch_run.CloudDashboard(['pairs', 'spatial_analytics'], Path(temporary), RESOURCES,
                                               {**guide, 'note_mode': True}, ui='plain')
            self.assertEqual(fallback.forecast.base['spatial_analytics'], 1000)
            self.assertEqual(fallback.forecast.base['pairs'], 1)


class RemoteRun:
    """A sandbox-shaped directory tree read by the real remote snippet."""

    def __init__(self, root: Path, started: float):
        self.preview, self.run = root / "preview", root / "preview/runs/attempt"
        self.journal = root / "status.json"
        self.run.mkdir(parents=True)
        self.started = started

    def write(self, *, report_age=0.0, build=True, events=(), sample=None):
        steps = [{"name": "full-build", "status": "running", "started_epoch": self.started + 60}] if build else []
        self.journal.write_text(json.dumps({"status": "running", "started_epoch": self.started, "steps": steps}))
        report = self.preview / "prepare-report.json"
        report.write_text(json.dumps({"status": "running", "progress_directory": str(self.run), "resources": RESOURCES}))
        os.utime(report, (self.started + 60 + report_age,) * 2)
        (self.run / "dashboard-state.json").write_text(json.dumps({"states": {"metadata": {}, "tiles": {}}}))
        with (self.run / "progress.jsonl").open("a") as stream:
            stream.writelines(json.dumps(event) + "\n" for event in events)
        if sample:
            (self.run / "resources.jsonl").write_text(json.dumps(sample) + "\n")


class TelemetryTests(unittest.TestCase):
    def test_poller_mirrors_events_incrementally_and_ignores_previous_attempts(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            remote = RemoteRun(root, time.time() - 600)
            arguments = SimpleNamespace(journal=str(remote.journal), preview_root=str(remote.preview))
            poller = watch_run.Poller(local_execute, arguments, root / "mirror", 1)
            remote.write(report_age=-120)  # restored report from an earlier attempt
            poller.poll()
            self.assertIsNone(poller.snapshot["run"])
            start = {"stage": "metadata", "kind": "stage_start", "time": remote.started + 61}
            remote.write(events=[start], sample={"time": 1, "cpu": 50, "memory.current": "8", "memory.max": "16"})
            poller.poll()
            remote.write(events=[{**start, "kind": "phase", "phase": "Scan"}])
            poller.poll()
            mirrored = (root / "mirror/attempt/progress.jsonl").read_text().splitlines()
            self.assertEqual([json.loads(line)["kind"] for line in mirrored], ["stage_start", "phase"])
            self.assertEqual(poller.snapshot["names"], ["metadata", "tiles"])
            self.assertEqual(poller.snapshot["sample"]["memory.max"], "16")
            self.assertTrue(poller.snapshot["caught_up"])
            self.assertLess(abs(poller.clock_offset), 5)

    def test_poller_stops_when_the_sandbox_has_ended(self):
        def execute(argv):
            raise watch_run.RunEnded
        arguments = SimpleNamespace(journal="", preview_root="/nowhere")
        poller = watch_run.Poller(execute, arguments, Path(tempfile.mkdtemp()), 0)
        poller.run_forever()  # returns instead of retrying forever
        self.assertTrue(poller.ended)

    def test_once_renders_cloud_run_with_journal_stages_and_container_memory(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            now = time.time()
            remote = RemoteRun(root, now - 100)
            remote.write(events=[{"stage": "metadata", "kind": "command_start", "time": now - 30}],
                         sample={"time": now, "cpu": 25.0, "rss": 2**30, "cores": 1.5,
                                 "memory.current": str(8 * 2**30), "memory.max": str(32 * 2**30),
                                 "scratch_logical_bytes": {"/scratch": 5 * 2**30}})
            path = root / "baseline.json"
            path.write_text(json.dumps(baseline()))
            output = io.StringIO()
            console = Console(file=output, width=130, height=45, force_terminal=False)
            arguments = SimpleNamespace(journal=str(remote.journal), preview_root=str(remote.preview), baseline=path,
                                        mirror=root / "mirror", poll_seconds=1, once=True, keep_open=False, sandbox_id="fixture")
            self.assertEqual(watch_run.watch(local_execute, arguments, console=console), 0)
            frame = output.getvalue()
            self.assertIn("Restore inputs & plan", frame)
            self.assertIn("Last run", frame)
            self.assertIn("8.0 / 32.0 GiB", frame)
            self.assertIn("Scratch 5 GiB", frame)
            # Remaining = metadata 70 s + tiles 150 s + retention 30 s.
            self.assertIn("0:04:10", frame)
            self.assertIn("0:01:10", frame)  # metadata: 100 s baseline, 30 s elapsed


class ForecastTests(unittest.TestCase):
    def forecast(self):
        model = watch_run.PhaseForecast(None, RESOURCES, baseline())
        model.base = {"tiles": 150.0}
        model.accept({"stage": "tiles", "kind": "stage_start", "time": 0})
        return model

    def test_phase_baselines_drive_stage_remaining(self):
        model = self.forecast()
        model.accept({"stage": "tiles", "kind": "phase", "phase": "Validate", "time": 0})
        self.assertAlmostEqual(model.remaining("tiles", 30), 70 + 50)
        model.accept({"stage": "tiles", "kind": "phase", "phase": "Compile", "time": 90})
        self.assertAlmostEqual(model.remaining("tiles", 100), 40)

    def test_overdue_phase_without_work_rate_is_unknown_not_zero(self):
        model = self.forecast()
        model.accept({"stage": "tiles", "kind": "phase", "phase": "Compile", "time": 0})
        self.assertIsNone(model.remaining("tiles", 80))

    def test_measured_work_rate_replaces_phase_baseline(self):
        model = self.forecast()
        model.accept({"stage": "tiles", "kind": "phase", "phase": "Validate", "time": 0})
        for moment, completed in ((10, 25), (20, 50)):
            model.accept({"stage": "tiles", "kind": "work", "overall": True, "completed": completed,
                          "total": 100, "time": moment})
        # Half done after 20 s: 20 s live remaining instead of the 80 s baseline, plus Compile.
        self.assertAlmostEqual(model.remaining("tiles", 20), 20 + 50)


class BaselineTests(unittest.TestCase):
    def test_baseline_uses_stage_or_command_records_phases_and_journal(self):
        with tempfile.TemporaryDirectory() as temporary:
            events = Path(temporary) / "progress.jsonl"
            records = [
                {"stage": "tiles", "kind": "stage_start", "time": 100},
                {"stage": "tiles", "kind": "phase", "phase": "Validate", "time": 102},
                {"stage": "tiles", "kind": "phase", "phase": "Compile", "time": 140},
                {"stage": "tiles", "kind": "stage_end", "status": "passed", "elapsed": 60, "time": 160},
                {"stage": "tiles", "kind": "command_end", "status": "passed", "wall_seconds": 61, "time": 161},
                {"stage": "dna_evidence", "kind": "command_start", "time": 161},
                {"stage": "dna_evidence", "kind": "stage_start", "time": 161},
                {"stage": "dna_evidence", "kind": "command_end", "status": "passed", "wall_seconds": 9, "time": 170},
            ]
            events.write_text("".join(json.dumps(record) + "\n" for record in records))
            journal = {"started_epoch": 0, "finished_epoch": 200,
                       "steps": [{"name": "full-build", "started_epoch": 90, "seconds": 85}]}
            result = watch_run.build_baseline(events, label="fixture", resources=RESOURCES, journal=journal)
        self.assertEqual(result["order"], ["tiles", "dna_evidence"])
        self.assertEqual(result["stages"], {"tiles": 60, "dna_evidence": 9, "restore": 90, "retention": 25})
        self.assertEqual(result["phases"]["tiles"], [[None, 2], ["Validate", 38], ["Compile", 20]])

    def test_latest_baseline_prefers_the_same_coverage_mode(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            for name, mode, created in (("fast", "fast", "2026-01-02"), ("precise", "precise", "2026-01-01")):
                (directory / f"{name}.json").write_text(json.dumps({"mode": mode, "created_at": created}))
            self.assertEqual(watch_run.latest_baseline(directory, mode="precise").name, "precise.json")
            self.assertEqual(watch_run.latest_baseline(directory, mode="fast").name, "fast.json")
            (directory / "precise.json").unlink()
            self.assertEqual(watch_run.latest_baseline(directory, mode="precise").name, "fast.json")

    def test_two_watchers_save_one_baseline_per_run(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "mirror/attempt").mkdir(parents=True)
            (root / "mirror/attempt/progress.jsonl").write_text("")
            snapshot = {"run": "/remote/attempt", "resources": RESOURCES, "journal": {"started_epoch": 0}}
            arguments = SimpleNamespace(sandbox_id="abcdef12-run")
            for _ in range(2):
                watch_run.save_baseline(snapshot, root / "mirror", arguments, directory=root / "baselines")
            self.assertEqual([p.name for p in (root / "baselines").iterdir()], ["1970-01-01-abcdef12-run.json"])

    def test_checked_in_baseline_covers_every_full_preparation_stage(self):
        data = json.loads(watch_run.latest_baseline().read_text())
        self.assertEqual(set(data["stages"]), {"restore", "retention", *data["order"]})
        self.assertIn("spatial_analytics", data["order"])


class LauncherTests(unittest.TestCase):
    def follow(self, watch_code):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            ledger = root / "cloud-runs.json"
            ledger.write_text(json.dumps({"runs": [{"sandbox_id": "sandbox", "status": "running"}]}))
            with (patch.object(cloud_run, "watch_run", SimpleNamespace(
                      latest_baseline=lambda mode: Path("b.json"), sandbox_executor=lambda sandbox_id: None,
                      watch=lambda execute, options: watch_code)),
                  patch.object(cloud_run, "REPOSITORY", root), patch.object(cloud_run, "LEDGER", ledger),
                  patch("cloud_storage.credentials", lambda: None),
                  patch.object(cloud_run, "stop_sandbox", lambda *a, **k: self.fail("watchers never stop the run")),
                  contextlib.redirect_stdout(io.StringIO())):
                code = cloud_run.follow("sandbox", "fast")
            return code, json.loads(ledger.read_text())["runs"][0]["status"]

    def test_watching_records_the_outcome_without_stopping_the_sandbox(self):
        self.assertEqual(self.follow(0), (0, "passed"))
        self.assertEqual(self.follow(1), (1, "failed"))

    def test_ctrl_c_detaches_and_leaves_the_run_going(self):
        self.assertEqual(self.follow(130), (130, "running"))

    def test_sandbox_main_command_waits_for_upload_and_exits_after_the_run(self):
        self.assertIn("Sandbox.run(\"bash\", \"-c\", ENTRY", Path(cloud_run.__file__).read_text())
        lines = [line for line in cloud_run.ENTRY.splitlines() if line]
        self.assertIn("bootstrap.sh", lines[0])
        self.assertEqual(lines[-1], "exit $code")

    def test_payload_ships_working_tree_and_private_extras_but_refuses_credentials(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            (root / ".gitignore").write_text("private/\n")
            (root / "tracked.py").write_text("x = 1\n")
            (root / "private").mkdir()
            (root / "private/extra.json").write_text("{}")
            (root / "private/other.json").write_text("{}")
            with patch.object(cloud_run, "REPOSITORY", root):
                subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init", "--allow-empty"],
                               cwd=root, check=True)
                data, identity = cloud_run.payload(["private/extra.json"])
                with tarfile.open(fileobj=io.BytesIO(data)) as archive:
                    self.assertEqual(archive.getnames(), [".gitignore", "private/extra.json", "tracked.py"])
                self.assertTrue(identity["dirty"])
                (root / "tracked.py").write_text("token = 'ghp_" + "a" * 36 + "'\n")
                with self.assertRaises(SystemExit):
                    cloud_run.payload([])


if __name__ == "__main__":
    unittest.main()
