# /// script
# requires-python = ">=3.13"
# dependencies = ["marimo>=0.20,<0.21", "pandas>=3,<4", "pyarrow>=24"]
# ///
"""Verify and explore the mammal pilot using the current website DNA rule."""

import marimo

__generated_with = "0.20.4"
app = marimo.App(width="medium")


@app.cell
def _():
    import hashlib
    import json
    import os
    from pathlib import Path

    import marimo as mo
    import pandas as pd

    return Path, hashlib, json, mo, os, pd


@app.cell
def _(Path, hashlib, json, mo, os, pd):
    project = Path(__file__).resolve().parents[1]
    directory = Path(os.environ.get("ARK_RESEARCH_RESULTS", project / "data/edge/phylogenetic-gap"))
    mo.stop(not (directory / "report.json").is_file(), mo.md("No saved research report. Run the corresponding optional CLI or set `ARK_RESEARCH_RESULTS`."))
    report = json.loads((directory / "report.json").read_text())
    for name, output in report["outputs"].items():
        assert hashlib.sha256((directory / name).read_bytes()).hexdigest() == output["sha256"], name
    for name, code in report["code"].items():
        assert hashlib.sha256(Path(code["path"]).read_bytes()).hexdigest() == code["sha256"], name
    species = pd.read_parquet(directory / "species_gap_pilot.parquet")
    gaps = pd.read_parquet(directory / "complete_crown_gaps.parquet")
    audit = pd.read_parquet(directory / "goat_mammal_tree_audit.parquet")
    plan = pd.read_parquet(directory / "greedy_selection.parquet")
    links = pd.read_parquet(report["sources"]["tree_links"]["path"])
    legacy = pd.read_parquet(report["sources"]["legacy_species"]["path"])
    legacy["iucn_sis_id"] = pd.to_numeric(legacy.iucn_sis_id).astype("int64")
    assert species.iucn_sis_id.is_unique and gaps.tree_tip.is_unique
    assert audit.taxon_id.is_unique
    assert audit.loc[audit.tree_join_status.eq("linked"), "tree_tip"].is_unique
    assert len(species) == report["mammals"]
    assert species.tree_identity_verified.sum() == report["tree_linked"]
    assert species.identity_score_eligible.sum() == report["identity_score_eligible"]
    eligible = species[species.gap_score_eligible]
    held = species[species.identity_score_eligible & ~species.gap_score_eligible]
    assert len(eligible) == report["eligible"]
    assert len(held) == report["priorities_withheld_for_incomplete_genus_baseline"]
    assert held.genus_baseline_incomplete.all() and held.website_species_dna.eq(False).all()
    assert species.loc[~species.gap_score_eligible, "ark_gap_priority"].isna().all()
    assert eligible.ark_gap_priority.notna().all() and eligible.genomic_gap_myr.ge(0).all()
    assert (
        (eligible.ark_gap_priority - eligible.threat_weight * eligible.genomic_gap_myr)
        .abs()
        .lt(1e-8)
        .all()
    )
    assert eligible.loc[eligible.website_species_dna.eq(True), "genomic_gap_myr"].eq(0).all()
    assert report["extinction_status_policy"] == "verified_iucn_overrides_ott_extinction_flags"
    known_status = species[species.extinction_status_source.eq("IUCN")]
    assert known_status.effective_extinct.astype(bool).equals(
        known_status.redlist_category.eq("EX")
    )
    overridden_status = species[species.ott_extinction_overridden]
    assert len(overridden_status) == report["iucn_extinction_overrides"]
    assert overridden_status.ott_extinct_flag.all()
    assert overridden_status.effective_extinct.eq(False).all()
    assert overridden_status.loc[overridden_status.tree_tip.isna(), "ark_gap_priority"].isna().all()
    # Compare available website rows in the checked identity cohort, including withheld priorities.
    assert species.loc[~species.legacy_comparison_available, "legacy_priority"].isna().all()
    assert species.legacy_comparison_status.eq("available").equals(species.legacy_comparison_available)
    assert (eligible.legacy_comparison_available.sum() == report["website_comparison_available"])
    assert ((~eligible.legacy_comparison_available).sum() == report["website_comparison_unavailable"])
    identity = species[species.identity_score_eligible & species.legacy_comparison_available].set_index("iucn_sis_id")
    old = legacy.set_index("iucn_sis_id").loc[identity.index]
    assert identity.website_species_dna.astype(bool).equals(old.has_dna_species_level.astype(bool))
    # Tree identities and all upstream genomic-transfer decisions are unchanged.
    before = links.set_index("iucn_sis_id").loc[species.iucn_sis_id]
    after = species.set_index("iucn_sis_id")
    for column in [
        "tree_tip",
        "tree_identity_verified",
        "ncbi_to_tree_link_verified",
        "safe_for_automatic_species_trait_transfer",
    ]:
        assert before[column].equals(after[column]), column
    witnessed = set(
        audit.loc[audit.legacy_broad_evidence & audit.tree_join_status.eq("linked"), "tree_tip"]
    )
    assert witnessed == set(gaps.loc[gaps.website_dna_on_tip, "tree_tip"])
    assert len(witnessed) == report["website_dna_baseline_tips"]
    assert gaps.loc[gaps.website_dna_on_tip, "genomic_gap_myr"].eq(0).all()
    assert set(gaps.nearest_dna_tip.dropna()).issubset(witnessed)
    assert (audit.legacy_broad_evidence & audit.tree_join_status.ne("linked")).sum() == report[
        "dna_positive_goat_records_without_tree_link"
    ]
    assert len(plan) == report["greedy_selected"] <= report["greedy_budget"]
    assert plan.tree_tip.is_unique and plan.iucn_sis_id.is_unique
    assert plan.step.tolist() == list(range(1, len(plan) + 1))
    assert not set(plan.tree_tip) & witnessed
    assert plan.genomic_gap_myr.gt(0).all()
    assert plan.genomic_gap_myr.le(plan.genomic_gap_myr_static + 1e-8).all()
    text = (directory / "report.md").read_text()
    return audit, plan, report, species, text


