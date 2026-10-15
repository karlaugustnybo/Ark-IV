"""Instrument an explicit benchmark, optional census calibration and stress commands."""

from __future__ import annotations

import argparse
import contextlib
import json
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import psutil
from launch import owned_process

from ark_pipeline.cli import benchmark_pipeline as bp
from ark_pipeline.runtime.provenance import atomic_json, sha256

ROOT = Path("/scratch/ark-data")
OUT = Path("/scratch/benchmark")
REPORT = Path("/scratch/profile-report")
CURRENT = "setup"
CG = Path("/sys/fs/cgroup")


def read(name):
    try:
        return (CG / name).read_text().strip()
    except OSError:
        return None


def counters(name):
    value = read(name)
    if not value:
        return {}
    result = {}
    for line in value.splitlines():
        parts = line.split()
        if len(parts) == 2:
            with contextlib.suppress(ValueError):
                result[parts[0]] = int(parts[1])
    return result


def allocated(path):
    if not path.exists():
        return 0
    result = subprocess.run(
        ["du", "-s", "--block-size=1", str(path)], capture_output=True, text=True
    )
    return int(result.stdout.split()[0]) if result.returncode == 0 else None


def sample():
    stat = counters("memory.stat")
    processes = []
    for child in [psutil.Process(), *psutil.Process().children(recursive=True)]:
        with contextlib.suppress(psutil.Error, OSError):
            mem = child.memory_full_info()
            processes.append(
                {
                    "pid": child.pid,
                    "rss": mem.rss,
                    "pss": getattr(mem, "pss", None),
                    "uss": getattr(mem, "uss", None),
                    "cpu_seconds": sum(child.cpu_times()[:2]),
                    "threads": child.num_threads(),
                }
            )
    return {
        "seconds": time.monotonic(),
        "stage": CURRENT,
        "memory_current": int(read("memory.current"))
        if read("memory.current") is not None
        else None,
        "memory_peak": int(read("memory.peak")) if read("memory.peak") is not None else None,
        "memory_stat": stat,
        "memory_events": counters("memory.events"),
        "cpu_stat": counters("cpu.stat"),
        "io_stat": read("io.stat"),
        "cpu_pressure": read("cpu.pressure"),
        "memory_pressure": read("memory.pressure"),
        "io_pressure": read("io.pressure"),
        "processes": processes,
    }


