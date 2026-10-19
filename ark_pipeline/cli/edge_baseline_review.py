"""Audit the mammal pilot's missing DNA-positive tree identities."""

import argparse
import json
import re
import shutil
from pathlib import Path

import duckdb
import pandas as pd

from ark_pipeline.cli.edge_prototype import receipt
from ark_pipeline.edge import (
    assessment_join,
    canonical_taxid,
    load_clade,
    load_ncbi_merges,
    normalize_category,
)
from ark_pipeline.edge_baseline_review import review_baseline_links
from ark_pipeline.edge_links import current_ncbi_ranks, direct_identifiers
from ark_pipeline.edge_matching import (
    current_species_lineages,
    current_species_names,
    normalize_name,
    tip_taxonomy,
)
from ark_pipeline.edge_synonyms import ncbi_names_for_iucn
from ark_pipeline.runtime.provenance import atomic_json, iso_now, sha256


def run(args):
    report_path = args.pilot_dir / "report.json"
    pilot = json.loads(report_path.read_text())
    audit_path = args.pilot_dir / "goat_mammal_tree_audit.parquet"
    if "baseline_review" in pilot["sources"]:
        previous_path = Path(pilot["sources"]["baseline_review"]["path"])
        if sha256(previous_path) != pilot["sources"]["baseline_review"]["sha256"]:
            raise ValueError("Previous baseline review changed")
        previous = json.loads(previous_path.read_text())
        report_record = previous["sources"]["input_pilot_report"]
        audit_record = previous["sources"]["input_pilot_audit"]
        report_path, audit_path = Path(report_record["path"]), Path(audit_record["path"])
        if (
            sha256(report_path) != report_record["sha256"]
            or sha256(audit_path) != audit_record["sha256"]
        ):
            raise ValueError("Original baseline review inputs changed")
        pilot = json.loads(report_path.read_text())
    source_keys = [
        "goat_species",
        "ncbi_names",
        "ncbi_nodes",
        "ncbi_merged",
        "dated-tree",
        "ott-taxonomy",
        "ott_index",
        "global_tree_tips",
        "crosswalk",
        "iucn_assessments",
    ]
    sources = {key: pilot["sources"][key] for key in source_keys}
    for key, source in sources.items():
        if sha256(Path(source["path"])) != source["sha256"]:
            raise ValueError("Review source checksum changed: " + key)
    if sha256(audit_path) != pilot["outputs"][audit_path.name]["sha256"]:
        raise ValueError("Pilot audit changed")
    synonyms = args.input_dir / "ott_synonyms.parquet"
    stamp = json.loads(synonyms.with_suffix(".receipt.json").read_text())
    if (
        stamp["input"]["archive_sha256"] != sources["ott-taxonomy"]["sha256"]
        or sha256(synonyms) != stamp["output_sha256"]
    ):
        raise ValueError("OTT synonym index changed")
    sources["ott_synonym_index"] = receipt(synonyms)
    baseline = pd.read_parquet(audit_path)
    missing = baseline[baseline.legacy_broad_evidence & baseline.tree_join_status.ne("linked")]
    taxids = set(missing.canonical_ncbi_taxid)
    merges = load_ncbi_merges(Path(sources["ncbi_merged"]["path"]))
    names = current_species_names(Path(sources["ncbi_names"]["path"]), taxids, merges)
    ranks = current_ncbi_ranks(Path(sources["ncbi_nodes"]["path"]), taxids)
    lineages = current_species_lineages(
        Path(sources["ncbi_nodes"]["path"]), Path(sources["ncbi_names"]["path"]), taxids
    )
    wanted = pd.DataFrame(
        [
            dict(subject_ncbi_taxid=r.canonical_ncbi_taxid, normalized_name=name)
            for r in missing.itertuples()
            for name in sorted(
                names.get(r.canonical_ncbi_taxid, set()) | {normalize_name(r.scientific_name)}
            )
        ], columns=["subject_ncbi_taxid", "normalized_name"], dtype="str"
    ).drop_duplicates()
    ncbi_evidence = ncbi_names_for_iucn(
        Path(sources["ncbi_names"]["path"]), set(wanted.normalized_name), merges
    )
    name_ranks = current_ncbi_ranks(
        Path(sources["ncbi_nodes"]["path"]), set(ncbi_evidence.ncbi_taxid)
    )
    ncbi_evidence = ncbi_evidence[ncbi_evidence.ncbi_taxid.map(name_ranks).eq("species")]
    name_ids = ncbi_evidence.groupby("normalized_name").ncbi_taxid.agg(set).to_dict()
    iucn_rows = assessment_join(
        Path(sources["crosswalk"]["path"]), Path(sources["iucn_assessments"]["path"])
    )
    iucn_groups = {}
    for row in iucn_rows:
        if (
            row["safe_for_automatic_species_trait_transfer"] is True
            and str(row["assessment_sis_id"]) == str(row["iucn_sis_id"])
            and re.search(r"\bGlobal\b", row["scopes"] or "", re.I)
            and "subpopulation" not in row["iucn_scientific_name"].lower()
            and row["matched_ncbi_species_taxid"]
        ):
            taxid = canonical_taxid(str(row["matched_ncbi_species_taxid"]), merges)
            if taxid in taxids:
                iucn_groups.setdefault(taxid, []).append(row)
    iucn_identities = {
        taxid: {
            "iucn_sis_id": rows[0]["iucn_sis_id"],
            "iucn_assessment_id": str(rows[0]["iucn_assessment_id"]),
            "iucn_scientific_name": rows[0]["iucn_scientific_name"],
            "iucn_category": normalize_category(rows[0]["redlistCategory"]),
        }
        for taxid, rows in iucn_groups.items()
        if len(rows) == 1 and rows[0]["redlistCategory"]
    }
    print(
        f"Reviewing {len(missing)} DNA-positive baseline records against names, lineage and tree structure",
        flush=True,
    )
    with duckdb.connect() as c:
        c.execute("SET memory_limit='1GB'")
        c.register("wanted", wanted)
        targets = c.execute(
            """
            SELECT w.*,x.ott_id,x.taxon_name,x.taxon_rank,x.sourceinfo,x.flags,'exact_ott_name' AS identity_basis
            FROM wanted w JOIN read_parquet(?) x
              ON w.normalized_name=lower(regexp_replace(trim(x.taxon_name),'\\s+',' ','g'))
            UNION
            SELECT w.*,x.ott_id,x.taxon_name,x.taxon_rank,x.sourceinfo,x.flags,'ott_alias' AS identity_basis
            FROM wanted w JOIN read_parquet(?) z USING(normalized_name)
              JOIN read_parquet(?) x USING(ott_id)
        """,
            [sources["ott_index"]["path"], str(synonyms), sources["ott_index"]["path"]],
        ).fetchdf()
        c.register("targets", targets)
        evidence = c.execute(
            """
            SELECT t.subject_ncbi_taxid,t.normalized_name,t.identity_basis,p.*
            FROM targets t JOIN read_parquet(?) p USING(ott_id)
        """,
            [sources["global_tree_tips"]["path"]],
        ).fetchdf()
    leaf_meta = (
        tip_taxonomy(
            evidence[["tree_tip", "ott_id", "label_occurrences"]].drop_duplicates(),
            Path(sources["ott_index"]["path"]),
            include_ancestor_names=True,
        )
        if not evidence.empty else pd.DataFrame(columns=["tree_tip"])
    )
    groups = evidence.groupby(["subject_ncbi_taxid", "tree_tip"], as_index=False).agg(
        identity_basis=("identity_basis", lambda v: ",".join(sorted(set(v)))),
        queried_names=("normalized_name", lambda v: ";".join(sorted(set(v)))),
    )
    candidates = groups.merge(leaf_meta, on="tree_tip", validate="many_to_one")
    direct, repeated = direct_identifiers(
        Path(sources["global_tree_tips"]["path"]),
        Path(sources["ott_index"]["path"]),
        taxids,
        merges,
    )
    tree = load_clade(Path(sources["dated-tree"]["path"]), pilot["clade"])
    internal_ids = {
        m[1] for n in tree.traverse() if not n.is_leaf and (m := re.search(r"_ott(\d+)$", n.name))
    }
    reviewed, review, candidate_audit = review_baseline_links(
        baseline,
        candidates,
        targets,
        names,
        ranks,
        lineages,
        direct,
        repeated,
        merges,
        {n.name for n in tree.leaves()},
        internal_ids,
        ncbi_name_ids=name_ids,
        iucn_identities=iucn_identities,
    )
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    input_dir = output / "input"
    input_dir.mkdir(exist_ok=True)
    if report_path.resolve() != (input_dir / "report.json").resolve():
        shutil.copyfile(report_path, input_dir / "report.json")
    if audit_path.resolve() != (input_dir / audit_path.name).resolve():
        shutil.copyfile(audit_path, input_dir / audit_path.name)
    reviewed.to_parquet(output / "reviewed_baseline_links.parquet", index=False)
    review.to_parquet(output / "baseline_identity_review.parquet", index=False)
    review[review.ott_extinction_overridden].to_parquet(
        output / "extinction_status_overrides.parquet", index=False
    )
    candidate_audit.to_parquet(output / "candidate_review.parquet", index=False)
    targets.to_parquet(output / "ott_name_targets.parquet", index=False)
    candidates.to_parquet(output / "documented_leaf_candidates.parquet", index=False)
    ncbi_evidence.to_parquet(output / "current_ncbi_name_evidence.parquet", index=False)
    sources["input_pilot_report"] = receipt(input_dir / "report.json")
    sources["input_pilot_audit"] = receipt(input_dir / audit_path.name)
    direct_rows = [
        dict(canonical_ncbi_taxid=k, tree_tip=tip)
        for k, tips in direct.items()
        for tip in sorted(tips)
    ]
    pd.DataFrame(direct_rows, columns=["canonical_ncbi_taxid", "tree_tip"]).to_parquet(
        output / "direct_identifier_candidates.parquet", index=False
    )
    report = dict(
        schema_version=1,
        created_at=iso_now(),
        reviewed=len(review),
        recovered=int(review.recovered.sum()),
        unresolved=int((~review.recovered).sum()),
        clade=pilot["clade"],
        reason_counts=review.review_reason.value_counts().to_dict(),
        extinction_status_policy="verified_iucn_overrides_ott_extinction_flags",
        iucn_extinction_overrides=int(review.ott_extinction_overridden.sum()),
        source_hashes={k: v["sha256"] for k, v in sources.items() if k in source_keys},
        sources=sources,
        code={
            str(p.resolve().relative_to(Path.cwd())): receipt(p)
            for p in [
                Path(__file__),
                Path("ark_pipeline/edge_baseline_review.py"),
                Path("ark_pipeline/edge_matching.py"),
                Path("ark_pipeline/edge_synonyms.py"),
                Path("ark_pipeline/edge_links.py"),
                Path("ark_pipeline/extinction_status.py"),
                Path("ark_pipeline/edge.py"),
            ]
        },
        outputs={p.name: receipt(p) for p in sorted(output.glob("*.parquet"))},
        limitations=[
            "Website DNA criteria, source tree, earlier IUCN identities and genomic-transfer flags remain unchanged.",
            "Current NCBI species identity and lineage, unique species leaf, documented names and absence of conflicting source IDs are required for recovery.",
            "Multiple leaves for a merged current NCBI concept remain unresolved; preferred names alone do not establish which dated branch represents the sample.",
            "Named internal tree nodes, subspecies, hybrids and unspecified taxa are audited without grafting or copying a relative's tip.",
            "Absence of a documented source name does not prove biological absence. Missing current-NCBI identities require source reconciliation.",
            "Verified IUCN assessments overrule conflicting OTT extinction flags. Raw flags and assessment IDs are preserved; absent dated leaves remain coverage gaps with no invented branch length.",
        ],
    )
    atomic_json(output / "report.json", report)
    text = [
        "# Mammal DNA baseline identity review",
        "",
        f"Reviewed {len(review)} missing DNA-positive baseline identities: {report['recovered']} recovered, {report['unresolved']} unresolved.",
        "",
        "| Outcome | Records |",
        "|---|---:|",
    ]
    text += [f"| {reason} | {count} |" for reason, count in report["reason_counts"].items()]
    text += ["", "## Recovered", ""] + [
        f"- {r.scientific_name}: {r.tree_tip}" for r in review[review.recovered].itertuples()
    ]
    text += ["", "## Limits", ""] + ["- " + v for v in report["limitations"]]
    text += [
        "",
        "## IUCN extinction-status overrides",
        "",
        "IUCN is authoritative for verified assessed species. These overrides correct status; a missing dated leaf remains a coverage gap.",
        "",
        "| Species | IUCN | Raw OTT flags | Tree leaf |",
        "|---|---|---|---|",
    ]
    text += [
        f"| {r.scientific_name} | {r.iucn_category} | {r.ott_extinction_flags_evidence} | Missing |"
        for r in review[review.ott_extinction_overridden].itertuples()
    ]
    (output / "report.md").write_text("\n".join(text) + "\n")
    print(
        json.dumps(
            {k: report[k] for k in ["reviewed", "recovered", "unresolved", "reason_counts"]},
            indent=2,
        )
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pilot-dir", type=Path, default=Path("data/edge/phylogenetic-gap"))
    parser.add_argument("--input-dir", type=Path, default=Path("data/edge/inputs"))
    parser.add_argument("--output-dir", type=Path, default=Path("data/edge/baseline-review"))
    run(parser.parse_args())


if __name__ == "__main__":
    main()
