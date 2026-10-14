"""Compare mammal tree gaps with Ark's existing website DNA scoring rule."""

from __future__ import annotations

import argparse
import json
import os
import re
import tomllib
from pathlib import Path

import duckdb
import pandas as pd

from ark_pipeline.builders.coarse_cache import DEFAULT_WEIGHTS
from ark_pipeline.cli.edge_prototype import receipt
from ark_pipeline.cli.serving_metadata import source_file, validate_crosswalk_sources
from ark_pipeline.cli.sources_acquire import load_manifest
from ark_pipeline.edge import acquire_inputs, canonical_taxid, load_clade, load_ncbi_merges
from ark_pipeline.edge_baseline_review import apply_reviewed_baseline
from ark_pipeline.edge_links import current_ncbi_ranks, direct_identifiers
from ark_pipeline.edge_matching import normalize_name
from ark_pipeline.extinction_status import extinction_status
from ark_pipeline.phylogenetic_gap import genome_evidence, greedy_targets, marginal_gaps, rooted_pd
from ark_pipeline.runtime.provenance import atomic_json, iso_now, sha256


def comparison(frame, old, new, k=100):
    common = frame[
        frame[old].notna() & frame[new].notna() & frame.redlist_category.isin(["CR", "EN", "VU"])
    ].copy()
    if common.empty:
        return {"species": 0}
    first = common.sort_values([old, "iucn_sis_id"], ascending=[False, True]).head(k)
    second = common.sort_values([new, "iucn_sis_id"], ascending=[False, True]).head(k)
    boundary = first[old].iloc[-1]
    return dict(
        species=len(common),
        budget=min(k, len(common)),
        top_overlap=len(set(first.iucn_sis_id) & set(second.iucn_sis_id)),
        rank_correlation=(
            float(common[old].rank().corr(common[new].rank()))
            if len(common) > 1 and common[old].nunique() > 1 and common[new].nunique() > 1
            else None
        ),
        legacy_cutoff_score=float(boundary),
        legacy_at_cutoff=int(common[old].eq(boundary).sum()),
        legacy_at_or_above_cutoff=int(common[old].ge(boundary).sum()),
        tie_break="ascending IUCN SIS ID for fixed-budget diagnostics only",
    )