def main(argv=None):
    global CURRENT, ROOT, OUT, REPORT
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--sample", type=Path, required=True)
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--boundaries", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--census", type=Path)
    parser.add_argument("--archive")
    parser.add_argument("--probe-root", type=Path)
    parser.add_argument(
        "--extra-stages",
        type=Path,
        help="Optional JSON list of name/command/timeout stages, measured after the benchmark",
    )
    args = parser.parse_args(argv)
    ROOT, OUT, REPORT = args.root.resolve(), args.output.resolve(), args.report.resolve()
    if OUT.exists() or REPORT.exists() or args.workers <= 0:
        parser.error("Use fresh output/report roots and positive workers")
    if any(
        a.is_relative_to(b) or b.is_relative_to(a)
        for a, b in ((OUT, REPORT), (OUT, ROOT), (REPORT, ROOT))
    ):
        parser.error("Input, output and diagnostics must be separate")
    if any((args.census, args.archive, args.probe_root)) and not all(
        (args.census, args.archive, args.probe_root)
    ):
        parser.error("Census calibration requires --census, --archive and --probe-root together")
    extra = json.loads(args.extra_stages.read_text()) if args.extra_stages else []
    from launch import validate_stages

    validate_stages(extra)
    REPORT.mkdir(parents=True)
    report = {
        "status": "running",
        "measurements": [],
        "fixture_sha256": sha256(args.sample),
        "profile_sha256": sha256(args.profile),
        "memory_max": read("memory.max"),
        "cpu_max": read("cpu.max"),
        "scope": "Explicit fixture benchmark; no global throughput claim",
    }
    stop = threading.Event()

    def monitor():
        with (REPORT / "resource-samples.jsonl").open("w") as log:
            while not stop.is_set():
                entry = sample()
                entry["benchmark_spill_allocated_bytes"] = allocated(OUT / "scratch")
                entry["output_allocated_bytes"] = allocated(OUT)
                entry["filesystem_free_bytes"] = shutil.disk_usage(REPORT).free
                log.write(json.dumps(entry) + "\n")
                log.flush()
                atomic_json(REPORT / "live.json", entry)
                stop.wait(1)

    thread = threading.Thread(target=monitor, daemon=True)
    thread.start()
    original_plan, original_stage = bp.plan, bp.run_stage

    def instrument(stage, output, environment, dashboard=None, *, execute=None):
        global CURRENT
        CURRENT = stage["name"]
        before, started = sample(), time.monotonic()
        try:
            return execute() if execute else original_stage(stage, output, environment, dashboard)
        finally:
            after = sample()
            elapsed = time.monotonic() - started
            cpu_before, cpu_after = (
                before["cpu_stat"].get("usage_usec"),
                after["cpu_stat"].get("usage_usec"),
            )
            cpu = (
                None if cpu_before is None or cpu_after is None else (cpu_after - cpu_before) / 1e6
            )
            report["measurements"].append(
                {
                    "stage": CURRENT,
                    "seconds": elapsed,
                    "before": before,
                    "after": after,
                    "average_used_cpu_cores": None if cpu is None else cpu / elapsed,
                }
            )
            atomic_json(REPORT / "profile-report.json", report)

    def plan(arguments):
        config, stages, environment = original_plan(arguments)
        if args.census:
            for stage in stages:
                if stage["name"] == "source_scan":
                    stage["command"] = [
                        sys.executable,
                        str(Path(__file__).with_name("cloud_census.py")),
                        "--config",
                        str(OUT / "config.json"),
                        "--census",
                        str(args.census.resolve()),
                        "--archive",
                        args.archive,
                        "--probe-root",
                        str(args.probe_root.resolve()),
                    ]
                    stage["measurement_scope"] = (
                        "SHA-matched single-archive I/O calibration; projected full census"
                    )
        return config, stages, environment

    bp.plan, bp.run_stage = plan, instrument
    try:
        boundaries = json.loads(args.boundaries.read_text())
        command = [
            "--root",
            str(ROOT),
            "--output-root",
            str(OUT),
            "--sample",
            str(args.sample),
            "--profile",
            str(args.profile),
            "--workers",
            str(args.workers),
            "--fresh",
            "--ui",
            "plain",
        ]
        for key, value in boundaries.items():
            command += ["--" + key.replace("_", "-"), value]
        result = bp.run(bp.parser().parse_args(command))
        report["benchmark_status"] = result["status"]
        if result["status"] != "passed":
            raise RuntimeError("Production benchmark failed")
        for stage in extra:

            def execute(stage=stage):
                with (REPORT / (stage["name"] + ".log")).open("w") as log:
                    with owned_process(
                        stage["command"], stdout=log, stderr=subprocess.STDOUT
                    ) as child:
                        child.wait(timeout=stage["timeout"])
                        if child.returncode:
                            raise subprocess.CalledProcessError(child.returncode, stage["command"])

            instrument(stage, OUT, {}, execute=execute)
        report["status"] = "passed"
        return 0
    except BaseException as error:
        report.update(status="failed", error_type=type(error).__name__)
        raise
    finally:
        bp.plan, bp.run_stage = original_plan, original_stage
        stop.set()
        thread.join()
        for name in (
            "benchmark-report.json",
            "config.json",
            "population.json",
            "pairs-report.json",
            "lists-report.json",
            "polygon-timings.jsonl",
        ):
            if (OUT / name).is_file():
                shutil.copy2(OUT / name, REPORT / name)
        report.update(
            memory_events=counters("memory.events"), memory_peak_bytes=read("memory.peak")
        )
        atomic_json(REPORT / "profile-report.json", report)


if __name__ == "__main__":
    raise SystemExit(main())
