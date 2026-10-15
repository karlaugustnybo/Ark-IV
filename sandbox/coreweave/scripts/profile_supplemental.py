"""Stress complete real point tables, basin normalization and geometry ID reconciliation."""

import argparse
import json
from pathlib import Path

from ark_pipeline.cli.spatial_pairs import (
    build_point_archive,
    load_acquisition_manifest,
    normalize_hydrobasin_archive,
    supplemental_files,
)
from ark_pipeline.runtime.provenance import atomic_json
from ark_pipeline.spatial.coverage import load_spatial_profile


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument(
        "--reconcile", action="store_true", help="Requires compatible spatial integration"
    )
    args = parser.parse_args(argv)
    root, out = args.root, args.output
    if out.exists():
        parser.error("Use a fresh output root")
    out.mkdir(parents=True)
    profile = load_spatial_profile(args.profile)
    reports = []
    for record in supplemental_files(root, load_acquisition_manifest(root)):
        identity = {
            "source_sha256": record["sha256"],
            "profile_sha256": profile.digest,
            "scope": "stress-only",
        }
        if record["format"] == "point":
            report = build_point_archive(record, out, profile, identity, force=True)
        else:
            report = normalize_hydrobasin_archive(
                record,
                out,
                profile,
                identity,
                scratch_dir=out / "scratch",
                memory_limit="2GB",
                threads=8,
                force=True,
            )
        reports.append({key: value for key, value in report.items() if key != "path"})
    atomic_json(
        out / "supplemental-report.json",
        {
            "status": "passed",
            "archives": reports,
            "scope": "Point transformation and basin normalization only; geometry reconciliation and basin-to-H3 join are not verified",
        },
    )
    if args.reconcile:
        from preflight_checks import reconcile_basins

        reconcile_basins(root, out, out / "coverage-audit")
    print(json.dumps(reports), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
