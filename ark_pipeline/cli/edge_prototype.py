"""Acquire or run the optional mammal dated-tree/EDGE-style investigation."""

from __future__ import annotations

import argparse
import json
import os
import tomllib
from pathlib import Path

import pandas as pd

from ark_pipeline.cli.serving_metadata import source_file
from ark_pipeline.cli.sources_acquire import load_manifest
from ark_pipeline.edge import (
    acquire_inputs,
    assessment_join,
    compare_reference,
    load_clade,
    load_ncbi_merges,
    read_mammal_reference,
    reviewed_tip_join,
    score_clade,
    tip_identifiers,
)
from ark_pipeline.runtime.provenance import atomic_json, git_state, iso_now, sha256


def receipt(path: Path) -> dict:
    return {"path": str(path.resolve()), "sha256": sha256(path), "bytes": path.stat().st_size}


def run(args, config: dict, sources: dict) -> dict:
    from importlib.metadata import distribution

    engine_distribution = distribution("dated-complete-tree")
    engine_origin = json.loads(engine_distribution.read_text("direct_url.json") or "{}")
    if engine_origin.get("vcs_info", {}).get("commit_id") != config["engine_commit"]:
        raise ValueError(
            "Installed dated-tree engine differs from configured commit; run uv sync --group edge"
        )
    root = args.root.expanduser().resolve()
    manifest = load_manifest(root)
    assessments = source_file(
        root,
        manifest,
        "iucn-red-list-tabular",
        "assessments.csv",
        root / "IUCN_Red_List/assessments.csv",
    )
    crosswalk = args.crosswalk.expanduser().resolve()
    summary_path = crosswalk.with_name("match_summary.json")
    if not summary_path.is_file():
        raise ValueError("Crosswalk requires match_summary.json source provenance")
    summary = json.loads(summary_path.read_text())
    if summary.get("sources", {}).get("iucn_assessments", {}).get("sha256") != sha256(assessments):
        raise ValueError("Crosswalk assessment snapshot is stale; rebuild/review the crosswalk")
    # No GoaT attributes enter this prototype; retain the reviewed crosswalk
    # snapshot even when a newer GoaT export has been acquired.
    merged = source_file(root, manifest, "ncbi-taxonomy", "merged.dmp", root / "TOL/merged.dmp")
    merges = load_ncbi_merges(merged)
    print("Extracting complete mammal crown", flush=True)
    tree = load_clade(Path(sources["dated-tree"]["path"]), config["clade"])
    print(f"Indexing OTT identifiers for {len(tree):,} tree tips", flush=True)
    identifiers = tip_identifiers(tree, Path(sources["ott-taxonomy"]["path"]), merges)
    joined = reviewed_tip_join(assessment_join(crosswalk, assessments), identifiers, merges)
    if not any(
        row["join_status"] == "joined" and row["redlist_category"] != "EX" for row in joined
    ):
        raise ValueError("No reviewed, globally assessed extant mammals resolve to tree tips")
    print(f"Computing ED and {args.samples} seeded risk draws", flush=True)
    scores, diagnostics = score_clade(tree, joined, args.samples, args.seed)
    score_by_tip = {row["tree_tip"]: row for row in scores}
    for row in joined:
        if row["join_status"] == "joined":
            if row["redlist_category"] == "EX":
                row["join_status"] = "excluded_extinct"
            else:
                row.update(score_by_tip[row["tree_tip"]])
                row["join_status"] = "scored"
    species = pd.DataFrame(joined)
    comparison, agreement = compare_reference(
        species, read_mammal_reference(Path(sources["mammal-reference"]["path"]))
    )
    output = args.output_dir.expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    species.to_parquet(output / "species_edge_prototype.parquet", index=False)
    pd.DataFrame(scores).to_parquet(output / "complete_clade_scores.parquet", index=False)
    comparison.to_parquet(output / "mammal_reference_comparison.parquet", index=False)
    tree.write(str(output / "mammal_crown.tre"), parser=1, format_root_node=True)
    from importlib.metadata import version

    report = {
        "schema_version": 1,
        "status": "prototype_unvalidated",
        "created_at": iso_now(),
        "clade": config["clade"],
        "samples": args.samples,
        "seed": args.seed,
        "phylogenetic_tree_count": 1,
        "engine_commit": config["engine_commit"],
        "engine_version": version("dated-complete-tree"),
        "git": git_state(Path.cwd()),
        "sources": {
            **sources,
            "crosswalk": receipt(crosswalk),
            "crosswalk_provenance": receipt(summary_path),
            "assessments": receipt(assessments),
            "ncbi_merged_ids": receipt(merged),
            "config": receipt(args.config),
        },
        "code": {
            "edge": receipt(Path(__file__).resolve().parents[1] / "edge.py"),
            "cli": receipt(Path(__file__).resolve()),
        },
        "outputs": {
            path.name: receipt(path)
            for path in (
                output / "species_edge_prototype.parquet",
                output / "complete_clade_scores.parquet",
                output / "mammal_reference_comparison.parquet",
                output / "mammal_crown.tre",
            )
        },
        "coverage": {
            "registered_mammals": len(species),
            "join_status_counts": species.join_status.value_counts().to_dict(),
        },
        "tree": diagnostics,
        "reference_comparison": agreement,
        "limitations": [
            "One equal-splits median tree; intervals measure extinction-risk uncertainty only.",
            "Scores computed on every retained mammal crown tip before joining assessments; mammalian stem excluded.",
            "DD, NE and unjoined tips draw independently from the engine's pooled risk distribution.",
            "EX tips with safe joins are removed; EW receives fixed p=0.97 and no candidate list.",
            "Possibly-extinct flags are preserved without silently changing categories or extinction probabilities.",
            "Risk-only candidate lists are exploratory and are not official EDGE Species designations.",
            "No Ark score weights, map cells or serving artifacts consume this output.",
            "Chronogram versus interpolated-date provenance is not yet available for residual attribution.",
            "The released crown retains promoted subspecies, placeholder taxa and OTT flag anomalies; extant status is not independently validated.",
        ],
    }
    atomic_json(output / "report.json", report)
    counts = report["coverage"]["join_status_counts"]
    lines = [
        "# Ark mammal dated-tree prototype",
        "",
        "Status: descriptive, unvalidated prototype.",
        "",
        f"Scored **{counts.get('scored', 0):,} of {len(species):,} registered mammals** on a complete released crown of **{diagnostics['retained_tree_tips']:,} retained tips**.",
        f"Used one median tree and {args.samples:,} risk draws (seed {args.seed}).",
        "",
        "## Identifier coverage",
        "",
        "| Result | Species |",
        "|---|---:|",
    ]
    lines += [f"| {status.replace('_', ' ')} | {count:,} |" for status, count in counts.items()]
    lines += [
        "",
        "## Published mammal comparison",
        "",
        agreement["interpretation"],
        "",
        "| Cohort | Species | ED2 rank correlation | EDGE2 rank correlation |",
        "|---|---:|---:|---:|",
    ]
    for label, stats in (
        ("All SIS joins", agreement["all_joined"]),
        ("Same threat category", agreement["same_category"]),
    ):
        values = [
            f"{stats[key]:.4f}" if stats[key] is not None else "Unavailable"
            for key in ("ed2_spearman", "edge2_spearman")
        ]
        lines.append(f"| {label} | {stats['species']:,} | {values[0]} | {values[1]} |")
    lines += [
        "",
        f"Threat category differs for {agreement['category_changed_species']:,} matched species.",
        "",
        "## Interpretation",
        "",
    ] + [f"- {item}" for item in report["limitations"]]
    lines += [
        "",
        "## Inputs and methods",
        "",
        "- [Dated tree release](https://zenodo.org/records/19049120)",
        "- [Pinned reference engine](https://github.com/jdduke24/dated-complete-tree/tree/"
        + config["engine_commit"]
        + ")",
        "- [EDGE2 protocol and mammal supplement](https://doi.org/10.1371/journal.pbio.3001991)",
        "- [Original EDGE definition](https://doi.org/10.1371/journal.pone.0000296)",
        "",
        "Full components, intervals, flags and residuals are in the adjacent Parquet files. Input hashes and run identity are in report.json.",
    ]
    (output / "report.md").write_text("\n".join(lines) + "\n")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("config/edge_prototype.toml"))
    parser.add_argument("--input-dir", type=Path, default=Path("data/edge/inputs"))
    parser.add_argument(
        "--root", type=Path, default=Path(os.environ.get("GLOBAL_DATA_ROOT", "data/external"))
    )
    parser.add_argument(
        "--crosswalk",
        type=Path,
        default=Path(
            os.environ.get(
                "GLOBAL_CROSSWALK_PATH", "data/exports/iucn_goat_global/iucn_goat_crosswalk.parquet"
            )
        ),
    )
    parser.add_argument("--output-dir", type=Path, default=Path("data/edge/mammals"))
    parser.add_argument("--download", action="store_true")
    parser.add_argument("--download-only", action="store_true")
    parser.add_argument("--samples", type=int, default=256)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args(argv)
    try:
        config = tomllib.loads(args.config.read_text())
        sources = acquire_inputs(config, args.input_dir, args.download or args.download_only)
        atomic_json(args.input_dir / "manifest.json", {"schema_version": 1, "sources": sources})
        if not args.download_only:
            report = run(args, config, sources)
            print(
                json.dumps(
                    {
                        "status": report["status"],
                        "coverage": report["coverage"],
                        "report": str(args.output_dir / "report.md"),
                    },
                    indent=2,
                )
            )
        return 0
    except (OSError, ValueError, ImportError) as error:
        print(json.dumps({"status": "failed", "error": str(error)}, indent=2))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
