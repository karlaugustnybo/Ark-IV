"""Calibrate a selected source archive against an explicit hash-matched local census."""

from __future__ import annotations

import argparse
import copy
import json
import math
import time
from pathlib import Path

from ark_pipeline.cli.benchmark_pipeline import source_scan
from ark_pipeline.runtime.provenance import atomic_json, sha256


def calibrate(full, item, elapsed):
    local = next(x for x in full["archives"] if x["logical_name"] == item["logical_name"])
    if local.get("sha256") != item["sha256"]:
        raise ValueError("Local census source identity differs")
    seconds = local["wall_seconds"]
    if not math.isfinite(seconds) or seconds <= 0 or not math.isfinite(elapsed) or elapsed <= 0:
        raise ValueError("Calibration durations must be positive and finite")
    result = copy.deepcopy(full)
    result["cloud_io_calibration"] = {
        "archive": item["logical_name"],
        "source_sha256": item["sha256"],
        "cloud_seconds": elapsed,
        "local_archive_seconds": seconds,
        "local_full_scan_seconds": sum(x["wall_seconds"] for x in full["archives"]),
        "projected_cloud_scan_seconds": elapsed
        * sum(x["wall_seconds"] for x in full["archives"])
        / seconds,
        "scope": "Single-archive ratio projection; full cloud source scan was not executed",
    }
    return result


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--config", type=Path, required=True)
    p.add_argument("--census", type=Path, required=True)
    p.add_argument("--archive", required=True)
    p.add_argument("--probe-root", type=Path, required=True)
    args = p.parse_args(argv)
    config = json.loads(args.config.read_text())
    root, output = Path(config["root"]), Path(config["output"])
    manifest = json.loads((root / "acquisition/current.json").read_text())
    source = manifest["sources"]["iucn-spatial"]
    item = next(f for f in source["files"] if f["logical_name"] == args.archive)
    from cloud_storage import safe_path

    full = json.loads(args.census.read_text())
    expected = {record["logical_name"]: record["sha256"] for record in source["files"]}
    actual = {record["logical_name"]: record["sha256"] for record in full["archives"]}
    if actual != expected or len(actual) != len(full["archives"]):
        raise ValueError("Full census archive identities differ from the acquisition manifest")
    if any(
        not math.isfinite(row["wall_seconds"]) or row["wall_seconds"] <= 0
        for row in full["archives"]
    ):
        raise ValueError("All census durations must be positive and finite")
    path = safe_path(root, item["path"])
    if sha256(path) != item["sha256"]:
        raise ValueError("Calibration input checksum mismatch")
    args.probe_root.mkdir(parents=True, exist_ok=False)
    # Reference the explicit input, retaining its original identity. No receipt edits.
    import os

    os.link(path, args.probe_root / path.name)
    manifest["sources"]["iucn-spatial"] = {**source, "files": [{**item, "path": path.name}]}
    atomic_json(args.probe_root / "acquisition/current.json", manifest)
    calibration_out = output / "io-calibration"
    calibration_out.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    source_scan({**config, "root": str(args.probe_root), "output": str(calibration_out)})
    result = calibrate(full, item, time.monotonic() - started)
    atomic_json(output / "population.json", result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
