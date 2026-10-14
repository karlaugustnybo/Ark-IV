"""Audit mammal matching, published-score disagreements and alternative-tree stability."""

from __future__ import annotations

import argparse
import json
import os
import tomllib
from importlib.metadata import distribution
from pathlib import Path

import pandas as pd

from ark_pipeline.cli.edge_prototype import receipt
from ark_pipeline.cli.serving_metadata import source_file
from ark_pipeline.cli.sources_acquire import load_manifest
from ark_pipeline.edge import (
    acquire_inputs,
    assessment_join,
    canonical_taxid,
    compare_reference,
    load_clade,
    load_ncbi_merges,
    read_mammal_reference,
    reviewed_tip_join,
    score_clade,
    tip_identifiers,
)
from ark_pipeline.edge_links import current_ncbi_ranks
from ark_pipeline.edge_matching import (
    build_ott_index,
    current_species_lineages,
    current_species_names,
    improve_matches,
    tree_taxonomy,
)
from ark_pipeline.edge_validation import (
    align_mammal_crown,
    outlier_diagnostics,
    rank_tree,
    scored_species,
    stability_summary,
    topology_signature,
)
from ark_pipeline.runtime.provenance import atomic_json, iso_now, sha256


def run(args):
    config = tomllib.loads(args.config.read_text())
    origin = json.loads(distribution("dated-complete-tree").read_text("direct_url.json") or "{}")
    if origin.get("vcs_info", {}).get("commit_id") != config["engine_commit"]:
        raise ValueError("Engine differs from the pinned commit; run uv sync --group edge")
    sources = acquire_inputs(config, args.input_dir, False)
    validation_sources = acquire_inputs(
        {"sources": config["validation_sources"]}, args.input_dir, False
    )
    manifests = []
    for source in config["tree_archives"]:
        path = args.input_dir / source["id"] / "manifest.json"
        manifest = json.loads(path.read_text())
        if manifest["source"] != source or manifest["clade"] != config["clade"]:
            raise ValueError("Tree archive receipt differs from configuration")
        for item in manifest["trees"]:
            if sha256(Path(item["path"])) != item["sha256"]:
                raise ValueError("Extracted tree checksum changed")
        manifests.append(manifest)
    root = args.root.expanduser().resolve()
    registry = load_manifest(root)
    assessments = source_file(
        root,
        registry,
        "iucn-red-list-tabular",
        "assessments.csv",
        root / "IUCN_Red_List/assessments.csv",
    )
    provenance_path = args.crosswalk.with_name("match_summary.json")
    provenance = json.loads(provenance_path.read_text())
    if provenance.get("sources", {}).get("iucn_assessments", {}).get("sha256") != sha256(
        assessments
    ):
        raise ValueError("Crosswalk assessment snapshot is stale; rebuild/review it")
    merged = source_file(root, registry, "ncbi-taxonomy", "merged.dmp", root / "TOL/merged.dmp")
    names_path = source_file(root, registry, "ncbi-taxonomy", "names.dmp", root / "TOL/names.dmp")
    nodes_path = source_file(root, registry, "ncbi-taxonomy", "nodes.dmp", root / "TOL/nodes.dmp")
    merges = load_ncbi_merges(merged)
    tree = load_clade(Path(sources["dated-tree"]["path"]), config["clade"])
    original_tips = {leaf.name for leaf in tree.leaves()}
    identifiers = tip_identifiers(tree, Path(sources["ott-taxonomy"]["path"]), merges)
    rows = assessment_join(args.crosswalk, assessments)
    ranks = current_ncbi_ranks(
        nodes_path,
        {
            canonical_taxid(str(row["matched_ncbi_species_taxid"]), merges)
            for row in rows if row["matched_ncbi_species_taxid"]
        },
    )
    baseline_joined = reviewed_tip_join(rows, identifiers, merges, current_ranks=ranks)
    print("Auditing exact names against genus, family and existing identifiers", flush=True)
    index = build_ott_index(
        Path(sources["ott-taxonomy"]["path"]), args.input_dir / "ott_index.parquet"
    )
    metadata = tree_taxonomy(tree, index)
    names = current_species_names(
        names_path,
        {
            row["canonical_ncbi_taxid"]
            for row in baseline_joined
            if row["join_status"] == "ncbi_not_in_clade"
        },
        merges,
    )
    current_lineages = current_species_lineages(nodes_path, names_path, set(names))
    joined, audit = improve_matches(
        baseline_joined, args.crosswalk, metadata, names, merges, current_lineages=current_lineages
    )
    output = args.output_dir
    output.mkdir(parents=True, exist_ok=True)
    metadata.to_parquet(output / "tip_taxonomy.parquet", index=False)
    audit.to_parquet(output / "matching_audit.parquet", index=False)
    if not any(
        row["join_status"] == "joined" and row["redlist_category"] != "EX" for row in joined
    ):
        raise ValueError(
            "No eligible assessed species after current-identity matching; validation cohort unsupported"
        )
    reference = read_mammal_reference(Path(sources["mammal-reference"]["path"]))
    print("Scoring the original matching route for a controlled comparison", flush=True)
    scores, _ = score_clade(
        tree.copy(), baseline_joined, args.samples, args.seed, stable_risk_order=True
    )
    baseline_species = scored_species(baseline_joined, scores)
    baseline_comparison, baseline_agreement = compare_reference(baseline_species, reference)
    tree_specs = [
        ("equal-median", "equal-median", None),
        ("birth-median", "birth-median", Path(validation_sources["birth-model-median"]["path"])),
    ]
    for manifest in manifests:
        tree_specs.extend(
            (f"{manifest['source']['id']}-{i:03d}", manifest["source"]["id"], Path(item["path"]))
            for i, item in enumerate(manifest["trees"])
        )
    ranked = []
    diagnostics = {}
    for tree_id, group, path in tree_specs:
        print(f"Scoring {tree_id} ({args.samples} common risk draws)", flush=True)
        if path is None:
            candidate = tree.copy()
        else:
            candidate = load_clade(path, config["clade"])
        raw_tip_count = len(candidate)
        extras = {leaf.name for leaf in candidate.leaves()} - original_tips
        extra_metadata = tree_taxonomy(candidate, index) if extras else metadata
        removed_environmental = align_mammal_crown(candidate, original_tips, extra_metadata)
        topology_hash = topology_signature(candidate)
        tip_metadata = metadata.set_index("tree_tip").to_dict("index")
        for leaf in candidate.leaves():
            meta = tip_metadata.get(leaf.name, {})
            leaf.add_prop("ott_rank", meta.get("taxon_rank"))
            leaf.add_prop("ott_flags", meta.get("flags"))
        scores, diagnostics[tree_id] = score_clade(
            candidate, joined, args.samples, args.seed, stable_risk_order=True
        )
        diagnostics[tree_id]["released_crown_tips"] = raw_tip_count
        diagnostics[tree_id]["removed_nonmammal_environmental_tips"] = removed_environmental
        diagnostics[tree_id]["topology_sha256"] = topology_hash
        diagnostics[tree_id]["excluded_environmental_crown_stem_myr"] = candidate.props.get(
            "excluded_environmental_crown_stem_myr", 0
        )
        species = scored_species(joined, scores)
        ranked.append(rank_tree(species, tree_id, group))
        if tree_id == "equal-median":
            improved_species = species
            pd.DataFrame(scores).to_parquet(output / "complete_clade_scores.parquet", index=False)
    improved_species.to_parquet(output / "species_edge_validated_pilot.parquet", index=False)
    comparison, agreement = compare_reference(improved_species, reference)
    comparison.to_parquet(output / "reference_comparison.parquet", index=False)
    common_ids = set(baseline_comparison.iucn_sis_id)
    _, controlled_agreement = compare_reference(
        improved_species[improved_species.iucn_sis_id.isin(common_ids)], reference
    )
    results = pd.concat(ranked, ignore_index=True)
    results.to_parquet(output / "tree_species_scores.parquet", index=False)
    top_frequency = (
        results.assign(in_threatened_top100=results.threatened_rank <= 100)
        .groupby(["iucn_sis_id", "iucn_scientific_name", "redlist_category"], as_index=False)
        .agg(
            top100_trees=("in_threatened_top100", "sum"),
            trees=("tree_id", "nunique"),
            rank_min=("assessed_rank", "min"),
            rank_max=("assessed_rank", "max"),
        )
    )
    top_frequency.to_parquet(output / "top100_pilot_frequency.parquet", index=False)
    equal = results[results.tree_id == "equal-median"]
    stability, stability_stats = stability_summary(
        results[results.tree_id != "equal-median"], equal
    )
    stability.to_parquet(output / "species_tree_stability.parquet", index=False)
    birth_stability, birth_stability_stats = stability_summary(
        results[results.tree_group.isin(["birth-topology", "birth-dates"])],
        results[results.tree_id == "birth-median"],
    )
    birth_stability.to_parquet(
        output / "species_tree_stability_vs_birth_median.parquet", index=False
    )
    outliers = outlier_diagnostics(comparison, stability)
    outliers.to_parquet(output / "outlier_diagnostics.parquet", index=False)
    date_topologies = {
        value["topology_sha256"]
        for key, value in diagnostics.items()
        if key.startswith("birth-dates-")
    }
    if date_topologies != {diagnostics["birth-median"]["topology_sha256"]}:
        raise ValueError("Date pilot topology differs from the birth median")
    topology_count = len(
        {
            value["topology_sha256"]
            for key, value in diagnostics.items()
            if key.startswith("birth-topology-")
        }
    )
    report = {
        "schema_version": 1,
        "created_at": iso_now(),
        "status": "validation_pilot_not_official_EDGE",
        "samples_per_tree": args.samples,
        "seed": args.seed,
        "engine_commit": config["engine_commit"],
        "tree_count": len(tree_specs),
        "topology_audit": {
            "birth_date_topology_matches_birth_median": True,
            "birth_topology_unique_mammal_topologies": topology_count,
            "median_trees_share_topology": diagnostics["birth-median"]["topology_sha256"]
            == diagnostics["equal-median"]["topology_sha256"],
        },
        "threatened_top100_in_every_pilot_tree": int(
            (top_frequency.top100_trees == top_frequency.trees).sum()
        ),
        "coverage": {
            "registered_mammals": len(joined),
            "baseline_scored": int((baseline_species.join_status == "scored").sum()),
            "improved_scored": int((improved_species.join_status == "scored").sum()),
            "new_exact_name_matches": int(
                (improved_species.join_method == "exact_ncbi_name_genus_family").sum()
            ),
            "join_status_counts": improved_species.join_status.value_counts().to_dict(),
            "matching_candidate_reasons": audit.reason.value_counts().to_dict(),
            "named_nonplaceholder_tips_without_ott_metadata": sum(
                not leaf.name.endswith("ott0000") for leaf in tree.leaves()
            )
            - len(metadata),
        },
        "reference_before": baseline_agreement,
        "reference_after": agreement,
        "reference_after_on_original_cohort": controlled_agreement,
        "tree_stability": stability_stats,
        "tree_stability_vs_birth_median": birth_stability_stats,
        "tree_diagnostics": diagnostics,
        "sources": {
            **sources,
            **validation_sources,
            "assessments": receipt(assessments),
            "crosswalk": receipt(args.crosswalk),
            "crosswalk_provenance": receipt(provenance_path),
            "ncbi_names": receipt(names_path),
            "ncbi_nodes": receipt(nodes_path),
            "ncbi_merges": receipt(merged),
            "config": receipt(args.config),
        },
        "archives": manifests,
        "code": {
            name: receipt(Path(__file__).parents[1] / name)
            for name in [
                "edge.py", "edge_links.py", "edge_matching.py", "edge_trees.py",
                "edge_validation.py", "extinction_status.py",
            ]
        },
        "limitations": [
            "Eight first eligible archive members by default: a convenience pilot, not a random or complete 101-tree ensemble.",
            "Archive downloads stop after the requested crowns; full archive MD5 is not verified. Saved crowns and received prefixes have SHA256 receipts.",
            "Ranks are within safely scored IUCN mammals; top 100 means the 100 highest-scoring threatened mammals in this cohort.",
            "Top-100 cutoffs include tied ranks. Equal-median comparisons combine interpolation-model and within-model tree effects; comparisons against the birth median separate the baseline model change.",
            "The two released median trees have different topologies; their comparison does not isolate interpolation method. Global topology samples can repeat a mammal topology.",
            "Same seeded risk draws are assigned by sorted tip name across identical full tip sets. Pilot ranges are descriptive, not confidence intervals.",
            "Extra environmental samples without a mammalian class are pruned from alternative crowns, preserving branches and then excluding the resulting crown stem; each exclusion/stem is recorded. Other tip-universe changes fail the run.",
            "The available node-age cache postdates the released trees and cannot establish release-specific calibrated/interpolated provenance.",
            "Category changes, assessed coverage, topology, branch dates and risk models can all contribute to disagreements with the 2023 reference.",
            "No official EDGE Species designation; no map, serving scores or Ark weighting changes. Retained taxonomic/placeholder/flag caveats remain.",
        ],
    }
    report["outputs"] = {path.name: receipt(path) for path in sorted(output.glob("*.parquet"))}
    report["code"]["cli"] = receipt(Path(__file__))
    atomic_json(output / "report.json", report)
    write_report(output / "report.md", report, outliers, stability)
    return report


