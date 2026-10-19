"""Atomic dashboard checkpoints, keyed by source and invocation identity."""

from __future__ import annotations

import json
import platform
import shutil
import subprocess
from pathlib import Path

import psutil

from ark_pipeline.runtime.provenance import code_fingerprint, sha256


def pipeline_code(repository: Path):
    return code_fingerprint([
        *sorted((repository / "ark_pipeline").rglob("*.py")),
        repository / "justfile",
        *sorted((repository / "config").glob("*.json")),
        *sorted((repository / "config").glob("*.toml")),
        repository / "pyproject.toml",
        repository / "uv.lock",
    ])


def benchmark_environment(runtime: dict):
    """Stable measurement compatibility, separate from informational Git state.

    Legacy benchmark identities without this versioned field are deliberately
    not migrated: their original environment cannot be established reliably.
    Schema-1 dashboard state remains readable for an exactly matching identity.
    """
    processor = platform.processor()
    if platform.system() == "Darwin":
        try:
            processor = subprocess.run(["sysctl", "-n", "machdep.cpu.brand_string"],
                check=True, capture_output=True, text=True, timeout=5).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            pass
    elif Path("/proc/cpuinfo").is_file():
        processor = next((line.split(":", 1)[1].strip() for line in
                          Path("/proc/cpuinfo").read_text().splitlines()
                          if line.startswith("model name")), processor)
    compiler = shutil.which("tippecanoe")
    tool = {"path": compiler, "sha256": sha256(Path(compiler)) if compiler and Path(compiler).is_file() else None}
    return {"schema_version": 1, "runtime": {key: value for key, value in runtime.items() if key != "git"},
            "hardware": {"processor": processor, "physical_cpus": psutil.cpu_count(logical=False),
                         "memory_bytes": psutil.virtual_memory().total}, "compiler": tool}


def source_identity(root: Path):
    path = root / "acquisition/current.json"
    return sha256(path) if path.is_file() else None


def read_checkpoint(directory: Path, identity: dict):
    try:
        data = json.loads((directory / "dashboard-state.json").read_text())
        if data["schema_version"] == 1 and data["identity"] == identity and data["status"] != "passed":
            return data
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None


def find_checkpoint(parent: Path, identity: dict):
    for path in sorted(parent.glob("*/dashboard-state.json"), reverse=True):
        if read_checkpoint(path.parent, identity):
            return path.parent
    return None


def artifact_inventory(output: Path):
    """Cheap change detection for validated benchmark outputs between stages.

    Downstream production readers still perform their normal receipt/checksum
    validation; this inventory never grants permission to publish an output.
    """
    excluded = {"logs", "scratch", "progress.jsonl", "dashboard-state.json", "dashboard-state.json.tmp", ".run.lock",
                "benchmark-report.json", "benchmark-report.md", "benchmark-report.json.tmp", "polygon-timings.jsonl"}
    return {str(path.relative_to(output)): [path.stat().st_size, path.stat().st_mtime_ns]
            for path in output.rglob("*") if path.is_file() and not path.is_symlink()
            and not any(part in excluded for part in path.relative_to(output).parts)}


def validate_inventory(output: Path, inventory: dict):
    for relative, expected in inventory.items():
        path = output / relative
        if not path.resolve().is_relative_to(output.resolve()) or not path.is_file() or [path.stat().st_size, path.stat().st_mtime_ns] != expected:
            raise ValueError(f"Saved benchmark output changed or is missing: {relative}; start a fresh benchmark with --fresh")
