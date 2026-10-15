"""Offline tooling contracts: only temporary synthetic bytes and recording clients."""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "sandbox/coreweave/scripts"
sys.path.insert(0, str(SCRIPTS))
import analyze_profile
import cloud_census
import cloud_storage as storage
import cost_model
import full_pipeline_job as job
import full_run
import launch
import payload
import preflight_checks
import profile_benchmark_job as profiler
import restore_serving


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))
    return path


def record(path, data=b"synthetic", *, relative="output.bin"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return {
        "path": relative,
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "artifact": "fixture/project/file:v1",
    }


class Future:
    def __init__(self, value=None):
        self.value = value

    def result(self):
        return self.value


class PayloadTests(unittest.TestCase):
    def test_deterministic_pack_and_authorized_input_boundary(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            source = root / "source.py"
            rec = record(source, b'print("synthetic")\n')
            entry = {
                "root": str(root),
                "path": source.name,
                "destination": "code/source.py",
                "role": "code",
                "sha256": rec["sha256"],
            }
            manifest = write_json(root / "payload.json", {"files": [entry]})
            first, identity = payload.build_payload(manifest)
            self.assertEqual(first, payload.build_payload(manifest)[0])
            with tarfile.open(fileobj=io.BytesIO(first)) as archive:
                self.assertEqual(archive.getnames(), ["code/source.py"])
                self.assertEqual(archive.extractfile("code/source.py").read(), source.read_bytes())
                self.assertEqual(archive.getmembers()[0].mode, 0o644)
            self.assertEqual(identity["sha256"], hashlib.sha256(first).hexdigest())
            entry.update(
                role="authorized-input",
                provenance="invented fixture",
                transfer_authorization="local fake test",
            )
            write_json(manifest, {"files": [entry]})
            with self.assertRaises(ValueError):
                payload.build_payload(manifest)
            payload.build_payload(manifest, allow_authorized_data=True)
            source.write_text("changed")
            with self.assertRaisesRegex(ValueError, "changed"):
                payload.build_payload(manifest, allow_authorized_data=True)

    def test_unsafe_paths_symlinks_credentials_and_duplicate_names_reject(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            rec = record(root / "source.py")
            entry = {
                "root": str(root),
                "path": "source.py",
                "destination": "source.py",
                "role": "code",
                "sha256": rec["sha256"],
            }
            manifest = root / "manifest.json"
            for bad in [
                "../escape.py",
                "/escape.py",
                ".env",
                "results/report.py",
                "a\\b.py",
                "./source.py",
            ]:
                with self.subTest(bad=bad):
                    write_json(manifest, {"files": [{**entry, "destination": bad}]})
                    with self.assertRaises(ValueError):
                        payload.build_payload(manifest)
            write_json(manifest, {"files": [entry, entry]})
            with self.assertRaisesRegex(ValueError, "Duplicate"):
                payload.build_payload(manifest)
            (root / "link.py").symlink_to(root / "source.py")
            write_json(manifest, {"files": [{**entry, "path": "link.py"}]})
            with self.assertRaisesRegex(ValueError, "symlink"):
                payload.build_payload(manifest)
            # Deliberately synthetic pattern, never a real credential.
            secret = ("ghp_" + "x" * 40).encode()
            (root / "source.py").write_bytes(secret)
            write_json(
                manifest, {"files": [{**entry, "sha256": hashlib.sha256(secret).hexdigest()}]}
            )
            with self.assertRaisesRegex(ValueError, "credential"):
                payload.build_payload(manifest)

    def test_local_plan_never_imports_sdk_or_reads_credentials(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            rec = record(root / "source.py")
            manifest = write_json(
                root / "manifest.json",
                {
                    "files": [
                        {
                            "root": str(root),
                            "path": "source.py",
                            "destination": "source.py",
                            "role": "code",
                            "sha256": rec["sha256"],
                        }
                    ]
                },
            )
            commands = write_json(
                root / "commands.json", [{"name": "tiny", "command": ["true"], "timeout": 10}]
            )
            args = [
                "--manifest",
                str(manifest),
                "--commands",
                str(commands),
                "--output",
                str(root / "out"),
                "--image",
                "synthetic-image",
                "--cpu-rate",
                "1",
                "--memory-rate",
                "1",
            ]
            with (
                patch.dict(sys.modules, {"cwsandbox": None}),
                patch.object(launch, "sdk_options", side_effect=AssertionError("SDK touched")),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                self.assertEqual(full_run.main(args), 0)
                self.assertFalse((root / "out").exists())
                write_json(commands, [{"name": "../bad", "command": ["true"], "timeout": 10}])
                with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
                    full_run.main(args)


class FakeSandbox:
    sandbox_id = "synthetic-id"

    def __init__(self, fail=False):
        self.calls = []
        self.closed = False
        self.fail = fail

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.closed = True

    def exec(self, command, **kwargs):
        self.calls.append((command, kwargs))
        return Future(
            SimpleNamespace(
                returncode=int(self.fail and command == ["fail"]), stdout="synthetic", stderr=""
            )
        )

    def write_file_streaming(self, path, data, **kwargs):
        self.calls.append((path, data, kwargs))
        return Future()

    def read_file(self, path, **kwargs):
        return Future(b'{"synthetic":true}')


class LifecycleTests(unittest.TestCase):
    def test_recording_sdk_success_failure_recovery_and_teardown(self):
        with tempfile.TemporaryDirectory() as temp:
            for fail in [False, True]:
                with self.subTest(fail=fail):
                    sandbox = FakeSandbox(fail)
                    output = Path(temp) / str(fail)
                    command = ["fail"] if fail else ["true"]

                    def run():
                        return launch.run_remote(
                            b"code",
                            {"sha256": "synthetic"},
                            [{"name": "job", "command": command, "timeout": 5}],
                            output,
                            {"resources": {"cpu": "2"}},
                            sandbox_factory=lambda **kwargs: sandbox,
                            recover=[("/scratch/report.json", "report.json")],
                        )

                    if fail:
                        with self.assertRaises(RuntimeError):
                            run()
                    else:
                        run()
                    self.assertTrue(sandbox.closed)
                    self.assertEqual((output / "report.json").read_bytes(), b'{"synthetic":true}')
                    report = json.loads((output / "driver-report.json").read_text())
                    self.assertEqual(report["status"], "failed" if fail else "passed")
                    self.assertEqual(report["stages"][-1]["exit_code"], int(fail))
                    self.assertTrue(
                        all(
                            call[1]["timeout_seconds"] > 0
                            for call in sandbox.calls
                            if isinstance(call[0], list)
                        )
                    )

    def test_cost_and_sdk_option_wiring_without_credentials_or_network(self):
        self.assertEqual(launch.cost_plan(2, 4, 1800, 1, 0.5)["maximum_compute_scenario_usd"], 2)
        for value in [0, -1, float("nan"), float("inf")]:
            with self.assertRaises(ValueError):
                launch.cost_plan(2, 4, 10, value, 1)
        fake = SimpleNamespace(
            AuthStrategy=SimpleNamespace(WANDB="fake-auth"),
            DataPlaneMode=SimpleNamespace(GATEWAY="fake-mode"),
            ScratchVolumeOptions=lambda **kwargs: kwargs,
        )
        with (
            patch.dict(sys.modules, {"cwsandbox": fake}),
            patch.dict("os.environ", {"WANDB_API_KEY": "synthetic-test-value"}, clear=True),
        ):
            options = launch.sdk_options(launch.cost_plan(2, 4, 600, 1, 1), 8, "image-digest")
        self.assertEqual(options["resources"], {"cpu": "2", "memory": "4Gi"})
        self.assertEqual(options["volumes"][0]["size"], "8Gi")
        self.assertEqual(options["max_lifetime_seconds"], 600)

    def test_disk_probe_tolerates_only_disappearing_files_and_deadline_prevents_launch(self):
        with patch.object(
            job.subprocess,
            "run",
            return_value=SimpleNamespace(
                returncode=1,
                stdout="123 /scratch\n",
                stderr="du: vanished: No such file or directory\n",
            ),
        ):
            self.assertEqual(job.disk_bytes(), 123)
        with patch.object(
            job.subprocess,
            "run",
            return_value=SimpleNamespace(
                returncode=1, args=["du"], stdout="123 /scratch\n", stderr="permission denied"
            ),
        ):
            with self.assertRaises(subprocess.CalledProcessError):
                job.disk_bytes()
        with (
            tempfile.TemporaryDirectory() as temp,
            patch.object(job, "OUT", Path(temp)),
            patch.object(
                job.subprocess, "Popen", side_effect=AssertionError("Launched after deadline")
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "deadline"):
                job.run_command("synthetic", ["true"], {}, {"stages": []}, 0)


class StorageTests(unittest.TestCase):
    def test_manifest_hashes_caps_and_explicit_root(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            item = record(root / "source.bin", relative="source.bin")
            write_json(
                root / "acquisition/current.json", {"sources": {"synthetic": {"files": [item]}}}
            )
            self.assertEqual(preflight_checks.check(root)["files"], 2)
            (root / "source.bin").write_bytes(b"tampered!")
            with self.assertRaises(ValueError):
                preflight_checks.check(root)
            with self.assertRaises(ValueError), patch.dict(sys.modules, {"wandb": None}):
                storage.main(["upload-inputs", "--root", str(root)])
            with self.assertRaises(ValueError), patch.dict(sys.modules, {"wandb": None}):
                storage.main(["upload-inputs", "--root", str(root), "--dry-run", "--max-gb", "nan"])

    def test_entire_inventory_and_staged_bytes_checked_before_replace(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            item = record(root / "expected", relative="a/payload.bin")

            class Entry:
                def download(self, root, **kwargs):
                    p = Path(root) / "payload"
                    p.write_bytes(b"synthetic")
                    return str(p)

            api = SimpleNamespace(
                artifact=lambda identity: SimpleNamespace(
                    state="COMMITTED", get_entry=lambda name: Entry()
                )
            )
            self.assertEqual(storage.download_file(api, root, item), "verified")
            self.assertEqual(storage.download_file(api, root, item), "reused")
            storage.verify_inventory(root, {"files": [item]})
            for bad in ["../escape", "/escape", ".env", "_cloud_storage/payload", "./payload"]:
                with self.subTest(bad=bad), self.assertRaises(ValueError):
                    storage.validate_inventory(root, {"files": [{**item, "path": bad}]})
            with self.assertRaises(ValueError):
                storage.validate_inventory(root, {"files": [item, item]})
            (root / "link").symlink_to(root / "a", target_is_directory=True)
            with self.assertRaises(ValueError):
                storage.validate_inventory(root, {"files": [{**item, "path": "link/payload"}]})
            corrupt = {**item, "path": "new.bin", "sha256": "0" * 64}
            with self.assertRaisesRegex(ValueError, "checksum"):
                storage.download_file(api, root, corrupt)
            self.assertFalse((root / "new.bin").exists())
            pending = SimpleNamespace(artifact=lambda identity: SimpleNamespace(state="PENDING"))
            with self.assertRaises(storage.TransferError):
                storage.download_file(pending, root, {**item, "path": "pending.bin"})

    def test_committed_identity_and_error_sanitization(self):
        artifact = SimpleNamespace(
            state="COMMITTED",
            metadata={"sha256": "abc"},
            get_entry=lambda name: SimpleNamespace(size=3),
        )
        api = SimpleNamespace(artifact_exists=lambda path: True, artifact=lambda path: artifact)
        self.assertIs(storage.committed_file(api, "synthetic:v1", "abc", 3), artifact)
        with self.assertRaises(ValueError):
            storage.committed_file(api, "synthetic:v1", "wrong", 3)
        artifact.state = "PENDING"
        self.assertIsNone(storage.committed_file(api, "synthetic:v1", "abc", 3))

        def fail(**kwargs):
            raise RuntimeError("private signed request details")

        with self.assertRaises(storage.TransferError) as caught:
            storage.wait_for_commit(SimpleNamespace(wait=fail))
        self.assertNotIn("signed", str(caught.exception))

    def test_fake_upload_catalog_commit_and_reuse(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            item = record(root / "input.bin", relative="input.bin")
            events = []

            class Artifact:
                def __init__(self, name, type=None, metadata=None):
                    self.name = name + ":v1"
                    self.version = "v1"
                    self.metadata = metadata
                    self.state = "PENDING"

                def add_file(self, path, **kwargs):
                    events.append(("add", kwargs["name"]))

                def wait(self, **kwargs):
                    self.state = "COMMITTED"
                    events.append(("commit", self.name))

            class Run:
                def __enter__(self):
                    return self

                def __exit__(self, *args):
                    events.append(("close",))

                def log_artifact(self, artifact):
                    return artifact

                def use_artifact(self, artifact):
                    events.append(("input", artifact))

            api = SimpleNamespace(default_entity="fixture", artifact_exists=lambda path: False)
            fake = SimpleNamespace(
                Api=lambda **kwargs: api,
                init=lambda **kwargs: Run(),
                Settings=lambda **kwargs: kwargs,
                Artifact=Artifact,
            )
            args = SimpleNamespace(
                root=root,
                max_gb=0.001,
                state_dir=root / "state",
                entity="fixture",
                project="project",
                name="catalog",
                input_artifact="fixture/project/inputs:v1",
            )
            with (
                patch.dict(sys.modules, {"wandb": fake}),
                patch.object(storage, "credentials"),
                patch.object(storage, "private_project"),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                receipt = storage.upload(args, [item], "results")
            self.assertEqual(receipt["status"], "committed")
            self.assertEqual(receipt["files"][0]["sha256"], item["sha256"])
            self.assertEqual(events[-2:], [("commit", "catalog:v1"), ("close",)])
            self.assertEqual(
                json.loads((root / "state/results-receipt.json").read_text())["status"], "committed"
            )


class PipelineContracts(unittest.TestCase):
    def test_actual_main_full_plan_and_optional_reconciliation_contract(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            boundaries = {
                key: str(root / (key + ".json"))
                for key in ["admin0", "admin1", "eez", "conservation_framework", "municipality"]
            }
            with patch.object(job, "ROOT", root / "inputs"), patch.object(job, "OUT", root / "out"):
                config, stages, env, _ = job.full_plan(
                    ROOT / "config/spatial_semantics_iucn_richness_v3.toml", boundaries=boundaries
                )
            names = [s["name"] for s in stages]
            self.assertNotIn("sample_setup", names)
            self.assertNotIn("source_scan", names)
            self.assertEqual(names[0], "crosswalk")
            pair = next(s["command"] for s in stages if s["name"] == "pairs")
            self.assertIn("ark_pipeline.cli.spatial_pairs", pair)
            self.assertEqual(config["boundaries"], boundaries)
            self.assertEqual(env["GLOBAL_DATA_ROOT"], str(root / "inputs"))
            relations = root / "relations/archives/synthetic"
            relations.mkdir(parents=True)
            (relations / "basin_species.parquet").write_bytes(b"synthetic-not-parquet")

            def audit(*args):
                return {
                    "missing_ids": [7],
                    "missing_distinct_relationships": 2,
                    "unique_missing_ids": 1,
                }

            def writer(paths, missing, destination, **kwargs):
                self.assertEqual(missing, {7})
                self.assertEqual(len(paths), 1)
                return {"omitted_distinct_relationships": 2}

            result = preflight_checks.reconcile_basins(
                root, root / "relations", root / "audit", audit=audit, write_missing=writer
            )
            self.assertEqual(result["coverage_status"], "partial-missing-basin-geometries")
            with self.assertRaisesRegex(ValueError, "reconcile"):
                preflight_checks.reconcile_basins(
                    root,
                    root / "relations",
                    root / "bad-audit",
                    audit=audit,
                    write_missing=lambda *args, **kwargs: {"omitted_distinct_relationships": 1},
                )

    def test_census_identity_and_cost_math(self):
        full = {
            "archives": [
                {"logical_name": "synthetic.zip", "sha256": "a", "wall_seconds": 2},
                {"logical_name": "other.zip", "sha256": "b", "wall_seconds": 4},
            ]
        }
        item = {"logical_name": "synthetic.zip", "sha256": "a"}
        result = cloud_census.calibrate(full, item, 3)
        self.assertEqual(result["cloud_io_calibration"]["projected_cloud_scan_seconds"], 9)
        self.assertNotIn("cloud_io_calibration", full)
        with self.assertRaises(ValueError):
            cloud_census.calibrate(full, {**item, "sha256": "wrong"}, 3)
        work, pairs, sensitivity = cost_model.stratified_work(
            {"observations": [{"size_bin": 0, "kernel_seconds": 2, "output_pairs": 4}]},
            {"size_bin_counts": [3]},
        )
        self.assertEqual((work, pairs, sensitivity), (6, 12, [[6, 12], [6, 12]]))

    def test_restore_requires_full_hash_verification_and_preserves_source(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            original_root = "/synthetic-original"
            data = record(root / "data.bin", relative="data.bin")
            generation = root / "tiles/generations/synthetic"
            source = write_json(
                generation / "serving.json",
                {
                    "pinned_files": {
                        original_root + "/data.bin": {"bytes": data["bytes"], "modified_ns": 0}
                    }
                },
            )
            config_record = record(
                source, source.read_bytes(), relative="tiles/generations/synthetic/serving.json"
            )
            selection = write_json(
                root / "serving-selection.json",
                {"original_root": original_root, "generation": "tiles/generations/synthetic"},
            )
            selection_record = record(
                selection, selection.read_bytes(), relative="serving-selection.json"
            )
            write_json(
                root / "_cloud_storage/inventory.json",
                {"files": [data, config_record, selection_record]},
            )
            before = source.read_bytes()
            target = restore_serving.restore(root)
            self.assertEqual(source.read_bytes(), before)
            self.assertIn(str(root / "data.bin"), json.loads(target.read_text())["pinned_files"])
            self.assertFalse((root / "tiles/current").exists())
            (root / "data.bin").write_bytes(b"corrupt!!")
            with self.assertRaises(ValueError):
                restore_serving.restore(root)

    def test_profiler_failure_restores_hook_and_stops_sampler(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            record(root / "sample.parquet")
            record(root / "profile.toml")
            write_json(root / "boundaries.json", {})
            original = profiler.bp.run_stage
            with (
                patch.object(profiler.bp, "run", side_effect=RuntimeError("synthetic failure")),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                with self.assertRaises(RuntimeError):
                    profiler.main(
                        [
                            "--root",
                            str(root / "inputs"),
                            "--output",
                            str(root / "out"),
                            "--report",
                            str(root / "report"),
                            "--sample",
                            str(root / "sample.parquet"),
                            "--profile",
                            str(root / "profile.toml"),
                            "--boundaries",
                            str(root / "boundaries.json"),
                        ]
                    )
            self.assertIs(profiler.bp.run_stage, original)
            self.assertEqual(
                json.loads((root / "report/profile-report.json").read_text())["status"], "failed"
            )
            summary, _ = analyze_profile.analyze(root / "report")
            self.assertEqual(summary["stages"], [])

    def test_coarse_replay_real_export_hashes_and_read_only_source(self):
        import duckdb

        from ark_pipeline.builders.coarse_cache import METRICS, SYSTEMS
        from scripts import benchmark_coarse_exports

        with tempfile.TemporaryDirectory() as temp, contextlib.redirect_stdout(io.StringIO()):
            root = Path(temp).resolve()
            database = root / "build.duckdb"
            with duckdb.connect(str(database)) as connection:
                for system in SYSTEMS:
                    metrics = ", ".join(f'1::BIGINT AS "{metric}"' for metric in METRICS)
                    connection.execute(
                        f"CREATE TABLE h3_res3_agg_{system} AS SELECT '83754efffffffff' AS h3_index, {metrics}"
                    )
            write_json(root / "config.json", {"boundaries": {}})
            before = storage.digest(database)
            reports = []
            for name in ["first", "second"]:
                output = root / name
                with patch.object(
                    sys,
                    "argv",
                    [
                        "benchmark_coarse_exports",
                        "--benchmark-root",
                        str(root),
                        "--output-root",
                        str(output),
                    ],
                ):
                    benchmark_coarse_exports.main()
                reports.append(json.loads((output / "benchmark.json").read_text()))
            self.assertEqual(reports[0]["artifacts_sha256"], reports[1]["artifacts_sha256"])
            self.assertEqual(reports[0]["cells"], 1)
            self.assertEqual(storage.digest(database), before)
            with (
                patch.object(
                    sys,
                    "argv",
                    [
                        "benchmark_coarse_exports",
                        "--benchmark-root",
                        str(root),
                        "--output-root",
                        str(root / "first"),
                    ],
                ),
                contextlib.redirect_stderr(io.StringIO()),
            ):
                with self.assertRaises(SystemExit):
                    benchmark_coarse_exports.main()


class CompletionContracts(unittest.TestCase):
    def test_selected_profile_and_available_metadata_mode_are_preserved(self):
        profile = ROOT / "config/spatial_semantics_iucn_richness_v3.toml"
        metadata = {
            "name": "metadata",
            "command": ["python", "--iucn-metadata-mode", "cache-only", "--profile", str(profile)],
        }
        with patch.object(job.bp, "plan", return_value=({}, [metadata], {})):
            config, stages, environment, _ = job.full_plan(profile)
        command = stages[0]["command"]
        self.assertEqual(command[command.index("--iucn-metadata-mode") + 1], "auto")
        self.assertEqual(command[command.index("--profile") + 1], str(profile))
        self.assertEqual(environment["SPATIAL_PROFILE"], str(profile.resolve()))
        self.assertEqual(config["iucn_reconciliation"], "automatic")

    def test_unexpected_process_wait_error_still_terminates_owned_group(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            (root / "logs").mkdir()
            state = {"running": True, "waits": 0}

            def wait(**kwargs):
                state["waits"] += 1
                if state["waits"] == 1:
                    raise RuntimeError("invented polling error")
                state["running"] = False

            child = SimpleNamespace(
                pid=12345, poll=lambda: None if state["running"] else 0, wait=wait
            )
            with (
                patch.object(job, "OUT", root),
                patch.object(job.subprocess, "Popen", return_value=child),
                patch.object(job.os, "killpg") as kill,
            ):
                with self.assertRaisesRegex(RuntimeError, "polling error"):
                    job.run_command("fixture", ["true"], {}, {"stages": []}, float("inf"))
            self.assertEqual(kill.call_args.args[0], child.pid)
            self.assertFalse(state["running"])

    def test_stage_validation_happens_before_factory(self):
        with tempfile.TemporaryDirectory() as temp:
            for stages in (
                None,
                [None],
                [{"name": "extract", "command": ["true"], "timeout": 1}],
                [{"name": "stage", "command": "true", "timeout": 1}],
                [{"name": "stage", "command": ["true"], "timeout": True}],
            ):
                with self.subTest(stages=stages), self.assertRaises(ValueError):
                    launch.run_remote(
                        b"",
                        {},
                        stages,
                        Path(temp) / "new",
                        {},
                        sandbox_factory=lambda **kw: self.fail("allocated"),
                    )
            self.assertFalse((Path(temp) / "new").exists())

    def test_authorized_receipt_requires_exact_manifest_and_permission(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            item = record(root / "receipts/input.json", b'{"synthetic": true}')
            entry = {
                "root": str(root),
                "path": "receipts/input.json",
                "destination": "receipts/input.json",
                "role": "authorized-input",
                "sha256": item["sha256"],
                "provenance": "invented",
                "transfer_authorization": "fixture only",
            }
            manifest = write_json(root / "manifest.json", {"files": [entry]})
            with self.assertRaises(ValueError):
                payload.build_payload(manifest)
            bundle, _ = payload.build_payload(manifest, allow_authorized_data=True)
            with tarfile.open(fileobj=io.BytesIO(bundle)) as archive:
                self.assertEqual(archive.getnames(), ["receipts/input.json"])

    def test_full_worker_retains_coverage_and_serving_selection_before_persistence(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            boundaries = {
                name: str(record_path)
                for name in ("admin0", "admin1", "eez", "conservation_framework", "municipality")
                for record_path in [root / (name + ".json")]
            }
            for path in boundaries.values():
                Path(path).write_text("{}")
            out = root / "output"
            persisted = []

            def command(name, argv, environment, report, deadline):
                report["stages"].append({"name": name, "exit_code": 0})
                if name == "pairs":
                    p = out / "spatial/hydrobasins"
                    record(p / "missing_relationships.parquet")
                    write_json(
                        p / "receipt.json", {"totals": {"omitted_distinct_relationships": 2}}
                    )
                if name == "coarse_cache":
                    write_json(out / "coarse-tiles/map-metadata.json", {})
                if name == "tiles":
                    write_json(out / "tiles/generations/fixture/serving.json", {})
                    (out / "tiles/current").symlink_to(
                        "generations/fixture", target_is_directory=True
                    )

            def persist(argv):
                persisted.extend(argv)
                state = Path(argv[argv.index("--state-dir") + 1])
                write_json(
                    state / "results-receipt.json",
                    {"catalog": "fixture/project/result:v1", "status": "committed"},
                )

            with (
                patch.object(job, "run_command", command),
                patch.object(job, "storage_main", persist),
                patch.object(job, "ROOT", job.ROOT),
                patch.object(job, "OUT", job.OUT),
            ):
                self.assertEqual(
                    job.main(
                        [
                            "--mode",
                            "full",
                            "--root",
                            str(root / "inputs"),
                            "--output",
                            str(out),
                            "--profile",
                            str(ROOT / "config/spatial_semantics_iucn_richness_v3.toml"),
                            "--boundaries",
                            str(write_json(root / "boundaries.json", boundaries)),
                            "--artifact",
                            "fixture/project/input:v1",
                            "--result-name",
                            "result",
                            "--persist",
                        ]
                    ),
                    0,
                )
            self.assertNotIn(".", persisted)
            self.assertIn("tiles/generations", persisted)
            self.assertNotIn("tiles/current", persisted)
            self.assertEqual(
                json.loads((out / "serving-selection.json").read_text())["generation"],
                "tiles/generations/fixture",
            )
            self.assertEqual(
                json.loads((out / "coarse-tiles/map-metadata.json").read_text())["coverage_audit"][
                    "omitted_distinct_relationships"
                ],
                2,
            )
            self.assertEqual(
                json.loads((out / "run-report.json").read_text())["persistence_status"], "committed"
            )

    def test_profiling_calibration_stage_and_extra_command_are_measured(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            sample = root / "sample.parquet"
            record(sample)
            census = write_json(root / "census.json", {})
            extra = write_json(
                root / "extra.json",
                [
                    {
                        "name": "tiny-stress",
                        "command": [sys.executable, "-c", "print('synthetic')"],
                        "timeout": 5,
                    }
                ],
            )
            original_plan = profiler.bp.plan

            def run(arguments):
                _, stages, _ = profiler.bp.plan(arguments)
                scan = next(stage for stage in stages if stage["name"] == "source_scan")
                self.assertIn(str(census), scan["command"])
                self.assertIn("--probe-root", scan["command"])
                return {"status": "passed"}

            with (
                patch.object(profiler.bp, "run", run),
                patch.object(profiler, "ROOT", profiler.ROOT),
                patch.object(profiler, "OUT", profiler.OUT),
                patch.object(profiler, "REPORT", profiler.REPORT),
            ):
                self.assertEqual(
                    profiler.main(
                        [
                            "--root",
                            str(root / "inputs"),
                            "--output",
                            str(root / "out"),
                            "--report",
                            str(root / "report"),
                            "--sample",
                            str(sample),
                            "--profile",
                            str(ROOT / "config/spatial_semantics_iucn_richness_v3.toml"),
                            "--boundaries",
                            str(write_json(root / "boundaries.json", {})),
                            "--census",
                            str(census),
                            "--archive",
                            "invented.zip",
                            "--probe-root",
                            str(root / "probe"),
                            "--extra-stages",
                            str(extra),
                        ]
                    ),
                    0,
                )
            self.assertIs(profiler.bp.plan, original_plan)
            report = json.loads((root / "report/profile-report.json").read_text())
            self.assertEqual(report["measurements"][0]["stage"], "tiny-stress")
            self.assertEqual(report["status"], "passed")

    def test_restoration_publication_uses_verified_selection_and_validator(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            data = record(root / "data.bin", relative="data.bin")
            source = write_json(
                root / "tiles/generations/fixture/serving.json",
                {
                    "pinned_files": {
                        "/invented/data.bin": {"bytes": data["bytes"], "modified_ns": 0}
                    }
                },
            )
            config = record(
                source, source.read_bytes(), relative="tiles/generations/fixture/serving.json"
            )
            selection = write_json(
                root / "serving-selection.json",
                {"original_root": "/invented", "generation": "tiles/generations/fixture"},
            )
            inventory = write_json(
                root / "_cloud_storage/inventory.json", {"files": [data, config]}
            )
            with self.assertRaisesRegex(ValueError, "selection"):
                restore_serving.restore(root)
            chosen = record(selection, selection.read_bytes(), relative="serving-selection.json")
            write_json(inventory, {"files": [data, config, chosen]})
            validated = []
            target = restore_serving.restore(
                root, publish_current=True, validator=lambda path: validated.append(path)
            )
            self.assertEqual(validated, [target])
            self.assertEqual((root / "tiles/current").resolve(), source.parent)

    def test_comparison_executes_actual_sql_and_uses_verified_exports(self):
        import duckdb

        from scripts import compare_sandbox_run

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            baseline, candidate = root / "old", root / "new"
            sql = {
                "species.parquet": "SELECT 1::BIGINT gbif_accepted_id, 'Invented species' species_name, 'Invented family' AS \"family\", 'Least Concern' redlist_category, NULL::VARCHAR edge_group_name, true has_dna_species_level, true genus_has_dna, true family_has_dna, false goat_data_deficient",
                "species_systems.parquet": "SELECT 1::BIGINT gbif_accepted_id, 'terrestrial' AS \"system\"",
                "cell_species_lists.parquet": "SELECT '83754efffffffff' h3_index, 3 resolution, [1]::BIGINT[] species_ids",
                "species_cells_res3.parquet": "SELECT 1::BIGINT gbif_accepted_id, 3 resolution, ['83754efffffffff'] h3_indexes",
            }
            records = []
            with duckdb.connect() as connection:
                for base in (baseline, candidate / "exports"):
                    base.mkdir(parents=True)
                    for filename, query in sql.items():
                        path = base / filename
                        connection.execute(
                            "COPY ("
                            + query
                            + ") TO '"
                            + str(path).replace("'", "''")
                            + "' (FORMAT PARQUET)"
                        )
                        if base != baseline:
                            records.append(
                                record(path, path.read_bytes(), relative="exports/" + filename)
                            )
            write_json(candidate / "_cloud_storage/inventory.json", {"files": records})
            with patch.object(
                sys,
                "argv",
                [
                    "compare",
                    "--old",
                    str(baseline),
                    "--new",
                    str(candidate),
                    "--output",
                    str(root / "analysis"),
                ],
            ):
                self.assertEqual(compare_sandbox_run.main(), 0)
            report = json.loads((root / "analysis/comparison.json").read_text())
            self.assertEqual(report["membership"], {"shared": 1, "added": 0, "removed": 0})
            self.assertEqual(report["coarse_membership"]["equal_richness_cells"], 1)
            self.assertEqual(len(report["sources"]["baseline_sha256"]), 4)


if __name__ == "__main__":
    unittest.main()