def run(args):
    root = args.root.expanduser().resolve()
    registry = load_manifest(root)
    sources = {}
    for key, source_id, logical, fallback in [
        (
            "goat_species",
            "goat-species",
            "tol_species_all_ranks.tsv",
            "TOL/tol_species_all_ranks.tsv",
        ),
        ("ncbi_names", "ncbi-taxonomy", "names.dmp", "TOL/names.dmp"),
        ("ncbi_nodes", "ncbi-taxonomy", "nodes.dmp", "TOL/nodes.dmp"),
        ("ncbi_merged", "ncbi-taxonomy", "merged.dmp", "TOL/merged.dmp"),
        ("iucn_taxonomy", "iucn-red-list-tabular", "taxonomy.csv", "IUCN_Red_List/taxonomy.csv"),
        (
            "iucn_assessments",
            "iucn-red-list-tabular",
            "assessments.csv",
            "IUCN_Red_List/assessments.csv",
        ),
    ]:
        sources[key] = receipt(source_file(root, registry, source_id, logical, root / fallback))
    validate_crosswalk_sources(args.crosswalk, registry)
    summary_path = args.crosswalk.with_name("match_summary.json")
    summary = json.loads(summary_path.read_text())
    for key, entry in summary["sources"].items():
        if key in sources and entry["sha256"] != sources[key]["sha256"]:
            raise ValueError("Crosswalk source mismatch: " + key)
    cw_receipt = json.loads(args.crosswalk.with_name("receipt.json").read_text())
    if sha256(args.crosswalk) != cw_receipt["outputs"][args.crosswalk.name]["sha256"]:
        raise ValueError("Reviewed crosswalk output checksum changed")
    before = json.loads((args.links_dir / "report.json").read_text())
    links_path = args.links_dir / "species_tree_links.parquet"
    if sha256(links_path) != before["outputs"][links_path.name]["sha256"]:
        raise ValueError("Tree identity artifact changed")
    for key, source in [("iucn_taxonomy", "iucn_taxonomy"), ("iucn_assessments", "assessments")]:
        if sources[key]["sha256"] != before["sources"][source]["sha256"]:
            raise ValueError("IUCN snapshot differs from checked tree identities")
    config = tomllib.loads(args.config.read_text())
    acquired = acquire_inputs(
        {"sources": [s for s in config["sources"] if s["id"] in {"dated-tree", "ott-taxonomy"}]},
        args.input_dir,
        False,
    )
    for key in acquired:
        if acquired[key]["sha256"] != before["sources"][key]["sha256"]:
            raise ValueError("Tree/taxonomy differs from identity links")
    sources.update(acquired)
    tips = Path(before["sources"]["tree_tips"]["path"])
    if sha256(tips) != before["sources"]["tree_tips"]["sha256"]:
        raise ValueError("Global tip index changed")
    ott = args.input_dir / "ott_index.parquet"
    stamp = json.loads(ott.with_suffix(".receipt.json").read_text())
    if (
        sha256(ott) != stamp["output_sha256"]
        or stamp["input"]["archive_sha256"] != acquired["ott-taxonomy"]["sha256"]
    ):
        raise ValueError("OTT index changed")
    print("Reading the verified mammal crown and current GoaT species evidence", flush=True)
    tree = load_clade(Path(acquired["dated-tree"]["path"]), config["clade"])
    leaf_names = {n.name for n in tree.leaves()}
    with duckdb.connect() as c:
        goat = c.execute(
            "SELECT * FROM read_csv(?,delim='\t',header=true,all_varchar=true,sample_size=-1) WHERE lower(class)='mammalia' AND lower(taxon_rank)='species'",
            [sources["goat_species"]["path"]],
        ).fetchdf()
    evidence = genome_evidence(goat)
    merges = load_ncbi_merges(Path(sources["ncbi_merged"]["path"]))
    evidence["canonical_ncbi_taxid"] = evidence.taxon_id.map(lambda x: canonical_taxid(x, merges))
    duplicate_ids = set(
        evidence.loc[evidence.canonical_ncbi_taxid.duplicated(keep=False), "canonical_ncbi_taxid"]
    )
    ranks = current_ncbi_ranks(
        Path(sources["ncbi_nodes"]["path"]), set(evidence.canonical_ncbi_taxid)
    )
    mapping, repeated = direct_identifiers(tips, ott, set(evidence.canonical_ncbi_taxid), merges)
    direct = {
        taxid: next(iter(names))
        for taxid, names in mapping.items()
        if len(names) == 1 and taxid not in repeated and next(iter(names)) in leaf_names
    }
    cw = pd.read_parquet(args.crosswalk)
    links = pd.read_parquet(links_path)
    mammals = links[links.iucn_class.eq("MAMMALIA")].copy()
    if not mammals.iucn_sis_id.is_unique or not cw.iucn_sis_id.is_unique:
        raise ValueError("Species IDs not unique")
    fresh = cw.set_index("iucn_sis_id").to_dict("index")
    raw = (
        evidence[~evidence.canonical_ncbi_taxid.isin(duplicate_ids)]
        .set_index("canonical_ncbi_taxid")
        .to_dict("index")
    )
    baselines = []
    for row in evidence.to_dict("records"):
        taxid = row["canonical_ncbi_taxid"]
        reason = "ncbi_not_in_mammal_tree"
        if taxid in duplicate_ids:
            reason = "duplicate_current_goat_identity"
        elif ranks.get(taxid) != "species":
            reason = "not_current_ncbi_species"
        elif taxid in repeated or len(mapping.get(taxid, set())) > 1:
            reason = "ambiguous_tree_identifier"
        elif taxid in direct:
            reason = "linked"
        baselines.append(
            {
                **row,
                "tree_tip": direct.get(taxid) if reason == "linked" else None,
                "tree_join_status": reason,
                "tree_join_route": "ncbi_source_identifier" if reason == "linked" else None,
            }
        )
    baseline = pd.DataFrame(baselines)
    baseline_tips = set(
        baseline.loc[
            baseline.legacy_broad_evidence & baseline.tree_join_status.eq("linked"), "tree_tip"
        ]
    )
    rows = []
    for row in mammals.to_dict("records"):
        sis = row["iucn_sis_id"]
        match = fresh.get(sis, {})
        taxid = canonical_taxid(str(match.get("matched_ncbi_species_taxid") or ""), merges)
        source = raw.get(taxid)
        reason = "linked"
        if not row["tree_identity_verified"]:
            reason = "no_verified_tree_link"
        elif normalize_name(match.get("iucn_scientific_name")) != normalize_name(
            row["iucn_scientific_name"]
        ) or str(match.get("iucn_assessment_id")) != str(row["iucn_assessment_id"]):
            reason = "assessment_identity_conflict"
        elif not match.get("safe_for_automatic_species_trait_transfer", False):
            reason = "unsafe_current_goat_match"
        elif source is None:
            reason = "no_unique_current_goat_species"
        elif ranks.get(taxid) != "species":
            reason = "not_current_ncbi_species"
        elif taxid in repeated or len(mapping.get(taxid, set())) > 1:
            reason = "ambiguous_current_ncbi_tree_identity"
        elif mapping.get(taxid) and mapping[taxid] != {row["tree_tip"]}:
            reason = "current_ncbi_tree_identity_conflict"
        usable = reason == "linked"
        own_dna = bool(source["legacy_broad_evidence"]) if usable else None
        route = (
            "ncbi_source_identifier"
            if direct.get(taxid) == row["tree_tip"]
            else "reviewed_iucn_species_bridge"
        )
        if usable and own_dna:
            baseline_tips.add(row["tree_tip"])
            # Keep a witness for additional current-IUCN bridge baseline tips.
            if row["tree_tip"] not in set(
                baseline.loc[baseline.tree_join_status.eq("linked"), "tree_tip"]
            ):
                witness = next(b for b in baselines if b["canonical_ncbi_taxid"] == taxid)
                witness.update(
                    tree_tip=row["tree_tip"],
                    tree_join_status="linked",
                    tree_join_route=route,
                    bridge_iucn_sis_id=sis,
                )
        category = row["redlist_category"]
        threat = DEFAULT_WEIGHTS.get(category.lower())
        rows.append(
            {
                **row,
                "current_goat_taxid": taxid or None,
                "current_goat_join_status": reason,
                "current_goat_tree_route": route if usable else None,
                "website_species_dna": own_dna,
                "sample_reported": source["sample_reported"] if usable else None,
                "project_reported": source["project_reported"] if usable else None,
                "qualifying_genome_proxy": source["qualifying_genome_proxy"] if usable else None,
                "resampling_reported": source["resampling_reported"] if usable else None,
                "threat_weight": threat,
                "gap_score_eligible": usable and category != "EX" and threat is not None,
            }
        )
    baseline = pd.DataFrame(baselines)
    review_path = args.baseline_review
    if review_path is None:
        cached_review = Path("data/edge/baseline-review/report.json")
        if cached_review.is_file():
            review_path = cached_review
    review_recovered = 0
    identity_review = None
    if review_path is not None:
        review_report = json.loads(review_path.read_text())
        current_hashes = {key: value["sha256"] for key, value in sources.items()}
        current_hashes.update(
            ott_index=sha256(ott), global_tree_tips=sha256(tips), crosswalk=sha256(args.crosswalk)
        )
        if review_report["clade"] != config["clade"] or any(
            current_hashes.get(key) != value
            for key, value in review_report["source_hashes"].items()
        ):
            raise ValueError("Baseline review uses different current sources")
        required_code = {
            "ark_pipeline/cli/edge_baseline_review.py",
            "ark_pipeline/edge_baseline_review.py",
            "ark_pipeline/edge_matching.py",
            "ark_pipeline/edge_synonyms.py",
            "ark_pipeline/edge_links.py",
            "ark_pipeline/extinction_status.py",
            "ark_pipeline/edge.py",
        }
        if review_report.get("schema_version") != 1 or not required_code.issubset(
            review_report.get("code", {})
        ):
            raise ValueError("Baseline review lacks required code provenance; rerun review")
        for name in required_code:
            current_code = Path(__file__).resolve().parents[2] / name
            if sha256(current_code) != review_report["code"][name]["sha256"]:
                raise ValueError("Baseline review code changed; rerun review")
        for code in review_report["code"].values():
            if sha256(Path(code["path"])) != code["sha256"]:
                raise ValueError("Baseline review code changed; rerun review")
        review_directory = review_path.parent
        for name in ["reviewed_baseline_links.parquet", "baseline_identity_review.parquet"]:
            if sha256(review_directory / name) != review_report["outputs"][name]["sha256"]:
                raise ValueError("Baseline review output changed")
        identity_review = pd.read_parquet(review_directory / "baseline_identity_review.parquet")
        baseline = apply_reviewed_baseline(
            baseline,
            pd.read_parquet(review_directory / "reviewed_baseline_links.parquet"),
            identity_review,
        )
        review_recovered = review_report["recovered"]
        baseline_tips = set(
            baseline.loc[
                baseline.legacy_broad_evidence & baseline.tree_join_status.eq("linked"), "tree_tip"
            ]
        )
        sources["baseline_review"] = receipt(review_path)
        sources["reviewed_baseline_links"] = receipt(
            review_directory / "reviewed_baseline_links.parquet"
        )
        sources["baseline_identity_review"] = receipt(
            review_directory / "baseline_identity_review.parquet"
        )
    if not baseline.loc[baseline.tree_join_status.eq("linked"), "tree_tip"].is_unique:
        raise ValueError("Different current GoaT species compete for the same baseline tip")
    species = pd.DataFrame(rows)
    extinction_witnesses = (
        {}
        if identity_review is None
        else identity_review[~identity_review.canonical_ncbi_taxid.duplicated(keep=False)]
        .set_index("canonical_ncbi_taxid")
        .to_dict("index")
    )
    resolved_statuses = []
    for row in species.to_dict("records"):
        flags = row.get("ott_flags")
        witness = extinction_witnesses.get(row["current_goat_taxid"], {})
        if witness.get("iucn_sis_id") == row["iucn_sis_id"] and str(
            witness.get("iucn_assessment_id")
        ) == str(row["iucn_assessment_id"]):
            flags = ",".join(
                v
                for v in [flags, witness.get("ott_extinction_flags_evidence")]
                if isinstance(v, str) and v
            )
        verified_category = (
            row["redlist_category"]
            if (
                str(row.get("assessment_sis_id")) == str(row["iucn_sis_id"])
                and re.search(r"\bGlobal\b", row.get("scopes") or "", re.I)
                and "subpopulation" not in row["iucn_scientific_name"].lower()
            )
            else None
        )
        resolved_statuses.append(extinction_status(verified_category, flags))
    species = pd.concat([species, pd.DataFrame(resolved_statuses)], axis=1)
    excluded_dna = baseline[baseline.legacy_broad_evidence & baseline.tree_join_status.ne("linked")]
    incomplete_genera = {
        (normalize_name(row.family), normalize_name(row.genus))
        for row in excluded_dna.itertuples()
        if normalize_name(row.family) and normalize_name(row.genus)
    }
    incomplete_families = {
        normalize_name(value) for value in excluded_dna.family if normalize_name(value)
    }
    species["current_ncbi_genus"] = species.iucn_sis_id.map(
        lambda sis: fresh[sis].get("ncbi_genus")
    )
    species["current_ncbi_family"] = species.iucn_sis_id.map(
        lambda sis: fresh[sis].get("ncbi_family")
    )
    species["genus_baseline_incomplete"] = [
        (normalize_name(family), normalize_name(genus)) in incomplete_genera
        for family, genus in zip(
            species.current_ncbi_family, species.current_ncbi_genus, strict=True
        )
    ]
    species["family_baseline_incomplete"] = species.current_ncbi_family.map(normalize_name).isin(
        incomplete_families
    )
    species["identity_score_eligible"] = species.gap_score_eligible
    species["gap_score_eligible"] &= ~(
        species.genus_baseline_incomplete & species.website_species_dna.eq(False)
    )
    gaps = marginal_gaps(tree, baseline_tips)
    species = species.merge(gaps, on="tree_tip", how="left", validate="many_to_one")
    if (
        species.identity_score_eligible
        & species.website_species_dna.eq(False)
        & species.website_dna_on_tip.eq(True)
    ).any():
        raise ValueError("A DNA-negative focal identity conflicts with a represented baseline tip")
    species["ark_gap_priority"] = species.threat_weight * species.genomic_gap_myr
    species.loc[~species.gap_score_eligible, "ark_gap_priority"] = float("nan")
    validation = json.loads(args.legacy_validation.read_text())
    legacy_record = validation["artifacts"]
    legacy_path = Path(legacy_record["species"])
    if (
        sha256(legacy_path) != legacy_record["species_sha256"]
        or sha256(args.crosswalk) != legacy_record["crosswalk_sha256"]
    ):
        raise ValueError(
            "Legacy species comparison is not the reviewed current crosswalk generation"
        )
    legacy = pd.read_parquet(legacy_path)[
        [
            "iucn_sis_id",
            "has_dna_species_level",
            "genus_has_dna",
            "family_has_dna",
            "goat_data_deficient",
        ]
    ]
    legacy["iucn_sis_id"] = pd.to_numeric(legacy.iucn_sis_id).astype("int64")
    legacy["legacy_row_present"] = True
    species = species.merge(legacy, on="iucn_sis_id", how="left", validate="one_to_one")
    required_flags = ["has_dna_species_level", "genus_has_dna", "family_has_dna", "goat_data_deficient"]
    complete = species[required_flags].notna().all(axis=1)
    if not species.loc[complete, required_flags].isin([True, False]).all().all():
        raise ValueError("Website comparison requires boolean DNA flags")
    species["legacy_comparison_status"] = "available"
    species.loc[~complete, "legacy_comparison_status"] = "missing_required_website_flags"
    species.loc[species.legacy_row_present.isna(), "legacy_comparison_status"] = "absent_from_website_cohort"
    species["legacy_comparison_available"] = complete

    def old_weight(row):
        if not row.legacy_comparison_available:
            return float("nan")
        if row.goat_data_deficient:
            return DEFAULT_WEIGHTS["gdd"]
        if not row.family_has_dna:
            return DEFAULT_WEIGHTS["fam"]
        if not row.genus_has_dna:
            return DEFAULT_WEIGHTS["gen"]
        if not row.has_dna_species_level:
            return DEFAULT_WEIGHTS["sp"]
        return DEFAULT_WEIGHTS["samp"]

    species["legacy_dna_weight"] = species.apply(old_weight, axis=1)
    species["legacy_priority"] = species.threat_weight * species.legacy_dna_weight
    eligible = species.gap_score_eligible
    identity_eligible = species.identity_score_eligible & species.legacy_comparison_available
    if (
        not species.loc[identity_eligible, "website_species_dna"]
        .astype(bool)
        .equals(species.loc[identity_eligible, "has_dna_species_level"].astype(bool))
    ):
        raise ValueError("Experimental DNA rule differs from validated website species flag")
    for name in ["ark_gap_priority", "legacy_priority"]:
        species[name + "_rank"] = (
            species.loc[eligible, name].rank(ascending=False, method="min").reindex(species.index)
        )
    candidates = species[
        eligible
        & species.website_species_dna.eq(False)
        & species.redlist_category.isin(["CR", "EN", "VU"])
    ]
    plan = greedy_targets(tree, baseline_tips, candidates, args.budget)
    plan = plan.merge(
        species[
            [
                "iucn_sis_id",
                "iucn_scientific_name",
                "redlist_category",
                "legacy_priority",
                "genomic_gap_myr",
            ]
        ],
        on="iucn_sis_id",
        validate="one_to_one",
        suffixes=("", "_static"),
    )
    # Independent branch-union verification on representative scored species.
    pd_baseline = rooted_pd(tree, baseline_tips)
    for row in (
        species[eligible].sort_values("ark_gap_priority", ascending=False).head(20).itertuples()
    ):
        oracle = rooted_pd(tree, baseline_tips | {row.tree_tip}) - pd_baseline
        if abs(oracle - row.genomic_gap_myr) > 1e-8:
            raise ValueError("Independent marginal PD calculation disagrees")
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    baseline.to_parquet(output / "goat_mammal_tree_audit.parquet", index=False)
    gaps.to_parquet(output / "complete_crown_gaps.parquet", index=False)
    species.to_parquet(output / "species_gap_pilot.parquet", index=False)
    plan.to_parquet(output / "greedy_selection.parquet", index=False)
    sources.update(
        crosswalk=receipt(args.crosswalk),
        crosswalk_provenance=receipt(summary_path),
        crosswalk_receipt=receipt(args.crosswalk.with_name("receipt.json")),
        tree_links=receipt(links_path),
        tree_links_report=receipt(args.links_dir / "report.json"),
        global_tree_tips=receipt(tips),
        ott_index=receipt(ott),
        legacy_species=receipt(legacy_path),
        legacy_validation=receipt(args.legacy_validation),
        config=receipt(args.config),
    )
    report = dict(
        schema_version=1,
        created_at=iso_now(),
        status="experimental_mammal_phylogenetic_gap_website_dna_rule",
        website_dna_rule="sample_acquired nonempty OR in_progress nonempty OR ebp_standard_criteria nonempty OR (assembly_level chromosome/complete genome AND BUSCO >=90)",
        clade=config["clade"],
        weights=DEFAULT_WEIGHTS,
        mammals=len(species),
        tree_linked=int(species.tree_identity_verified.sum()),
        eligible=int(eligible.sum()),
        identity_score_eligible=int(species.identity_score_eligible.sum()),
        priorities_withheld_for_incomplete_genus_baseline=int(
            (species.identity_score_eligible & ~eligible).sum()
        ),
        dna_positive_goat_records_without_tree_link=int(len(excluded_dna)),
        website_dna_baseline_tips=len(baseline_tips),
        baseline_review_recovered=review_recovered,
        extinction_status_policy="verified_iucn_overrides_ott_extinction_flags",
        iucn_extinction_overrides=int(species.ott_extinction_overridden.sum()),
        baseline_rooted_pd_myr=pd_baseline,
        goat_species_records=len(evidence),
        goat_website_dna_records=int(evidence.legacy_broad_evidence.sum()),
        eligible_website_dna=int(species.loc[eligible, "website_species_dna"].sum()),
        eligible_threatened_without_website_dna=len(candidates),
        join_status_counts=species.current_goat_join_status.value_counts().to_dict(),
        website_comparison_available=int((eligible & species.legacy_comparison_available).sum()),
        website_comparison_unavailable=int((eligible & ~species.legacy_comparison_available).sum()),
        comparison=comparison(species[eligible], "legacy_priority", "ark_gap_priority"),
        greedy_budget=args.budget,
        greedy_selected=len(plan),
        sources=sources,
        code={
            str(p.resolve().relative_to(Path(__file__).resolve().parents[2])): receipt(p)
            for p in [
                Path(__file__),
                Path("ark_pipeline/phylogenetic_gap.py"),
                Path("ark_pipeline/edge.py"),
                Path("ark_pipeline/edge_matching.py"),
                Path("ark_pipeline/edge_benchmark.py"),
                Path("ark_pipeline/builders/species_metadata.py"),
                Path("ark_pipeline/builders/coarse_cache.py"),
                Path("ark_pipeline/edge_links.py"),
                Path("ark_pipeline/edge_baseline_review.py"),
                Path("ark_pipeline/extinction_status.py"),
            ]
        },
        limitations=[
            "Uses the website broad DNA evidence rule by user request. Samples, projects and criteria text count as represented; this is not a completed-good-genome baseline or EBP quality certification.",
            "The baseline includes all uniquely linked eligible GoaT mammal species, including species without IUCN assessments. Baseline records without a unique tree link remain unrepresented and are audited.",
            "Rooted marginal PD is conditional on this mammal crown and observed baseline. An omitted baseline species can inflate gaps; absent GoaT fields do not prove absence of genomic resources.",
            "Priorities are withheld for DNA-negative species whose same genus/family has a DNA-positive GoaT record without a verified tree link. Other missing records in the family remain explicitly flagged; gaps/rankings are conditional on baseline coverage.",
            "Unknown/unsafe current GoaT identities and unlinked tree tips have no new priority. Genomic-transfer flags from previous artifacts remain unchanged.",
            "Ark gap priority is existing threat weight multiplied by uncovered dated branch length. It is not EDGE/EDGE2, does not measure sequence divergence directly, and is not a conservation recommendation.",
            "Legacy score ties use SIS-ID order for fixed-budget overlap diagnostics; large tied groups limit interpretation. Greedy threat-weighted selection is a heuristic, not a proven global optimum.",
            "No map, weights, metadata or serving pipeline changes. Tree-date sensitivity and full integration remain pending.",
        ],
    )
    report["outputs"] = {p.name: receipt(p) for p in sorted(output.glob("*.parquet"))}
    atomic_json(output / "report.json", report)
    text = [
        "# Mammal sequencing-gap pilot",
        "",
        f"{report['eligible']:,} mammals have checked tree/GoaT identities and experimental priorities. The website rule marks {len(baseline_tips):,} mammal tree tips represented.",
        f"{len(candidates):,} threatened eligible mammals lack website DNA evidence.",
        f"{report['dna_positive_goat_records_without_tree_link']:,} DNA-positive GoaT mammal records lack a verified tree link. Priorities for {report['priorities_withheld_for_incomplete_genus_baseline']:,} additional mammals are withheld because an unlinked DNA-positive record belongs to the same genus. Missing records elsewhere in the family remain flagged; the remaining estimates are conditional on baseline coverage.",
        "",
        f"Website comparisons are available for {report['website_comparison_available']:,} of {report['eligible']:,} eligible mammals; missing rows or flags remain unavailable.",
        f"Fixed-budget threatened top-100 overlap with the old score: {report['comparison'].get('top_overlap')} (legacy cutoff has {report['comparison'].get('legacy_at_cutoff')} tied species).",
        "",
        "## First greedy selections",
        "",
        "| Species | Category | Additional Myr | Old score |",
        "|---|---|---:|---:|",
    ]
    text += [
        f"| {r.iucn_scientific_name} | {r.redlist_category} | {r.genomic_gap_myr:.3f} | {format(r.legacy_priority, 'g') if pd.notna(r.legacy_priority) else 'Unavailable'} |"
        for r in plan.head(10).itertuples()
    ]
    text += ["", "## Limits", "", *["- " + v for v in report["limitations"]]]
    (output / "report.md").write_text("\n".join(text) + "\n")
    print(
        json.dumps(
            {
                key: report[key]
                for key in [
                    "mammals",
                    "tree_linked",
                    "eligible",
                    "website_dna_baseline_tips",
                    "eligible_threatened_without_website_dna",
                    "join_status_counts",
                    "comparison",
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
    parser.add_argument("--links-dir", type=Path, default=Path("data/edge/links-synonyms"))
    parser.add_argument(
        "--baseline-review",
        type=Path,
        help="Reviewed baseline receipt; uses the local cached review when present",
    )
    parser.add_argument(
        "--crosswalk",
        type=Path,
        default=Path(os.environ.get("GLOBAL_CROSSWALK_PATH", "data/exports/iucn_goat_global/iucn_goat_crosswalk.parquet")),
    )
    parser.add_argument(
        "--legacy-validation",
        type=Path,
        required=True,
        help="Validation receipt for the current website species artifact and crosswalk",
    )
    parser.add_argument("--output-dir", type=Path, default=Path("data/edge/phylogenetic-gap"))
    parser.add_argument("--budget", type=int, default=20)
    run(parser.parse_args())


if __name__ == "__main__":
    main()
