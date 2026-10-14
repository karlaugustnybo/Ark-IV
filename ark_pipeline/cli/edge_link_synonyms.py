"""Investigate remaining tree identities using documented alternative names."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import duckdb
import pandas as pd

from ark_pipeline.cli.edge_prototype import receipt
from ark_pipeline.cli.serving_metadata import source_file
from ark_pipeline.cli.sources_acquire import load_manifest
from ark_pipeline.edge import load_ncbi_merges
from ark_pipeline.edge_links import current_ncbi_ranks, direct_identifiers, exact_name_candidates
from ark_pipeline.edge_matching import normalize_name, tip_taxonomy
from ark_pipeline.edge_synonyms import (
    build_synonym_index,
    extend_synonym_links,
    ncbi_names_for_iucn,
    prepare_candidates,
)
from ark_pipeline.runtime.provenance import atomic_json, iso_now, sha256


def run(args):
    prior_path = args.baseline_dir / "report.json"
    prior = json.loads(prior_path.read_text())
    first = json.loads(Path(prior["sources"]["baseline_report"]["path"]).read_text())
    for key, value in prior["sources"].items():
        if sha256(Path(value["path"])) != value["sha256"]:
            raise ValueError(f"Baseline source changed: {key}")
    for name, value in prior["outputs"].items():
        if sha256(args.baseline_dir / name) != value["sha256"]:
            raise ValueError(f"Baseline output changed: {name}")
    root = args.root.expanduser().resolve()
    registry = load_manifest(root)
    for logical, key in [("taxonomy.csv", "iucn_taxonomy"), ("assessments.csv", "assessments")]:
        path = source_file(
            root, registry, "iucn-red-list-tabular", logical, root / "IUCN_Red_List" / logical
        )
        if sha256(path) != prior["sources"][key]["sha256"]:
            raise ValueError("Registered IUCN snapshot differs from expanded identity links")
    paths = {
        name: source_file(
            root, registry, "ncbi-taxonomy", name + ".dmp", root / "TOL" / (name + ".dmp")
        )
        for name in ["names", "nodes", "merged"]
    }
    for name, path in paths.items():
        if sha256(path) != first["sources"]["ncbi_" + name]["sha256"]:
            raise ValueError("Registered NCBI snapshot differs from original identity links")
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    baseline = pd.read_parquet(args.baseline_dir / "species_tree_links.parquet")
    cw = pd.read_parquet(prior["sources"]["crosswalk"]["path"])
    with duckdb.connect() as c:
        fresh = (
            c.execute(
                "SELECT * FROM read_csv(?,all_varchar=true)",
                [prior["sources"]["iucn_taxonomy"]["path"]],
            )
            .fetchdf()
            .astype(object)
            .where(lambda x: x.notna(), None)
        )
    remaining = baseline[~baseline.tree_identity_verified]
    names = set(remaining.iucn_scientific_name.map(normalize_name))
    print(f"Investigating documented names for {len(remaining):,} remaining species", flush=True)
    merges = load_ncbi_merges(paths["merged"])
    ncbi = ncbi_names_for_iucn(paths["names"], names, merges)
    ranks = current_ncbi_ranks(paths["nodes"], set(ncbi.ncbi_taxid))
    ncbi = ncbi[ncbi.ncbi_taxid.map(ranks).eq("species")]
    ncbi.to_parquet(output / "current_ncbi_name_evidence.parquet", index=False)
    tips = Path(prior["sources"]["tree_tips"]["path"])
    taxonomy = args.input_dir / "ott_index.parquet"
    stamp = json.loads(taxonomy.with_suffix(".receipt.json").read_text())
    if (
        stamp["input"]["archive_sha256"] != prior["sources"]["ott-taxonomy"]["sha256"]
        or sha256(taxonomy) != stamp["output_sha256"]
    ):
        raise ValueError("OTT taxonomy index changed")
    synonyms = build_synonym_index(
        Path(prior["sources"]["ott-taxonomy"]["path"]), args.input_dir / "ott_synonyms.parquet"
    )
    with duckdb.connect() as c:
        c.register("wanted", pd.DataFrame({"normalized_name": sorted(names)}))
        aliases = c.execute(
            "SELECT s.* FROM read_parquet(?) s JOIN wanted USING(normalized_name)", [str(synonyms)]
        ).fetchdf()
        all_targets = c.execute(
            """
            SELECT w.normalized_name,x.ott_id,x.taxon_rank,'accepted_name' AS basis
            FROM wanted w JOIN read_parquet(?) x
              ON w.normalized_name=lower(regexp_replace(trim(x.taxon_name),'\\s+',' ','g'))
            UNION
            SELECT a.normalized_name,x.ott_id,x.taxon_rank,'ott_alias' AS basis
            FROM read_parquet(?) a JOIN wanted USING(normalized_name)
              JOIN read_parquet(?) x USING(ott_id)
        """,
            [str(taxonomy), str(synonyms), str(taxonomy)],
        ).fetchdf()
        c.register("aliases", aliases)
        aliased = c.execute(
            """SELECT a.normalized_name,t.tree_tip,t.ott_id,t.label_occurrences,a.alias_sourceinfo,
                         'ott_synonym' AS identity_basis FROM aliases a
                     JOIN read_parquet(?) t USING(ott_id) JOIN read_parquet(?) x USING(ott_id)
                     WHERE x.taxon_rank='species' """,
            [str(tips), str(taxonomy)],
        ).fetchdf()
    aliases.to_parquet(output / "ott_alias_evidence.parquet", index=False)
    all_targets.to_parquet(output / "ott_name_target_evidence.parquet", index=False)
    name_targets = (
        all_targets[all_targets.taxon_rank.eq("species")]
        .groupby("normalized_name")
        .ott_id.agg(set)
        .to_dict()
    )
    exact = exact_name_candidates(tips, taxonomy, {"remaining": names})
    if not exact.empty:
        exact = tip_taxonomy(exact, taxonomy)[
            ["normalized_name", "tree_tip", "ott_id", "label_occurrences"]
        ]
    exact["identity_basis"] = "exact_ott_name"
    exact["alias_sourceinfo"] = ""
    identifiers, _ = direct_identifiers(tips, taxonomy, set(ncbi.ncbi_taxid), merges)
    ncbi_records = [
        dict(
            normalized_name=r.normalized_name,
            tree_tip=tip,
            identity_basis="current_ncbi_name",
            alias_sourceinfo="ncbi:" + r.ncbi_taxid,
        )
        for r in ncbi.itertuples()
        for tip in sorted(identifiers.get(r.ncbi_taxid, []))
    ]
    ncbi_tips = pd.DataFrame(
        ncbi_records, columns=["normalized_name", "tree_tip", "identity_basis", "alias_sourceinfo"]
    )
    evidence = pd.concat([exact, aliased, ncbi_tips], ignore_index=True).drop_duplicates()
    evidence.to_parquet(output / "candidate_name_evidence.parquet", index=False)
    with duckdb.connect() as c:
        c.register("wanted", evidence[["tree_tip"]].drop_duplicates())
        leaves = c.execute(
            "SELECT t.* FROM read_parquet(?) t JOIN wanted USING(tree_tip)", [str(tips)]
        ).fetchdf()
    metadata = (
        tip_taxonomy(leaves, taxonomy, include_ancestor_names=True)
        if not leaves.empty
        else pd.DataFrame()
    )
    candidates = prepare_candidates(evidence, metadata, fresh, ncbi, merges, name_targets)
    candidates.to_parquet(output / "name_candidate_taxonomy.parquet", index=False)
    expanded, audit = extend_synonym_links(baseline, cw, fresh, candidates)
    audit.loc[audit.reason.eq("no_exact_species_leaf"), "reason"] = "no_documented_species_leaf"
    audit.to_parquet(output / "name_change_audit.parquet", index=False)
    leaf_columns = [
        "ott_id",
        "terminal_myr",
        "ott_name",
        "ott_rank",
        "ott_flags",
        "ott_sourceinfo",
        "label_occurrences",
    ]
    expanded = expanded.drop(columns=leaf_columns)
    accepted = expanded.loc[expanded.tree_identity_verified, ["tree_tip"]]
    with duckdb.connect() as c:
        c.register("accepted", accepted)
        attributes = c.execute(
            """SELECT t.tree_tip,t.ott_id,t.terminal_myr,t.label_occurrences,x.taxon_name AS ott_name,
                x.taxon_rank AS ott_rank,x.flags AS ott_flags,x.sourceinfo AS ott_sourceinfo
            FROM read_parquet(?) t JOIN accepted USING(tree_tip) JOIN read_parquet(?) x USING(ott_id)""",
            [str(tips), str(taxonomy)],
        ).fetchdf()
    expanded = expanded.merge(attributes, on="tree_tip", how="left", validate="many_to_one")
    verified = expanded[expanded.tree_identity_verified]
    assert (
        verified.tree_tip.is_unique
        and verified.ott_rank.eq("species").all()
        and verified.label_occurrences.eq(1).all()
    )
    expanded.to_parquet(output / "species_tree_links.parquet", index=False)
    accepted_sis = set(audit.loc[audit.accepted, "iucn_sis_id"])
    review = (
        audit[~audit.iucn_sis_id.isin(accepted_sis)]
        .groupby("iucn_sis_id", as_index=False)
        .agg(
            reasons=("reason", lambda x: ",".join(sorted(set(x)))),
            candidate_tips=("tree_tip", lambda x: sorted(set(x.dropna()))),
            conflicting_ranks=(
                "conflicting_ranks",
                lambda x: ",".join(sorted(set(x.dropna()) - {""})),
            ),
        )
        .merge(
            remaining[["iucn_sis_id", "iucn_scientific_name", "iucn_class"]],
            on="iucn_sis_id",
            validate="one_to_one",
        )
    )
    # Distinguish a name missing from the dated tree from a name missing from
    # OTT altogether. These are recorded-name checks, not biological absence.
    review["normalized_name"] = review.iucn_scientific_name.map(normalize_name)
    with duckdb.connect() as c:
        c.register("review", review[["iucn_sis_id", "normalized_name"]])
        availability = c.execute(
            """
            SELECT r.iucn_sis_id,x.ott_id,x.taxon_rank,'accepted_name' AS basis
            FROM review r JOIN read_parquet(?) x
              ON r.normalized_name=lower(regexp_replace(trim(x.taxon_name),'\\s+',' ','g'))
            UNION
            SELECT r.iucn_sis_id,x.ott_id,x.taxon_rank,'ott_alias' AS basis
            FROM review r JOIN read_parquet(?) a USING(normalized_name)
              JOIN read_parquet(?) x USING(ott_id)
        """,
            [str(taxonomy), str(synonyms), str(taxonomy)],
        ).fetchdf()
    availability.to_parquet(output / "remaining_ott_name_availability.parquet", index=False)
    known = set(availability.iucn_sis_id)
    species_known = set(availability.loc[availability.taxon_rank.eq("species"), "iucn_sis_id"])
    review["recorded_name_gap"] = None
    missing_leaf = review.reasons.eq("no_documented_species_leaf")
    review.loc[missing_leaf, "recorded_name_gap"] = "no_recorded_ott_name"
    review.loc[missing_leaf & review.iucn_sis_id.isin(known), "recorded_name_gap"] = (
        "ott_name_only_at_nonspecies_rank"
    )
    review.loc[missing_leaf & review.iucn_sis_id.isin(species_known), "recorded_name_gap"] = (
        "ott_species_name_without_dated_leaf"
    )
    review["current_ncbi_name_found"] = review.normalized_name.isin(set(ncbi.normalized_name))
    review.to_parquet(output / "remaining_review.parquet", index=False)
    by_class = (
        expanded.assign(new_link=expanded.join_method.eq("documented_binomial_name_change"))
        .groupby("iucn_class", dropna=False, as_index=False)
        .agg(
            assessments=("iucn_sis_id", "size"),
            linked=("tree_identity_verified", "sum"),
            new_links=("new_link", "sum"),
        )
    )
    by_class.to_parquet(output / "coverage_by_class.parquet", index=False)
    new_audit = audit[audit.accepted]
    report = dict(
        schema_version=1,
        created_at=iso_now(),
        assessments=len(expanded),
        baseline_linked=prior["linked"],
        linked=len(verified),
        new_links=len(new_audit),
        remaining=len(review),
        new_links_with_documented_genus_change=int(new_audit.documented_genus_change.sum()),
        ott_alias_species_names_on_tree=int(aliased.normalized_name.nunique()),
        ncbi_name_records=len(ncbi),
        remaining_reason_counts=review.reasons.value_counts().to_dict(),
        no_documented_leaf_name_gap_counts=review.recorded_name_gap.dropna()
        .value_counts()
        .to_dict(),
        audit_reason_counts=audit.reason.value_counts().to_dict(),
        coverage_by_class=by_class.to_dict("records"),
        sources={
            **prior["sources"],
            "expanded_baseline_report": receipt(prior_path),
            "expanded_baseline_species": receipt(args.baseline_dir / "species_tree_links.parquet"),
            "ott_synonym_index": receipt(synonyms),
            "ott_taxonomy_index": receipt(taxonomy),
            **{"ncbi_" + name: receipt(path) for name, path in paths.items()},
        },
        code={
            p.name: receipt(p)
            for p in [
                Path(__file__),
                Path(__file__).parents[1] / "edge.py",
                Path("ark_pipeline/edge_synonyms.py"),
                Path("ark_pipeline/edge_link_extension.py"),
                Path("ark_pipeline/edge_matching.py"),
                Path("ark_pipeline/edge_links.py"),
            ]
        },
        limitations=[
            "Only documented binomial name changes with unchanged specific epithet are eligible automatically. Different-epithet synonyms require species-concept review; OTT alias types are unspecified.",
            "Genus changes require explicit OTT alias or current NCBI name/identifier evidence, matching family, order and class, and no other comparable lineage conflict. This does not assert that the genera are synonyms.",
            "Aliases targeting a different assessed IUCN species, contradictory current NCBI identities, ambiguous names, reserved tips and competing IUCN proposals are withheld.",
            "All previous links and all genomic-transfer/NCBI-verification flags are preserved. New links do not authorize new genomic transfers.",
            "No tree modification, fuzzy matching, global scoring or official EDGE rank certification. A lack of documented names is not proof of biological absence.",
        ],
    )
    report["outputs"] = {p.name: receipt(p) for p in sorted(output.glob("*.parquet"))}
    atomic_json(output / "report.json", report)
    text = [
        "# Documented name-change investigation",
        "",
        f"Checked identity coverage: **{len(verified):,} / {len(expanded):,} ({len(verified) / len(expanded):.1%})**; **{len(new_audit):,} additional links**.",
        f"{int(new_audit.documented_genus_change.sum()):,} additional links use a documented genus change with family/order/class agreement.",
        "",
        "## Remaining species",
        "",
        *[f"- {k}: {v:,}" for k, v in report["remaining_reason_counts"].items()],
        "",
        "### Names without a documented dated-tree leaf",
        "",
        *[f"- {k}: {v:,}" for k, v in report["no_documented_leaf_name_gap_counts"].items()],
        "",
        "## Limits",
        "",
        *["- " + x for x in report["limitations"]],
    ]
    (output / "report.md").write_text("\n".join(text) + "\n")
    print(
        json.dumps(
            {
                k: report[k]
                for k in [
                    "baseline_linked",
                    "linked",
                    "new_links",
                    "remaining",
                    "new_links_with_documented_genus_change",
                    "remaining_reason_counts",
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
    parser.add_argument("--input-dir", type=Path, default=Path("data/edge/inputs"))
    parser.add_argument("--baseline-dir", type=Path, default=Path("data/edge/links-expanded"))
    parser.add_argument("--output-dir", type=Path, default=Path("data/edge/links-synonyms"))
    run(parser.parse_args())


if __name__ == "__main__":
    main()
