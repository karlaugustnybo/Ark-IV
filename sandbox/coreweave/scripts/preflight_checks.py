"""Offline preflight of an explicit acquisition manifest and selected Linux tests."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from cloud_storage import LOOKUP_PINS, digest, input_plan


def check(root):
    plan = input_plan(root)
    for item in plan:
        if not item.get("expected_sha256") and item["path"] not in {
            "acquisition/current.json",
            LOOKUP_PINS,
        }:
            raise ValueError("Preflight requires checksum-pinned acquisition inputs")
        if item.get("expected_sha256") and digest(root / item["path"]) != item["expected_sha256"]:
            raise ValueError("Acquisition checksum mismatch")
    return {
        "status": "passed",
        "files": len(plan),
        "bytes": sum(p["bytes"] for p in plan),
        "scope": "Manifest size/checksum validation only; no geometry, omission or global coverage claim",
    }


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--root", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--test", action="append", default=[])
    p.add_argument(
        "--relations",
        type=Path,
        help="Optional explicit normalized relations; requires compatible spatial integration",
    )
    args = p.parse_args(argv)
    report = check(args.root)
    for name in args.test:
        if Path(name).name != name or not name.startswith("test_") or not name.endswith(".py"):
            p.error("Tests must be explicit unittest filenames")
        subprocess.run(
            [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-p", name], check=True
        )
    report["tests"] = args.test
    args.output.mkdir(parents=True, exist_ok=False)
    if args.relations is not None:
        report["basin_audit"] = reconcile_basins(
            args.root, args.relations, args.output / "coverage-audit"
        )
    (args.output / "preflight-report.json").write_text(json.dumps(report, indent=2) + "\n")
    return 0


def reconcile_basins(root, relations, output, *, audit=None, write_missing=None):
    """Optional integrated spatial contract; no receipt rewriting or ID substitution.

    The source-checker implementation belongs to the independent spatial PR.
    Injected callables permit offline interface tests without copying that code.
    """
    if audit is None or write_missing is None:
        try:
            from ark_pipeline.cli.sources_check_hydrobasins import audit

            from ark_pipeline.cli.spatial_pairs import (
                write_missing_basin_relationships as write_missing,
            )
        except ImportError:
            raise RuntimeError(
                "Basin reconciliation requires the compatible spatial-source integration"
            ) from None
    output.mkdir(parents=True, exist_ok=False)
    geometry = audit(root, relations)
    paths = sorted(relations.glob("archives/*/basin_species.parquet"))
    if not paths:
        raise ValueError("Explicit normalized relations are required")
    summary = write_missing(
        paths,
        set(geometry["missing_ids"]),
        output / "missing_relationships.parquet",
        memory_limit="2GB",
        threads=2,
    )
    if summary["omitted_distinct_relationships"] != geometry["missing_distinct_relationships"]:
        raise ValueError("Omission audit does not reconcile with source relationships")
    summary["coverage_status"] = (
        "partial-missing-basin-geometries" if geometry["unique_missing_ids"] else "complete"
    )
    (output / "geometry-audit.json").write_text(json.dumps(geometry, indent=2) + "\n")
    (output / "coverage-summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    return summary


if __name__ == "__main__":
    raise SystemExit(main())