@app.cell
def _(mo, plan, text):
    mo.vstack([mo.md(text), mo.ui.table(plan, selection=None)])
    return


@app.cell
def _(Path, audit, hashlib, json, mo, pd, report, species):
    _review_display = mo.md("No baseline identity review attached to this run.")
    if "baseline_review" in report["sources"]:
        review_path = Path(report["sources"]["baseline_review"]["path"])
        assert (
            hashlib.sha256(review_path.read_bytes()).hexdigest()
            == report["sources"]["baseline_review"]["sha256"]
        )
        review_report = json.loads(review_path.read_text())
        for _name, _output in review_report["outputs"].items():
            assert (
                hashlib.sha256((review_path.parent / _name).read_bytes()).hexdigest()
                == _output["sha256"]
            ), _name
        for _code in review_report["code"].values():
            assert hashlib.sha256(Path(_code["path"]).read_bytes()).hexdigest() == _code["sha256"]
        input_record = review_report["sources"]["input_pilot_audit"]
        assert (
            hashlib.sha256(Path(input_record["path"]).read_bytes()).hexdigest()
            == input_record["sha256"]
        )
        original_audit = pd.read_parquet(input_record["path"]).set_index("taxon_id")
        reviewed_audit = audit.set_index("taxon_id").loc[original_audit.index]
        identity_review = pd.read_parquet(review_path.parent / "baseline_identity_review.parquet")
        candidate_review = pd.read_parquet(review_path.parent / "candidate_review.parquet")
        assert identity_review.taxon_id.is_unique
        assert len(identity_review) == review_report["reviewed"]
        assert (
            identity_review.recovered.sum()
            == review_report["recovered"]
            == report["baseline_review_recovered"]
        )
        assert (
            identity_review.review_reason.eq("multiple_tree_tips_for_current_ncbi_species")
            & identity_review.recovered
        ).sum() == 0
        for _column in ["canonical_ncbi_taxid", "scientific_name", "legacy_broad_evidence"]:
            assert original_audit[_column].equals(reviewed_audit[_column]), _column
        unchanged = ~original_audit.index.isin(
            identity_review.loc[identity_review.recovered, "taxon_id"]
        )
        for _column in ["tree_tip", "tree_join_status", "tree_join_route"]:
            assert original_audit.loc[unchanged, _column].equals(
                reviewed_audit.loc[unchanged, _column]
            ), _column
        confirmed = candidate_review[candidate_review.accepted]
        assert len(confirmed) == review_report["recovered"]
        assert confirmed.candidate_reason.eq("eligible").all()
        assert confirmed.conflicting_ranks.fillna("").eq("").all()
        assert confirmed.agreeing_ranks.str.split(",").map(len).ge(3).all()
        assert review_report["extinction_status_policy"] == report["extinction_status_policy"]
        status_overrides = pd.read_parquet(
            review_path.parent / "extinction_status_overrides.parquet"
        )
        assert len(status_overrides) == review_report["iucn_extinction_overrides"]
        assert status_overrides.extinction_status_source.eq("IUCN").all()
        assert status_overrides.effective_extinct.eq(False).all()
        assert (
            status_overrides.iucn_sis_id.notna().all()
            and status_overrides.iucn_assessment_id.notna().all()
        )
        for override in status_overrides.itertuples():
            focal = species[species.iucn_sis_id.eq(override.iucn_sis_id)]
            assert len(focal) == 1
            assert focal.redlist_category.iloc[0] == override.iucn_category
            assert focal.ott_extinction_overridden.iloc[0]
        _review_display = mo.vstack(
            [
                mo.md((review_path.parent / "report.md").read_text()),
                mo.ui.table(identity_review, selection=None),
            ]
        )
    _review_display
    return


@app.cell
def _(mo):
    search = mo.ui.text(value="", label="Scientific name contains")
    search
    return (search,)


@app.cell
def _(audit, mo, search, species):
    selected = species[
        species.iucn_scientific_name.str.contains(search.value, case=False, regex=False, na=False)
    ].head(100)
    mo.ui.tabs(
        {
            "Priorities and coverage": mo.ui.table(selected, selection=None),
            "GoaT baseline witnesses": mo.ui.table(
                audit[audit.canonical_ncbi_taxid.isin(selected.current_goat_taxid)], selection=None
            ),
        }
    )
    return


if __name__ == "__main__":
    app.run()
