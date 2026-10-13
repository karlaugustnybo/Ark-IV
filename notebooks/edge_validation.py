# /// script
# requires-python = ">=3.13"
# dependencies = [
#     "marimo>=0.20,<0.21",
#     "pandas>=3,<4",
#     "pyarrow>=24",
#     "altair>=6,<7",
# ]
# ///
"""Inspect saved mammal EDGE results; never acquires or alters source data."""

import marimo

__generated_with = "0.20.4"
app = marimo.App(width="medium")


@app.cell
def _():
    import hashlib
    import json
    import os
    from pathlib import Path

    import altair as alt
    import marimo as mo
    import pandas as pd

    return Path, alt, hashlib, json, mo, os, pd


@app.cell
def _(Path, hashlib, json, mo, os, pd):
    project_root = Path(__file__).resolve().parents[1]
    results_dir = Path(os.environ.get("ARK_RESEARCH_RESULTS", project_root / "data/edge/validation"))
    mo.stop(not (results_dir / "report.json").is_file(), mo.md("No saved research report. Run the corresponding optional CLI or set `ARK_RESEARCH_RESULTS`."))
    report = json.loads((results_dir / "report.json").read_text())
    for artifact_name, artifact_receipt in report["outputs"].items():
        actual_hash = hashlib.sha256((results_dir / artifact_name).read_bytes()).hexdigest()
        assert actual_hash == artifact_receipt["sha256"], f"Changed output: {artifact_name}"
    species = pd.read_parquet(results_dir / "species_edge_validated_pilot.parquet")
    stability = pd.read_parquet(results_dir / "species_tree_stability.parquet")
    outliers = pd.read_parquet(results_dir / "outlier_diagnostics.parquet")
    match_audit = pd.read_parquet(results_dir / "matching_audit.parquet")
    assert species.iucn_sis_id.is_unique
    assert stability.groupby(["tree_group", "iucn_sis_id"]).size().max() == 1
    report_text = (results_dir / "report.md").read_text()
    return match_audit, outliers, report, report_text, species, stability


@app.cell
def _(mo, report):
    mo.md(f"""
    # Mammal EDGE: what changes when we change the tree?

    **{report['coverage']['improved_scored']:,} safely matched mammals**, scored on
    **{report['tree_count']} trees** with **{report['samples_per_tree']} common risk draws per tree**.

    Larger EDGE2 scores indicate more modeled evolutionary history at risk.
    Rank 1 means the highest score within the safely scored mammal cohort.
    Pilot ranges show sampled tree variation; they are not confidence intervals.
    These results identify research leads and do not establish official EDGE species.
    """)
    return


@app.cell
def _(mo, species):
    selectable_species = species[species.join_status == "scored"].sort_values("iucn_scientific_name")
    species_options = dict(zip(selectable_species.iucn_scientific_name, selectable_species.iucn_sis_id))
    species_choice = mo.ui.dropdown(species_options, value=next(iter(species_options), None), label="Choose a mammal", searchable=True)
    species_choice
    return (species_choice,)


@app.cell
def _(alt, match_audit, mo, outliers, species, species_choice, stability):
    selected_sis = species_choice.value
    selected_scores = species[species.iucn_sis_id == selected_sis]
    selected_stability = stability[stability.iucn_sis_id == selected_sis]
    selected_match = match_audit[match_audit.iucn_sis_id == selected_sis]
    selected_disagreement = outliers[outliers.iucn_sis_id == selected_sis]
    rank_ranges = alt.Chart(selected_stability).mark_rule(strokeWidth=7).encode(
        x=alt.X("rank_min:Q", title="Rank among safely scored mammals (lower is higher priority)", scale=alt.Scale(zero=False)),
        x2="rank_max:Q", y=alt.Y("tree_group:N", title="Tree comparison"),
        tooltip=["tree_group", "trees", "rank_min", "rank_max", "top100_threatened_trees"]
    ).properties(height=150, title="Rank range across sampled trees")
    baseline_rank = alt.Chart(selected_stability).mark_tick(color="#ce5905", thickness=3, size=22).encode(
        x="baseline_rank:Q", y="tree_group:N", tooltip=["baseline_rank"]
    )
    mo.vstack([
        mo.md("Orange marks show the original equal-splits median-tree rank. Grey ranges show each comparison; the birth median is one tree."),
        mo.ui.altair_chart(rank_ranges + baseline_rank),
        mo.ui.tabs({
            "Tree sensitivity": mo.ui.table(selected_stability, selection=None),
            "Reference disagreement": mo.ui.table(selected_disagreement, selection=None),
            "Matching evidence": mo.vstack([mo.md("An empty table means no exact-name rescue was attempted; inspect join_method in the score row."), mo.ui.table(selected_match, selection=None)]),
            "Score and join record": mo.ui.table(selected_scores, selection=None),
        })
    ])
    return


@app.cell
def _(alt, mo, outliers):
    same_category_points = outliers[outliers.category_agrees & (outliers.ark_edge2_myr_median > 0) & (outliers.reference_edge2_myr > 0)]
    agreement_chart = alt.Chart(same_category_points).mark_circle(size=15, opacity=0.3).encode(
        x=alt.X("reference_edge2_myr:Q", scale=alt.Scale(type="log"), title="Published EDGE2 median (Myr, log scale)"),
        y=alt.Y("ark_edge2_myr_median:Q", scale=alt.Scale(type="log"), title="Ark EDGE2 median (Myr, log scale)"),
        tooltip=["iucn_scientific_name", "redlist_category", "reference_edge2_myr", "ark_edge2_myr_median", "diagnostic"]
    ).properties(height=360, title="Comparison restricted to species with the same threat category")
    mo.ui.altair_chart(agreement_chart)
    return


@app.cell
def _(mo, outliers, report_text, stability):
    stable_leads = stability[(stability.tree_group.isin(["birth-topology", "birth-dates"])) & (stability.top100_threatened_trees == stability.trees)]
    mo.ui.tabs({
        "Plain-English report": mo.md(report_text),
        "Largest score disagreements": mo.ui.table(outliers.head(30), selection=None),
        "Always in sampled threatened top 100": mo.vstack([
            mo.md("Rows are per tree comparison. A species may appear in one or both groups; the sampled trees are a convenience sample, not a full-ensemble guarantee."),
            mo.ui.table(stable_leads, selection=None),
        ]),
    })
    return


if __name__ == "__main__":
    app.run()
