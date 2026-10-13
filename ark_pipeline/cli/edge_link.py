"""Link all registered Ark assessments to Duke's unchanged global Tree of Life."""

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
from ark_pipeline.edge import (
    acquire_inputs,
    assessment_join,
    canonical_taxid,
    load_ncbi_merges,
    reviewed_tip_join,
)
from ark_pipeline.edge_links import (
    build_tip_index,
    current_ncbi_ranks,
    direct_identifiers,
    exact_name_candidates,
)
from ark_pipeline.edge_matching import (
    build_ott_index,
    current_species_lineages,
    current_species_names,
    improve_matches,
    tip_taxonomy,
)
from ark_pipeline.runtime.provenance import atomic_json, iso_now, sha256


def run(args):
    config = tomllib.loads(args.config.read_text())
    sources = acquire_inputs(
        {"sources": [s for s in config["sources"] if s["id"] in {"dated-tree", "ott-taxonomy"}]},
        args.input_dir,
        args.download,
    )
    root = args.root.expanduser().resolve()
    registry = load_manifest(root)
    assessments = source_file(
        root,
        registry,
        "iucn-red-list-tabular",
        "assessments.csv",
        root / "IUCN_Red_List/assessments.csv",
    )
    summary_path = args.crosswalk.with_name("match_summary.json")
    provenance = json.loads(summary_path.read_text())
    if provenance.get("sources", {}).get("iucn_assessments", {}).get("sha256") != sha256(
        assessments
    ):
        raise ValueError("Crosswalk assessment snapshot is stale; rebuild/review it")
    ncbi = {
        name: source_file(
            root, registry, "ncbi-taxonomy", name + ".dmp", root / "TOL" / (name + ".dmp")
        )
        for name in ["merged", "names", "nodes"]
    }
    merges = load_ncbi_merges(ncbi["merged"])
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    print("Indexing leaves from the unchanged, checksum-verified Duke global tree", flush=True)
    tips = build_tip_index(Path(sources["dated-tree"]["path"]), output / "global_tree_tips.parquet")
    taxonomy = build_ott_index(
        Path(sources["ott-taxonomy"]["path"]), args.input_dir / "ott_index.parquet"
    )
    rows = assessment_join(args.crosswalk, assessments, clade_class=None)
    target_ids = {
        canonical_taxid(row["matched_ncbi_species_taxid"], merges)
        for row in rows
        if row["safe_for_automatic_species_trait_transfer"] is True
        and row["matched_ncbi_species_taxid"]
    }
    print(
        f"Checking {len(rows):,} assessments and {len(target_ids):,} reviewed NCBI identities",
        flush=True,
    )
    identifiers, repeated_ids = direct_identifiers(tips, taxonomy, target_ids, merges)
    baseline = reviewed_tip_join(rows, identifiers, merges)
    ranks = current_ncbi_ranks(ncbi["nodes"], target_ids)
    for row in baseline:
        taxid = row.get("canonical_ncbi_taxid")
        if taxid in repeated_ids:
            row.update(tree_tip=None, join_status="ambiguous_repeated_tree_label")
        if taxid and ranks.get(taxid) != "species":
            row.update(tree_tip=None, join_status="ncbi_not_current_species")
    unresolved = {
        row["canonical_ncbi_taxid"] for row in baseline if row["join_status"] == "ncbi_not_in_clade"
    }
    print(
        f"Direct identity links: {sum(r['join_status'] == 'joined' for r in baseline):,}; checking exact names for {len(unresolved):,} unresolved identities",
        flush=True,
    )
    names = current_species_names(ncbi["names"], unresolved, merges)
    lineages = current_species_lineages(ncbi["nodes"], ncbi["names"], set(names))
    candidates = exact_name_candidates(tips, taxonomy, names)
    if candidates.empty:
        metadata = pd.DataFrame(
            columns=[
                "tree_tip",
                "taxon_rank",
                "normalized_name",
                "ott_genus",
                "ott_family",
                "sourceinfo",
            ]
        )
    else:
        metadata = tip_taxonomy(candidates, taxonomy)
    improved, audit = improve_matches(
        baseline, args.crosswalk, metadata, names, merges, current_lineages=lineages
    )
    species = pd.DataFrame(improved)
    species["current_ncbi_rank"] = species.get(
        "canonical_ncbi_taxid", pd.Series(index=species.index, dtype="str")
    ).map(ranks)
    species["join_status"] = species.join_status.replace(
        {"joined": "linked", "ncbi_not_in_clade": "ncbi_not_in_tree"}
    )
    species["baseline_join_status"] = species.baseline_join_status.replace(
        {"joined": "linked", "ncbi_not_in_clade": "ncbi_not_in_tree"}
    )
    species["candidate_tree_tip"] = species.tree_tip.where(species.join_status.ne("linked"))
    species.loc[species.join_status.ne("linked"), "tree_tip"] = None
    linked = species[species.join_status == "linked"]
    if not species.iucn_sis_id.is_unique or not linked.tree_tip.is_unique:
        raise ValueError("Link table requires unique assessments and unique linked tips")
    with duckdb.connect() as c:
        c.register("linked", linked[["tree_tip"]])
        leaf_data = c.execute(
            """
            SELECT tips.tree_tip,tips.ott_id,tips.terminal_myr,t.taxon_name AS ott_name,
                   t.taxon_rank AS ott_rank,t.flags AS ott_flags,t.sourceinfo AS ott_sourceinfo
            FROM read_parquet(?) tips JOIN linked USING(tree_tip)
            JOIN read_parquet(?) t USING(ott_id)
        """,
            [str(tips), str(taxonomy)],
        ).fetchdf()
        tree_stats = (
            c.execute(
                """
            SELECT count(*) AS tips,count(*) FILTER(WHERE ott_id='0') AS placeholder_tips,
                   count(*) FILTER(WHERE label_occurrences>1) AS ambiguous_label_tips,
                   count(DISTINCT tree_tip) FILTER(WHERE label_occurrences>1) AS duplicate_labels,
                   count(DISTINCT ott_id) FILTER(WHERE ott_id!='0') AS distinct_named_ott_ids
            FROM read_parquet(?)
        """,
                [str(tips)],
            )
            .fetchdf()
            .to_dict("records")[0]
        )
    species = species.merge(leaf_data, on="tree_tip", how="left", validate="many_to_one")
    linked = species[species.join_status == "linked"]
    if linked.ott_id.isna().any() or not linked.ott_rank.eq("species").all():
        raise ValueError("A linked species lacks a species-level OTT tree leaf")
    species["extant_edge_input_candidate"] = species.join_status.eq(
        "linked"
    ) & species.redlist_category.ne("EX")
    species.to_parquet(output / "species_tree_links.parquet", index=False)
    audit.to_parquet(output / "name_link_audit.parquet", index=False)
    metadata.to_parquet(output / "name_candidate_taxonomy.parquet", index=False)
    by_class = (
        species.assign(
            iucn_class=species.iucn_class.fillna("UNKNOWN"),
            linked=species.join_status.eq("linked"),
            direct=species.join_method.eq("ncbi_identifier"),
            recovered=species.join_method.eq("exact_ncbi_name_genus_family"),
        )
        .groupby("iucn_class", as_index=False)
        .agg(
            assessments=("iucn_sis_id", "size"),
            linked=("linked", "sum"),
            direct=("direct", "sum"),
            recovered=("recovered", "sum"),
            extant_candidates=("extant_edge_input_candidate", "sum"),
        )
    )
    by_class["linked_fraction"] = by_class.linked / by_class.assessments
    by_class.to_parquet(output / "coverage_by_class.parquet", index=False)
    report = {
        "schema_version": 1,
        "created_at": iso_now(),
        "status": "global_identity_links_built_tree_suitability_pending",
        "tree_stats": tree_stats,
        "assessments": len(species),
        "linked": len(linked),
        "extant_candidates": int(species.extant_edge_input_candidate.sum()),
        "direct_links": int(species.join_method.eq("ncbi_identifier").sum()),
        "recovered_links": int(species.join_method.eq("exact_ncbi_name_genus_family").sum()),
        "join_status_counts": species.join_status.value_counts().to_dict(),
        "name_candidate_reasons": audit.reason.value_counts().to_dict(),
        "noncurrent_ncbi_rank_counts": species.loc[
            species.join_status.eq("ncbi_not_current_species"), "current_ncbi_rank"
        ]
        .fillna("missing_from_current_nodes")
        .value_counts()
        .to_dict(),
        "coverage_by_class": by_class.to_dict("records"),
        "sources": {
            **sources,
            "assessments": receipt(assessments),
            "crosswalk": receipt(args.crosswalk),
            "crosswalk_provenance": receipt(summary_path),
            **{f"ncbi_{k}": receipt(p) for k, p in ncbi.items()},
            "config": receipt(args.config),
        },
        "code": {
            p.name: receipt(p)
            for p in [
                Path(__file__),
                Path(__file__).parents[1] / "edge.py",
                Path(__file__).parents[1] / "edge_links.py",
                Path(__file__).parents[1] / "edge_matching.py",
            ]
        },
        "limitations": [
            "The original Duke median tree is unchanged. This table links identity; it does not validate inferred evolutionary dates or establish official EDGE scores.",
            "Only registered IUCN crosswalk species are linked here, not every GoaT-only species. GoaT attributes are not refreshed or transferred by this command.",
            "Upstream unsafe crosswalks and ambiguous tree identities remain unresolved; no fuzzy name matching is performed.",
            "Repeated source leaf labels are retained in the unchanged global tree/index, but every occurrence of those labels is excluded from automatic identity linking.",
            "Exact current NCBI names/synonyms require unique species-level OTT leaves, agreeing current genus and family, no conflicting OTT NCBI ID and no reserved tip.",
            "Current NCBI nodes/names/merged files are used together; the reviewed crosswalk may come from an older taxonomy snapshot. Non-species or missing current NCBI identities are rejected.",
            "Extinct assessment identities may be linked, but are excluded from the extant EDGE-input candidate flag. Tree flags are retained and do not override IUCN categories.",
            "An extant candidate flag establishes identity/input availability only. Complete-clade scoring and clade-specific scientific validation remain separate; this output does not alter serving data or map weights.",
        ],
    }
    report["outputs"] = {p.name: receipt(p) for p in sorted(output.glob("*.parquet"))}
    atomic_json(output / "report.json", report)
    text = [
        "# Ark species linked to Duke's global dated tree",
        "",
        f"Linked {len(linked):,} of {len(species):,} assessed species ({len(linked) / len(species):.1%}) to the unchanged Duke median tree.",
        f"{report['direct_links']:,} links use reviewed NCBI → OTT identifiers; {report['recovered_links']:,} use unique exact names with current genus/family agreement.",
        f"The source contains {tree_stats['tips']:,} tips. There are {report['extant_candidates']:,} extant identity/input candidates; these are not scientifically accepted EDGE scores.",
        "",
        "## Coverage",
        "",
        "| Class | Assessments | Linked | Coverage |",
        "|---|---:|---:|---:|",
    ]
    text += [
        f"| {r.iucn_class} | {r.assessments:,} | {r.linked:,} | {r.linked_fraction:.1%} |"
        for r in by_class.sort_values("assessments", ascending=False).itertuples()
    ]
    text += [
        "",
        "## Unresolved and excluded links",
        "",
        *[
            f"- {key}: {value:,}"
            for key, value in report["join_status_counts"].items()
            if key != "linked"
        ],
        "",
        "## Interpretation",
        "",
        "Yes: Ark can use Duke's exact tree and link its assessed species to it. Identity coverage and scientific reliability of resulting priorities are separate questions.",
        "",
        *[f"- {item}" for item in report["limitations"]],
    ]
    (output / "report.md").write_text("\n".join(text) + "\n")
    print(
        json.dumps(
            {
                key: report[key]
                for key in [
                    "assessments",
                    "linked",
                    "direct_links",
                    "recovered_links",
                    "extant_candidates",
                    "join_status_counts",
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
    parser.add_argument(
        "--crosswalk",
        type=Path,
        default=Path("data/exports/iucn_goat_global/iucn_goat_crosswalk.parquet"),
    )
    parser.add_argument("--config", type=Path, default=Path("config/edge_prototype.toml"))
    parser.add_argument("--input-dir", type=Path, default=Path("data/edge/inputs"))
    parser.add_argument("--output-dir", type=Path, default=Path("data/edge/links"))
    parser.add_argument("--download", action="store_true")
    run(parser.parse_args())


if __name__ == "__main__":
    main()
