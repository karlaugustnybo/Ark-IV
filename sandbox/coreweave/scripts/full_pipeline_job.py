"""Cloud job: verified inputs, bounded preflight, or explicitly requested full build."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from cloud_storage import main as storage_main

from ark_pipeline.cli import benchmark_pipeline as bp
from ark_pipeline.runtime.provenance import atomic_json

ROOT = Path("/scratch/ark-data")
OUT = Path("/scratch/ark-output")
PROFILE = (
    Path(__file__).resolve().parents[3 if Path(__file__).resolve().parent.name == "scripts" else 2]
    / "config/spatial_semantics_iucn_richness_v3.toml"
)


def full_plan(profile: Path = PROFILE, *, boundaries=None):
    # Reuse the production benchmark's downstream commands and isolated paths;
    # replace only its fixture-based pair/list stages with production builders.
    boundary_args = []
    sources = boundaries or {}
    for name, source in sources.items():
        target = Path(source)
        boundary_args += ["--" + name.replace("_", "-"), str(target)]
    args = bp.parser().parse_args(
        [
            "--root",
            str(ROOT),
            "--output-root",
            str(OUT),
            "--profile",
            str(profile),
            "--workers",
            "8",
            "--metric-workers",
            os.environ.get("ARK_PIPELINE_METRIC_WORKERS", "6"),
            "--metric-threads",
            "1",
            "--tile-threads",
            "8",
            "--tile-duckdb-threads",
            "1",
            "--memory-limit",
            "2GB",
            *boundary_args,
        ]
    )
    config, stages, env = bp.plan(args)
    env["SPATIAL_PROFILE"] = str(profile.resolve())
    stages = [s for s in stages if s["name"] not in {"sample_setup", "source_scan"}]
    for stage in stages:
        if stage["name"] == "metadata":
            command = stage["command"]
            if "--iucn-metadata-mode" in command:
                command[command.index("--iucn-metadata-mode") + 1] = "auto"
                command += ["--iucn-details-dir", str(OUT / "species/iucn-details")]
                config["iucn_reconciliation"] = "automatic"
            else:
                config["iucn_reconciliation"] = "unavailable-in-selected-code"
        if stage["name"] == "pairs":
            stage["command"] = [
                sys.executable,
                "-m",
                "ark_pipeline.cli.spatial_pairs",
                "build",
                "--root",
                str(ROOT),
                "--profile",
                str(profile),
                "--output-root",
                str(OUT / "spatial"),
            ]
        elif stage["name"] == "lists":
            stage["command"] = [
                sys.executable,
                "-m",
                "ark_pipeline.cli.spatial_aggregate",
                "run",
                "--root",
                str(ROOT),
                "--profile",
                str(profile),
                "--output-root",
                str(OUT / "spatial"),
                "--memory-limit",
                "2GB",
                "--threads",
                "8",
                "--ui",
                "plain",
            ]
    return config, stages, env, sources


def disk_bytes():
    result = subprocess.run(
        ["du", "-s", "--block-size=1", "/scratch"], capture_output=True, text=True
    )
    # Aggregation deletes spill/partition files while this read-only probe walks
    # scratch. GNU du can report a valid total and exit 1 for those vanished files.
    vanished = result.stderr.splitlines()
    if result.returncode and not (
        result.returncode == 1
        and vanished
        and all("No such file or directory" in line for line in vanished)
        and result.stdout.split()
    ):
        raise subprocess.CalledProcessError(
            result.returncode, result.args, output=result.stdout, stderr=result.stderr
        )
    return int(result.stdout.split()[0])


def memory_observation(report):
    """Record cgroup memory and leave margin for persistence before a slow rise."""
    root = Path("/sys/fs/cgroup")
    try:
        stat = dict(line.split() for line in (root / "memory.stat").read_text().splitlines())
        working = sum(int(stat.get(key, 0)) for key in ("anon", "shmem", "kernel"))
        current = int((root / "memory.current").read_text())
        limit = (root / "memory.max").read_text().strip()
        events = dict(line.split() for line in (root / "memory.events").read_text().splitlines())
    except (OSError, ValueError):
        report["memory_observation_available"] = False
        return False
    report["memory_observation_available"] = True
    report["peak_cgroup_memory_bytes"] = max(report.get("peak_cgroup_memory_bytes", 0), current)
    report["peak_working_memory_proxy_bytes"] = max(
        report.get("peak_working_memory_proxy_bytes", 0), working
    )
    report["memory_events"] = {key: int(value) for key, value in events.items()}
    # Cache may fill memory.current safely. The proxy is approximate and the
    # polling interval cannot prevent a sudden allocation from causing OOM.
    return limit != "max" and working > int(limit) * 0.90


def run_command(name, command, environment, report, deadline):
    report["current_stage"] = name
    atomic_json(OUT / "run-report.json", report)
    if time.monotonic() >= deadline:
        raise RuntimeError("Compute deadline exhausted before stage launch")
    start = time.monotonic()
    with (OUT / "logs" / f"{name}.log").open("w") as log:
        child = subprocess.Popen(
            command,
            stdout=log,
            stderr=subprocess.STDOUT,
            cwd=str(Path(__file__).resolve().parents[3]),
            env=environment,
            start_new_session=True,
        )
        try:
            while child.poll() is None:
                try:
                    child.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    try:
                        usage = disk_bytes()
                    except Exception:
                        import signal

                        os.killpg(child.pid, signal.SIGTERM)
                        try:
                            child.wait(timeout=15)
                        except subprocess.TimeoutExpired:
                            os.killpg(child.pid, signal.SIGKILL)
                            child.wait()
                        raise
                    report["peak_allocated_scratch_bytes"] = max(
                        report.get("peak_allocated_scratch_bytes", 0), usage
                    )
                    memory_headroom_exhausted = memory_observation(report)
                    atomic_json(OUT / "run-report.json", report)
                    if (
                        memory_headroom_exhausted
                        or time.monotonic() >= deadline
                        or usage > 450 * 1024**3
                        or shutil.disk_usage("/scratch").free < 20 * 1024**3
                    ):
                        import signal

                        os.killpg(child.pid, signal.SIGTERM)
                        try:
                            child.wait(timeout=15)
                        except subprocess.TimeoutExpired:
                            os.killpg(child.pid, signal.SIGKILL)
                            child.wait()
                        raise RuntimeError(
                            "Job stopped to preserve time/disk/memory headroom for result persistence"
                        )
        finally:
            if child.poll() is None:
                import signal

                os.killpg(child.pid, signal.SIGTERM)
                try:
                    child.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    os.killpg(child.pid, signal.SIGKILL)
                    child.wait()
    entry = {"name": name, "seconds": time.monotonic() - start, "exit_code": child.returncode}
    report["stages"].append(entry)
    atomic_json(OUT / "run-report.json", report)
    if child.returncode:
        raise RuntimeError(f"{name} failed; see cloud result log")


def main(argv=None):
    global ROOT, OUT
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["plan", "preflight", "full"], default="plan")
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument(
        "--boundaries",
        type=Path,
        required=True,
        help="JSON mapping benchmark boundary names to explicit paths",
    )
    parser.add_argument("--artifact", required=True)
    parser.add_argument("--result-name", required=True)
    parser.add_argument("--compute-seconds", type=int, default=1800)
    parser.add_argument(
        "--persist",
        action="store_true",
        help="Explicitly authorize result transfer; requires separate input rights",
    )
    parser.add_argument("--result-max-gb", type=float, default=50)
    parser.add_argument(
        "--relations",
        type=Path,
        help="Explicit preflight relations for compatible spatial integration",
    )
    parser.add_argument("--input-receipt", type=Path)
    args = parser.parse_args(argv)
    import re

    if not re.fullmatch(r"[^:/]+/[^:/]+/[^:/]+:v[0-9]+", args.artifact):
        parser.error("Use an immutable versioned input artifact identity")
    if args.input_receipt:
        receipt = json.loads(args.input_receipt.read_text())
        if receipt.get("status") != "committed" or receipt.get("catalog") != args.artifact:
            parser.error("Committed input receipt does not match selected artifact")
    ROOT, OUT = args.root.resolve(), args.output.resolve()
    if (
        args.compute_seconds <= 0
        or OUT.exists()
        or OUT.is_relative_to(ROOT)
        or ROOT.is_relative_to(OUT)
    ):
        parser.error("Use positive compute budget and fresh, separate input/output roots")
    boundaries = json.loads(args.boundaries.read_text())
    required = {"admin0", "admin1", "eez", "conservation_framework", "municipality"}
    if set(boundaries) != required or not all(Path(p).is_file() for p in boundaries.values()):
        parser.error("All five explicit benchmark boundary files are required")
    config, stages, env, _ = full_plan(args.profile, boundaries=boundaries)
    if args.mode == "plan":
        print(json.dumps({"config": config, "stages": stages, "environment": env}, indent=2))
        return 0
    OUT.mkdir(parents=True)
    (OUT / "logs").mkdir()
    atomic_json(OUT / "config.json", config)
    report = {
        "status": "running",
        "input_artifact": args.artifact,
        "profile_sha256": config["profile_sha256"],
        "stages": [],
        "coverage_scope": "Selected profile only; no claim of complete global coverage or lossless audit",
        "persistence_status": "not-requested",
    }
    environment = {**os.environ, **env}
    deadline = time.monotonic() + args.compute_seconds
    failure = None
    try:
        if args.mode == "preflight":
            run_command(
                "preflight",
                [
                    sys.executable,
                    str(Path(__file__).with_name("preflight_checks.py")),
                    "--root",
                    str(ROOT),
                    "--output",
                    str(OUT / "preflight"),
                    *(["--relations", str(args.relations)] if args.relations else []),
                ],
                environment,
                report,
                deadline,
            )
        else:
            for stage in stages:
                run_command(stage["name"], stage["command"], environment, report, deadline)
                if stage["name"] == "pairs":
                    index = OUT / "spatial/hydrobasins"
                    if (index / "missing_relationships.parquet").is_file():
                        audit = OUT / "coverage-audit"
                        audit.mkdir()
                        shutil.copy2(
                            index / "missing_relationships.parquet",
                            audit / "missing_relationships.parquet",
                        )
                        receipt = json.loads((index / "receipt.json").read_text())
                        atomic_json(audit / "coverage-summary.json", receipt["totals"])
                if (
                    stage["name"] == "coarse_cache"
                    and (OUT / "coverage-audit/coverage-summary.json").is_file()
                ):
                    metadata = OUT / "coarse-tiles/map-metadata.json"
                    contents = json.loads(metadata.read_text())
                    contents["coverage_audit"] = json.loads(
                        (OUT / "coverage-audit/coverage-summary.json").read_text()
                    )
                    contents["coverage_notice"] = (
                        "Relationships without geometry remain in the omission audit; see the selected profile."
                    )
                    atomic_json(metadata, contents)
            current = OUT / "tiles/current"
            if (current / "serving.json").is_file():
                generation = current.resolve()
                if not generation.is_relative_to(OUT):
                    raise ValueError("Serving generation escapes output root")
                atomic_json(
                    OUT / "serving-selection.json",
                    {
                        "original_root": str(OUT),
                        "generation": generation.relative_to(OUT).as_posix(),
                    },
                )
                report["serving_selection"] = "recorded"
            else:
                report["serving_selection"] = "unavailable-in-selected-code"
        report["status"] = "passed"
    except BaseException as error:
        report.update(status="failed", error_type=type(error).__name__)
        failure = error
    finally:
        atomic_json(OUT / "run-report.json", report)
        if args.persist:
            try:
                state = OUT.parent / (OUT.name + "-transfer")
                includes = ["logs", "config.json", "run-report.json"]
                includes += [
                    name
                    for name in (
                        "coverage-audit",
                        "preflight",
                        "serving-selection.json",
                        "tiles/generations",
                        "species",
                        "exports",
                        "coarse-tiles",
                        "static",
                        "metrics",
                        "adm2",
                        "crosswalk",
                        "prepared-inputs.json",
                        "build-validation.json",
                        "source-validation.json",
                        "build.duckdb",
                        "source.duckdb",
                    )
                    if (OUT / name).exists()
                ]
                storage_main(
                    [
                        "upload-results",
                        "--authorize-transfer",
                        "--root",
                        str(OUT),
                        *[v for name in includes for v in ("--include", name)],
                        "--state-dir",
                        str(state),
                        "--name",
                        args.result_name,
                        "--input-artifact",
                        args.artifact,
                        "--max-gb",
                        str(args.result_max_gb),
                    ]
                )
                receipt = json.loads((state / "results-receipt.json").read_text())
                report.update(
                    result_artifact=receipt["catalog"], persistence_status=receipt["status"]
                )
            except BaseException as error:
                report.update(
                    persistence_status="failed", persistence_error_type=type(error).__name__
                )
                failure = failure or error
        atomic_json(OUT / "run-report.json", report)
    if failure:
        raise RuntimeError("Pipeline failed; see private report and persistence status") from None
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