def write_report(path, report, outliers, stability):
    coverage = report["coverage"]
    same = report["reference_after"]["same_category"]
    correlations = {
        key: f"{same[key]:.3f}" if same[key] is not None else "Unavailable"
        for key in ("edge2_spearman", "ed2_spearman")
    }
    lines = [
        "# What the mammal EDGE follow-up tells us",
        "",
        "This is a working research pilot. Treat high scores as leads to investigate, and inspect tree-sensitive species before using their rank to make decisions.",
        "",
        "## Matching species",
        "",
        f"The stricter name route added **{coverage['new_exact_name_matches']:,} matches**. Scoring coverage changed from **{coverage['baseline_scored']:,} to {coverage['improved_scored']:,} of {coverage['registered_mammals']:,} mammals**.",
        "A new match needs an exact current NCBI scientific name or synonym, the same genus and family, one unique species tip, and no conflicting identifier or competing species. Ambiguous and upstream unsafe cases stay excluded.",
        "",
        "## Agreement with published scores",
        "",
        f"For {same['species']:,} species with the same threat category, rank agreement is **{correlations['edge2_spearman']} for EDGE2** and **{correlations['ed2_spearman']} for ED2**, where 1 means identical ordering.",
        "Unavailable correlation means the cohort is too small or has constant ranks to establish an ordering.",
        "Matching coverage and score accuracy are separate questions. The controlled before/after comparison in report.json uses the original comparison cohort, with identical seeded risk draws.",
        "",
        "## What changing the tree does",
        "",
        f"We scored {report['tree_count']} trees using the same assessed species and the same random risk draws. The two released median trees differ in both model and relationships, so their comparison cannot isolate a dating-method effect. The archive pilots vary relationships or calibration dates under the birth model.",
        "",
        "| Tree comparison | Trees | Top 100 retained from equal-splits median | Top 100 retained from birth median |",
        "|---|---:|---:|---:|",
    ]
    for group, stats in report["tree_stability"].items():
        birth_stats = report["tree_stability_vs_birth_median"].get(group)
        birth_overlap = (
            f"{birth_stats['top100_threatened_overlap_min']}–{birth_stats['top100_threatened_overlap_max']}"
            if birth_stats
            else "Same tree" if group == "birth-median" else "Unavailable"
        )
        lines.append(
            f"| {group} | {stats['trees']} | {stats['top100_threatened_overlap_min']}–{stats['top100_threatened_overlap_max']} | {birth_overlap} |"
        )
    lines += [
        "",
        "These are small convenience samples, so a species appearing in every sampled top 100 is promising evidence of stability, not a guarantee across all possible trees.",
        f"**{report['threatened_top100_in_every_pilot_tree']} threatened species** stayed in the top 100 across all {report['tree_count']} pilot trees. The date pilot has the same relationships as the birth median; the relationship pilot contains {report['topology_audit']['birth_topology_unique_mammal_topologies']} distinct mammal topologies. The birth-median column compares trees within one interpolation model.",
        "",
        "## Why some published scores disagree",
        "",
        "The table below shows the ten largest absolute EDGE2 score differences. Long or short terminal branches change how much evolutionary history a species represents. A large branch discrepancy identifies a plausible contributor; it does not prove that a date was interpolated incorrectly.",
        "",
        "| Species | Category Ark / reference | Ark EDGE2 | Reference EDGE2 | Ark terminal branch (Myr) | Reference terminal branch (Myr) |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for row in outliers.head(10).itertuples():
        lines.append(
            f"| {row.iucn_scientific_name} | {row.redlist_category} / {row.reference_category} | {row.ark_edge2_myr_median:.2f} | {row.reference_edge2_myr:.2f} | {row.terminal_branch_myr:.2f} | {row.reference_terminal_myr:.2f} |"
        )
    lines += [
        "",
        "The largest disagreements in the evolutionary-uniqueness part (ED2) also include species with low threat risk, which can be hidden by the combined EDGE2 score. Below, negative rank differences mean Ark ranks the species higher than the reference.",
        "",
        "| Species | ED2 rank difference, same category | Ark terminal branch (Myr) | Reference terminal branch (Myr) |",
        "|---|---:|---:|---:|",
    ]
    uniqueness_outliers = outliers[outliers.category_agrees].sort_values(
        "ed2_same_category_rank_delta", key=lambda values: values.abs(), ascending=False
    )
    for row in uniqueness_outliers.head(6).itertuples():
        lines.append(
            f"| {row.iucn_scientific_name} | {row.ed2_same_category_rank_delta:+.0f} | {row.terminal_branch_myr:.3f} | {row.reference_terminal_myr:.3f} |"
        )
    lines += [
        "",
        "## What to think about it",
        "",
        "The calculations run, matching is auditable, and we can now see which rankings move when the tree changes. Use the stable candidates as research leads. Rankings with large branch disagreements or large rank ranges need more checking before they can support conservation decisions.",
        "",
        "The available calibration cache is dated July 2026, after the March 2026 tree release. It is saved as evidence of that limitation, and is not used to claim which released dates were directly calibrated.",
        "",
        "## Limits and reproducibility",
        "",
    ] + ["- " + item for item in report["limitations"]]
    lines += [
        "",
        "Read `matching_audit.parquet` for every attempted exact-name match, `outlier_diagnostics.parquet` for individual disagreements, and `species_tree_stability.parquet` for each species’ rank range. `report.json` records source/code/output hashes and per-tree checks.",
        "",
        "[Official dated-tree release](https://zenodo.org/records/19049120) · [EDGE2 paper and mammal supplement](https://doi.org/10.1371/journal.pbio.3001991)",
        "",
    ]
    path.write_text("\n".join(lines))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("config/edge_prototype.toml"))
    parser.add_argument("--input-dir", type=Path, default=Path("data/edge/inputs"))
    parser.add_argument(
        "--root", type=Path, default=Path(os.environ.get("GLOBAL_DATA_ROOT", "data/external"))
    )
    parser.add_argument(
        "--crosswalk",
        type=Path,
        default=Path("data/exports/iucn_goat_global/iucn_goat_crosswalk.parquet"),
    )
    parser.add_argument("--output-dir", type=Path, default=Path("data/edge/validation"))
    parser.add_argument("--samples", type=int, default=128)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args(argv)
    result = run(args)
    print(
        json.dumps(
            {"coverage": result["coverage"], "report": str(args.output_dir / "report.md")}, indent=2
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
