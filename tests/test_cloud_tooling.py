"""Offline tooling contracts: only temporary synthetic bytes and recording clients."""

from __future__ import annotations

import contextlib
import hashlib
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

import psutil

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
    def test_full_job_records_plain_progress_and_resources(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'logs').mkdir()
            report = {'stages': []}
            command = [sys.executable, '-c',
                       "from ark_pipeline.runtime.progress import emit; emit('phase', phase='fixture'); import time; time.sleep(.1)"]
            with patch.object(job, 'OUT', root):
                job.run_command('fixture', command, dict(os.environ), report, time.monotonic() + 5)
            output = root / 'runs/fixture'
            self.assertTrue((output / 'resources.jsonl').is_file())
            self.assertIn('fixture', (output / 'progress.jsonl').read_text())
            self.assertEqual(json.loads((output / 'dashboard-state.json').read_text())['status'], 'passed')
            self.assertEqual(report['stages'][0]['exit_code'], 0)

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

    def test_lookup_pins_survive_manifest_and_companion_entrypoints(self):
        for companion in (False, True):
            with self.subTest(companion=companion), tempfile.TemporaryDirectory() as temp:
                root = Path(temp).resolve()
                lookups = [record(root / name, relative=name) for name in storage.EXTRA_INPUTS]
                manifest = write_json(root / "acquisition/current.json", {"sources": {}})
                with self.assertRaisesRegex(ValueError, "explicit size/SHA256"):
                    preflight_checks.check(root)
                if companion:
                    write_json(root / storage.LOOKUP_PINS, {"files": lookups})
                else:
                    write_json(manifest, {"sources": {"lookups": {"files": lookups}}})
                before = manifest.read_bytes()
                planned = {item["path"]: item for item in storage.input_plan(root)}
                for item in lookups:
                    self.assertEqual(planned[item["path"]]["expected_sha256"], item["sha256"])
                self.assertEqual(preflight_checks.check(root)["files"], 4 if companion else 3)
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(
                        storage.main(["upload-inputs", "--root", str(root), "--dry-run"]), 0
                    )
                    output = root / "preflight"
                    self.assertEqual(
                        preflight_checks.main(["--root", str(root), "--output", str(output)]), 0
                    )
                self.assertEqual(manifest.read_bytes(), before)
                (root / lookups[0]["path"]).write_bytes(b"tampered!")
                with self.assertRaisesRegex(ValueError, "checksum"):
                    preflight_checks.check(root)
                with (
                    patch.dict(sys.modules, {"wandb": SimpleNamespace()}),
                    patch.object(storage, "credentials") as credentials,
                ):
                    with self.assertRaisesRegex(ValueError, "checksum"):
                        storage.upload(
                            SimpleNamespace(root=root, max_gb=1), list(planned.values()), "inputs"
                        )
                    credentials.assert_not_called()
                if companion:
                    write_json(
                        root / storage.LOOKUP_PINS, {"files": [{**lookups[0], "sha256": "bad"}]}
                    )
                    with self.assertRaisesRegex(ValueError, "explicit size/SHA256"):
                        storage.input_plan(root)

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
    def test_full_job_propagates_explicit_resources_to_actual_stage_commands(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with patch.object(job, "ROOT", root / "inputs"), patch.object(job, "OUT", root / "out"):
                config, stages, environment, _ = job.full_plan(
                    ROOT / "config/spatial_semantics_iucn_richness_v3_fast.toml",
                    resource_options={"workers": 16, "metric_workers": 8,
                                      "duckdb_threads": 12, "metric_threads": 1},
                    memory_limit="4GB", iucn_details_dir=root / 'retained-cache',
                    iucn_metadata_mode='cache-only')
            lists = next(s['command'] for s in stages if s['name'] == 'lists')
            metrics = next(s['command'] for s in stages if s['name'] == 'fine_metrics')
            self.assertEqual(lists[lists.index('--threads')+1], '12')
            self.assertEqual(lists[lists.index('--memory-limit')+1], '4GB')
            self.assertEqual(metrics[metrics.index('--memory-limit')+1], '4GB')
            self.assertEqual(environment['SPATIAL_WORKERS'], '16')
            self.assertEqual(environment['RES7_WORKERS'], '8')
            self.assertEqual(environment['RES7_THREADS'], '1')
            self.assertEqual(environment['TIPPECANOE_MAX_THREADS'], '16')
            self.assertEqual(config['resources']['duckdb_threads'], 12)
            metadata = next(s['command'] for s in stages if s['name'] == 'metadata')
            self.assertEqual(metadata[metadata.index('--iucn-metadata-mode')+1], 'cache-only')
            self.assertEqual(metadata[metadata.index('--iucn-details-dir')+1], str(root / 'retained-cache'))

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

    def test_census_cli_binds_semantics_and_archive_observations(self):
        import copy

        profile = ROOT / "config/spatial_semantics_iucn_richness_v3.toml"
        policy = cloud_census.row_policy(cloud_census.load_spatial_profile(profile))
        bands = list(cloud_census.SIZE_BREAKS)
        cases = [
            None,
            "row_policy",
            "size_breaks",
            "census_method",
            "missing_method",
            "missing_policy",
            "missing_bands",
            "scanned",
            "excluded",
            "release",
            "bytes",
            "size_bin_counts",
            "missing_observation",
        ]
        for bad in cases:
            with self.subTest(bad=bad), tempfile.TemporaryDirectory() as temp:
                root = Path(temp).resolve()
                item = record(root / "source/synthetic.zip", relative="synthetic.zip")
                item.update(logical_name="synthetic.zip", release="invented")
                write_json(
                    root / "source/acquisition/current.json",
                    {"sources": {"iucn-spatial": {"files": [item]}}},
                )
                archive = {
                    **item,
                    "wall_seconds": 1,
                    "scanned": 1,
                    "excluded": {},
                    "size_bin_counts": [1] + [0] * len(bands),
                }
                measured = {
                    "row_policy": policy,
                    "size_breaks": bands,
                    "census_method": "native-ring-envelopes-v1",
                    "archives": [archive],
                    "eligible_polygons": 1,
                    "size_bin_counts": archive["size_bin_counts"],
                }
                full = copy.deepcopy(measured)
                if bad in {"row_policy", "size_breaks", "census_method"}:
                    full[bad] = "incompatible"
                elif bad == "missing_method":
                    del full["census_method"]
                elif bad == "missing_policy":
                    del full["row_policy"]
                elif bad == "missing_bands":
                    del full["size_breaks"]
                elif bad == "missing_observation":
                    del full["archives"][0]["scanned"]
                elif bad is not None:
                    full["archives"][0][bad] = "incompatible"
                census = write_json(root / "census.json", full)
                config = write_json(
                    root / "config.json",
                    {
                        "root": str(root / "source"),
                        "output": str(root / "output"),
                        "profile": str(profile),
                    },
                )
                before = census.read_bytes()

                def scan(config):
                    write_json(Path(config["output"]) / "population.json", measured)

                argv = [
                    "--config",
                    str(config),
                    "--census",
                    str(census),
                    "--archive",
                    "synthetic.zip",
                    "--probe-root",
                    str(root / "probe"),
                ]
                with patch.object(cloud_census, "source_scan", side_effect=scan):
                    if bad is None:
                        self.assertEqual(cloud_census.main(argv), 0)
                        saved = json.loads((root / "output/population.json").read_text())
                        self.assertEqual(saved["archives"], full["archives"])
                        self.assertGreater(saved["cloud_io_calibration"]["cloud_seconds"], 0)
                    else:
                        with self.assertRaises(ValueError):
                            cloud_census.main(argv)
                        self.assertFalse((root / "output/population.json").exists())
                self.assertEqual(census.read_bytes(), before)

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
                # This contract exercises orchestration, with no real sample.
                arguments.dry_run = True
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
            before = source.read_bytes()

            def validate(path):
                value = json.loads(path.read_text())
                for filename, pin in value["pinned_files"].items():
                    stat = Path(filename).stat()
                    self.assertEqual(pin, {"bytes": stat.st_size, "modified_ns": stat.st_mtime_ns})

            target = restore_serving.restore(root, publish_current=True, validator=validate)
            validate(root / "tiles/current/serving.json")
            self.assertEqual((root / "tiles/current/serving.json").resolve(), target)
            self.assertEqual(source.read_bytes(), before)
            with self.assertRaisesRegex(ValueError, "overwrite"):
                restore_serving.restore(root, publish_current=True, validator=validate)

    def test_restoration_refuses_validation_failure_and_existing_current(self):
        for existing in (False, True):
            with self.subTest(existing=existing), tempfile.TemporaryDirectory() as temp:
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
                chosen = record(
                    selection, selection.read_bytes(), relative="serving-selection.json"
                )
                write_json(
                    root / "_cloud_storage/inventory.json", {"files": [data, config, chosen]}
                )
                before = source.read_bytes()
                current = root / "tiles/current"
                if existing:
                    current.symlink_to("missing-selection", target_is_directory=True)

                def reject(path):
                    raise ValueError("Invented invalid serving configuration")

                with self.assertRaises(ValueError):
                    restore_serving.restore(root, publish_current=True, validator=reject)
                if existing:
                    self.assertEqual(current.readlink(), Path("missing-selection"))
                else:
                    self.assertFalse(current.exists() or current.is_symlink())
                self.assertEqual(source.read_bytes(), before)

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


class OwnedProcessTests(unittest.TestCase):
    def command(self, root, outcome):
        # The leader exits first; its worker ignores TERM in the same owned group.
        worker = (
            "import pathlib,signal,sys,time;"
            "signal.signal(signal.SIGTERM,signal.SIG_IGN);"
            "pathlib.Path(sys.argv[1]).write_text('ready');time.sleep(60)"
        )
        leader = (
            "import pathlib,subprocess,sys,time,os;"
            f"p=subprocess.Popen([sys.executable,'-c',{worker!r},{str(root / 'ready')!r}]);"
            f"ready=pathlib.Path({str(root / 'ready')!r});"
            "exec('while not ready.exists(): time.sleep(0.005)');"
            f"pathlib.Path({str(root / 'pids')!r}).write_text(str(os.getpid())+' '+str(p.pid));"
            + (f"sys.exit({outcome})" if outcome is not None else "time.sleep(60)")
        )
        return [sys.executable, "-c", leader]

    @contextlib.contextmanager
    def fixture(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            with patch.object(launch, "PROCESS_TERM_GRACE_SECONDS", 0.1):
                try:
                    yield root
                finally:
                    # Failures must not leak even these exact synthetic processes.
                    if (root / "pids").exists():
                        for pid in map(int, (root / "pids").read_text().split()):
                            try:
                                process = psutil.Process(pid)
                                if (
                                    process.is_running()
                                    and process.status() != psutil.STATUS_ZOMBIE
                                ):
                                    process.kill()
                            except psutil.NoSuchProcess:
                                pass

    def assert_stopped(self, root):
        pids = list(map(int, (root / "pids").read_text().split()))
        for pid in pids:
            deadline = time.monotonic() + 2
            while psutil.pid_exists(pid):
                try:
                    process = psutil.Process(pid)
                    if process.status() == psutil.STATUS_ZOMBIE:
                        break  # In containers the orphan's reaping belongs to PID 1.
                except psutil.NoSuchProcess:
                    break
                self.assertLess(time.monotonic(), deadline, f"Owned worker {pid} survived")
                time.sleep(0.01)
        self.assertFalse(psutil.pid_exists(pids[0]), "Direct leader was not reaped")

    def test_full_worker_finishes_group_on_every_exit_path(self):
        real_owned = launch.owned_process

        @contextlib.contextmanager
        def short_observation(command, **kwargs):
            with real_owned(command, **kwargs) as child:
                wait = child.wait

                def short_wait(timeout=None):
                    if timeout == 1 and mode == "poll_error":
                        end = time.monotonic() + 2
                        while not (root / "pids").exists():
                            self.assertLess(time.monotonic(), end)
                            time.sleep(0.01)
                        raise RuntimeError("invented polling error")
                    return wait(timeout=0.2 if timeout == 1 else timeout)

                child.wait = short_wait
                yield child

        for mode in ("success", "failure", "memory", "deadline", "disk", "exception", "poll_error"):
            with self.subTest(mode=mode), self.fixture() as root:
                (root / "logs").mkdir()
                outcome = 0 if mode == "success" else 3 if mode == "failure" else None
                report = {"stages": []}
                disk_error = RuntimeError("invented disk observation failure")

                def disk():
                    if mode == "exception":
                        raise disk_error
                    return 451 * 1024**3 if mode == "disk" else 0

                with (
                    patch.object(job, "OUT", root),
                    patch.object(job, "owned_process", short_observation),
                    patch.object(job, "disk_bytes", disk),
                    patch.object(job, "memory_observation", return_value=mode == "memory"),
                    patch.object(
                        job.shutil, "disk_usage", return_value=SimpleNamespace(free=30 * 1024**3)
                    ),
                ):
                    deadline = time.monotonic() + (0.15 if mode == "deadline" else 30)
                    if mode == "success":
                        job.run_command(
                            mode, self.command(root, outcome), dict(os.environ), report, deadline
                        )
                    else:
                        with self.assertRaises(RuntimeError) as raised:
                            job.run_command(
                                mode,
                                self.command(root, outcome),
                                dict(os.environ),
                                report,
                                deadline,
                            )
                        if mode == "exception":
                            self.assertIs(raised.exception, disk_error)
                        elif mode == "poll_error":
                            self.assertIn("polling error", str(raised.exception))
                        elif mode != "failure":
                            self.assertIn("headroom", str(raised.exception))
                self.assert_stopped(root)
                if mode in {"success", "failure"}:
                    self.assertEqual(report["stages"][0]["exit_code"], outcome)
                else:
                    self.assertEqual(report["stages"], [])

    def test_profiler_extra_stage_group_cleanup_preserves_reports_and_hooks(self):
        for outcome in (None, 0, 3):
            with self.subTest(outcome=outcome), self.fixture() as root:
                sample = write_json(root / "sample.json", {"synthetic": True})
                boundaries = write_json(root / "boundaries.json", {})
                extra = write_json(
                    root / "extra.json",
                    [
                        {
                            "name": "owned",
                            "command": self.command(root, outcome),
                            "timeout": 1,
                        }
                    ],
                )
                original_plan, original_stage = profiler.bp.plan, profiler.bp.run_stage
                threads = []
                thread_factory = profiler.threading.Thread

                def thread(*args, **kwargs):
                    result = thread_factory(*args, **kwargs)
                    threads.append(result)
                    return result

                with (
                    patch.object(profiler.bp, "run", return_value={"status": "passed"}),
                    patch.object(profiler, "sample", return_value={"cpu_stat": {}}),
                    patch.object(profiler, "allocated", return_value=0),
                    patch.object(profiler.threading, "Thread", thread),
                    patch.object(profiler, "ROOT", profiler.ROOT),
                    patch.object(profiler, "OUT", profiler.OUT),
                    patch.object(profiler, "REPORT", profiler.REPORT),
                ):
                    args = [
                        "--root",
                        str(root / "input"),
                        "--output",
                        str(root / "output"),
                        "--report",
                        str(root / "report"),
                        "--sample",
                        str(sample),
                        "--profile",
                        str(ROOT / "config/spatial_semantics_iucn_richness_v3.toml"),
                        "--boundaries",
                        str(boundaries),
                        "--extra-stages",
                        str(extra),
                    ]
                    if outcome == 0:
                        self.assertEqual(profiler.main(args), 0)
                    else:
                        with self.assertRaises(
                            subprocess.TimeoutExpired
                            if outcome is None
                            else subprocess.CalledProcessError
                        ):
                            profiler.main(args)
                self.assert_stopped(root)
                self.assertIs(profiler.bp.plan, original_plan)
                self.assertIs(profiler.bp.run_stage, original_stage)
                self.assertTrue(threads)
                self.assertTrue(all(not t.is_alive() for t in threads))
                report = json.loads((root / "report/profile-report.json").read_text())
                self.assertEqual(report["status"], "passed" if outcome == 0 else "failed")
                self.assertEqual(report["measurements"][0]["stage"], "owned")
                self.assertTrue((root / "report/owned.log").is_file())

    def test_cleanup_failure_preserves_original_interruption(self):
        child = SimpleNamespace()
        for original in (RuntimeError("original"), KeyboardInterrupt()):
            with (
                self.subTest(error=type(original).__name__),
                patch.object(launch.subprocess, "Popen", return_value=child),
                patch.object(launch, "_stop_process_group", side_effect=OSError("cleanup")),
            ):
                with self.assertRaises(type(original)) as raised:
                    with launch.owned_process(["unused"]):
                        raise original
                self.assertIs(raised.exception, original)
                self.assertIn("cleanup failed: OSError", original.__notes__[0])


if __name__ == "__main__":
    unittest.main()
