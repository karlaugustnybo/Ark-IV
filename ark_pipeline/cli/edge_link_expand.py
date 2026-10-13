"""Extend checked tree identity links directly from licensed IUCN taxonomy."""

from __future__ import annotations

import argparse
import json
import os
import tomllib
from pathlib import Path

import duckdb
import pandas as pd

from ark_pipeline.cli.edge_prototype import receipt
from ark_pipeline.cli.serving_metadata import source_file
from ark_pipeline.cli.sources_acquire import load_manifest
from ark_pipeline.edge import acquire_inputs
from ark_pipeline.edge_link_extension import extend_iucn_links
from ark_pipeline.edge_links import exact_name_candidates
from ark_pipeline.edge_matching import build_ott_index, normalize_name, tip_taxonomy
from ark_pipeline.runtime.provenance import atomic_json, iso_now, sha256


def run(args):
    baseline_report_path = args.baseline_dir / "report.json"
    before = json.loads(baseline_report_path.read_text())
    for name in ["species_tree_links.parquet", "global_tree_tips.parquet"]:
        if sha256(args.baseline_dir / name) != before["outputs"][name]["sha256"]:
            raise ValueError("Baseline link artifact checksum changed")
    crosswalk = Path(before["sources"]["crosswalk"]["path"])
    summary = Path(before["sources"]["crosswalk_provenance"]["path"])
    for name, path in [("crosswalk", crosswalk), ("crosswalk_provenance", summary)]:
        if sha256(path) != before["sources"][name]["sha256"]:
            raise ValueError("Crosswalk changed; rebuild baseline tree links")
    config = tomllib.loads(args.config.read_text())
    sources = acquire_inputs(
        {"sources": [s for s in config["sources"] if s["id"] in {"dated-tree", "ott-taxonomy"}]},
        args.input_dir,
        False,
    )
    for name in ["dated-tree", "ott-taxonomy"]:
        if sources[name]["sha256"] != before["sources"][name]["sha256"]:
            raise ValueError("Extension source differs from baseline tree/taxonomy")
    root = args.root.expanduser().resolve()
    registry = load_manifest(root)
    iucn_taxonomy = source_file(
        root, registry, "iucn-red-list-tabular", "taxonomy.csv", root / "IUCN_Red_List/taxonomy.csv"
    )
    assessments = source_file(
        root,
        registry,
        "iucn-red-list-tabular",
        "assessments.csv",
        root / "IUCN_Red_List/assessments.csv",
    )
    provenance = json.loads(summary.read_text())
    if provenance["sources"]["iucn_taxonomy"]["sha256"] != sha256(iucn_taxonomy) or before[
        "sources"
    ]["assessments"]["sha256"] != sha256(assessments):
        raise ValueError("IUCN source snapshots differ; rebuild/review baseline links")
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    baseline = pd.read_parquet(args.baseline_dir / "species_tree_links.parquet")
    cw = pd.read_parquet(crosswalk)
    with duckdb.connect() as c:
        fresh = (
            c.execute("SELECT * FROM read_csv(?,all_varchar=true)", [str(iucn_taxonomy)])
            .fetchdf()
            .astype(object)
            .where(lambda x: x.notna(), None)
        )
    unresolved = baseline[baseline.join_status != "linked"]
    names = {
        str(row.iucn_sis_id): {normalize_name(row.iucn_scientific_name)}
        for row in unresolved.itertuples()
    }
    tips = args.baseline_dir / "global_tree_tips.parquet"
    taxonomy = build_ott_index(
        Path(sources["ott-taxonomy"]["path"]), args.input_dir / "ott_index.parquet"
    )
    print(f"Checking direct IUCN identity for {len(unresolved):,} unresolved species", flush=True)
    candidates = exact_name_candidates(tips, taxonomy, names)
    if candidates.empty:
        metadata = pd.DataFrame(columns=["tree_tip", "normalized_name", "ott_id"])
    else:
        metadata = tip_taxonomy(candidates, taxonomy, include_ancestor_names=True)
    expanded, audit = extend_iucn_links(baseline, cw, fresh, metadata)
    metadata.to_parquet(output / "iucn_name_candidate_taxonomy.parquet", index=False)
    audit.to_parquet(output / "iucn_tree_link_audit.parquet", index=False)
    # Replace all leaf attributes from the SAME checked source, including new
    # links. Genomic-transfer status and original NCBI candidate IDs stay intact.
    leaf_columns = ["ott_id", "terminal_myr", "ott_name", "ott_rank", "ott_flags", "ott_sourceinfo"]
    expanded = expanded.drop(columns=leaf_columns)
    accepted = expanded[expanded.tree_identity_verified]
    with duckdb.connect() as c:
        c.register("accepted", accepted[["tree_tip"]])
        attributes = c.execute(
            """
            SELECT t.tree_tip,t.ott_id,t.terminal_myr,x.taxon_name AS ott_name,
                   x.taxon_rank AS ott_rank,x.flags AS ott_flags,x.sourceinfo AS ott_sourceinfo,
                   t.label_occurrences
            FROM read_parquet(?) t JOIN accepted USING(tree_tip)
            JOIN read_parquet(?) x USING(ott_id)
        """,
            [str(tips), str(taxonomy)],
        ).fetchdf()
    expanded = expanded.merge(attributes, how="left", on="tree_tip", validate="many_to_one")
    accepted = expanded[expanded.tree_identity_verified]
    if not accepted.ott_rank.eq("species").all() or not accepted.label_occurrences.eq(1).all():
        raise ValueError("New identity link lacks a unique species-level tree occurrence")
    new = expanded[expanded.join_method.eq("exact_iucn_name_three_lineage_ranks")]
    original = expanded[expanded.original_join_status.eq("linked")]
    if len(original) != before["linked"] or not original.set_index("iucn_sis_id").tree_tip.equals(
        baseline[baseline.join_status.eq("linked")].set_index("iucn_sis_id").tree_tip
    ):
        raise ValueError("Extension changed an original checked link")
    expanded.to_parquet(output / "species_tree_links.parquet", index=False)
    by_class = (
        expanded.assign(
            iucn_class=expanded.iucn_class.fillna("UNKNOWN"),
            newly_linked=expanded.join_method.eq("exact_iucn_name_three_lineage_ranks"),
        )
        .groupby("iucn_class", as_index=False)
        .agg(
            assessments=("iucn_sis_id", "size"),
            linked=("tree_identity_verified", "sum"),
            new_links=("newly_linked", "sum"),
            ncbi_verified=("ncbi_to_tree_link_verified", "sum"),
        )
    )
    by_class["linked_fraction"] = by_class.linked / by_class.assessments
    by_class.to_parquet(output / "coverage_by_class.parquet", index=False)
    report = {
        "schema_version": 1,
        "created_at": iso_now(),
        "status": "expanded_tree_identity_links_genomic_transfer_separate",
        "assessments": len(expanded),
        "baseline_linked": before["linked"],
        "linked": len(accepted),
        "new_links": len(new),
        "new_links_with_order_class_ancestor_membership": int(
            audit.loc[audit.accepted, "ancestor_membership_ranks"].fillna("").ne("").sum()
        ),
        "ncbi_verified_links": int(expanded.ncbi_to_tree_link_verified.sum()),
        "new_links_by_original_status": new.original_join_status.value_counts().to_dict(),
        "remaining_join_status_counts": expanded.loc[
            ~expanded.tree_identity_verified, "join_status"
        ]
        .value_counts()
        .to_dict(),
        "audit_reason_counts": audit.reason.value_counts().to_dict(),
        "coverage_by_class": by_class.to_dict("records"),
        "sources": {
            **sources,
            "baseline_report": receipt(baseline_report_path),
            "baseline_species": receipt(args.baseline_dir / "species_tree_links.parquet"),
            "tree_tips": receipt(tips),
            "crosswalk": receipt(crosswalk),
            "crosswalk_provenance": receipt(summary),
            "iucn_taxonomy": receipt(iucn_taxonomy),
            "assessments": receipt(assessments),
            "config": receipt(args.config),
        },
        "code": {
            p.name: receipt(p)
            for p in [
                Path(__file__),
                Path(__file__).parents[1] / "edge_link_extension.py",
                Path(__file__).parents[1] / "edge_matching.py",
                Path(__file__).parents[1] / "edge_links.py",
            ]
        },
        "limitations": [
            "Original checked links, the Duke tree and all upstream genomic-transfer safety flags are preserved.",
            "New links verify IUCN-to-OTT tree identity from unique exact current IUCN names, exact genus/family and at least one additional order/class match, with no comparable lineage conflict. IUCN order/class names may match exact OTT ancestors at another rank (recorded in the audit); this establishes named-clade membership, not synonym equivalence. They do not verify the old GoaT/NCBI candidate.",
            "New IUCN-only links have ncbi_to_tree_link_verified=false. Do not use them as automatic NCBI/GoaT genomic-transfer approvals; the old candidate ID and transfer flag retain their original upstream meaning.",
            "Unsafe existing MATCHED species-concept records, assessment/taxonomy conflicts, infraspecies/subpopulations, reserved tips, repeated labels and competing proposals are rejected.",
            "No fuzzy name matching or new tree tips are introduced. Exact-name taxonomic concept equivalence across source vintages still requires scientific review.",
            "Coverage describes identity availability, not official EDGE ranks, confidence in estimated dates, genomic-data coverage or global scoring completion.",
        ],
    }
    report["outputs"] = {p.name: receipt(p) for p in sorted(output.glob("*.parquet"))}
    atomic_json(output / "report.json", report)
    text = [
        "# Expanded IUCN-to-Duke tree identity links",
        "",
        f"Coverage increased from {before['linked']:,} to {len(accepted):,} of {len(expanded):,} assessments ({len(accepted) / len(expanded):.1%}).",
        f"Added {len(new):,} independent exact IUCN name/lineage links while preserving the original {before['linked']:,} NCBI-verified links.",
        "",
        "The new links establish IUCN/tree identity only. Every new link has ncbi_to_tree_link_verified=false; upstream genomic-transfer decisions are unchanged.",
        "",
        "## Coverage",
        "",
        "| Class | Assessed | Linked | New links |",
        "|---|---:|---:|---:|",
    ]
    text += [
        f"| {row.iucn_class} | {row.assessments:,} | {row.linked:,} | {row.new_links:,} |"
        for row in by_class.sort_values("assessments", ascending=False).itertuples()
    ]
    text += [
        "",
        "## Rejected candidates",
        "",
        *[
            f"- {key}: {value:,}"
            for key, value in report["audit_reason_counts"].items()
            if key != "eligible"
        ],
        "",
        "## Limits",
        "",
        *[f"- {item}" for item in report["limitations"]],
    ]
    (output / "report.md").write_text("\n".join(text) + "\n")
    print(
        json.dumps(
            {
                key: report[key]
                for key in [
                    "baseline_linked",
                    "linked",
                    "new_links",
                    "new_links_by_original_status",
                    "audit_reason_counts",
                ]
            },
            indent=2,
        ),
        flush=True,
    )
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root", type=Path, default=Path(os.environ.get("GLOBAL_DATA_ROOT", "data/global"))
    )
    parser.add_argument("--config", type=Path, default=Path("config/edge_prototype.toml"))
    parser.add_argument("--input-dir", type=Path, default=Path("data/edge/inputs"))
    parser.add_argument("--baseline-dir", type=Path, default=Path("data/edge/links"))
    parser.add_argument("--output-dir", type=Path, default=Path("data/edge/links-expanded"))
    run(parser.parse_args())


if __name__ == "__main__":
    main()
