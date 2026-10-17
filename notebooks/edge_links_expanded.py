# /// script
# requires-python = ">=3.13"
# dependencies = [
#     "marimo>=0.20,<0.21",
#     "pandas>=3,<4",
#     "pyarrow>=24",
# ]
# ///
"""Inspect independent IUCN/tree links; no acquisition, scoring or genomic transfer."""

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
    directory = Path(os.environ.get("ARK_RESEARCH_RESULTS", project / "data/edge/links-expanded"))
    mo.stop(not (directory / "report.json").is_file(), mo.md("No saved research report. Run the corresponding optional CLI or set `ARK_RESEARCH_RESULTS`."))
    report = json.loads((directory / "report.json").read_text())
    for name, output in report["outputs"].items():
        assert hashlib.sha256((directory / name).read_bytes()).hexdigest() == output["sha256"], name
    expanded = pd.read_parquet(directory / "species_tree_links.parquet")
    baseline = pd.read_parquet(report["sources"]["baseline_species"]["path"])
    audit = pd.read_parquet(directory / "iucn_tree_link_audit.parquet")
    coverage = pd.read_parquet(directory / "coverage_by_class.parquet")
    accepted = expanded[expanded.tree_identity_verified]
    new_links = expanded[expanded.join_method == "exact_iucn_name_three_lineage_ranks"]
    assert expanded.iucn_sis_id.is_unique
    assert accepted.tree_tip.is_unique
    assert len(accepted) == report["linked"]
    assert len(new_links) == report["new_links"]
    assert new_links.ncbi_to_tree_link_verified.eq(False).all()
    assert accepted.ott_rank.eq("species").all()
    assert accepted.label_occurrences.eq(1).all()
    assert len(audit[audit.accepted.astype(bool)]) == len(new_links)
    assert audit[audit.accepted.astype(bool)].conflicting_ranks.fillna("").eq("").all()
    assert baseline.set_index("iucn_sis_id").safe_for_automatic_species_trait_transfer.equals(expanded.set_index("iucn_sis_id").safe_for_automatic_species_trait_transfer)
    original = baseline[baseline.join_status == "linked"].set_index("iucn_sis_id").tree_tip
    assert original.equals(expanded.set_index("iucn_sis_id").loc[original.index, "tree_tip"])
    assert coverage.linked.sum() == report["linked"]
    report_text = (directory / "report.md").read_text()
    return audit, coverage, expanded, report, report_text


@app.cell
def _(coverage, mo, report, report_text):
    mo.vstack([
        mo.md(report_text),
        mo.md(f"**{report['new_links']:,} additional tree identity links**. Original checked links and genomic-transfer decisions are preserved. These outputs are identity checks, not global EDGE scores."),
        mo.ui.table(coverage.sort_values("assessments", ascending=False), selection=None),
    ])
    return


@app.cell
def _(mo):
    species_search = mo.ui.text(value="", label="Scientific name contains")
    species_search
    return (species_search,)


@app.cell
def _(audit, expanded, mo, species_search):
    selected = expanded[expanded.iucn_scientific_name.str.contains(species_search.value, case=False, regex=False, na=False)].head(100)
    selected_audit = audit[audit.iucn_sis_id.isin(selected.iucn_sis_id)]
    mo.ui.tabs({
        "Identity links": mo.ui.table(selected, selection=None),
        "Lineage evidence": mo.ui.table(selected_audit, selection=None),
    })
    return


if __name__ == "__main__":
    app.run()
