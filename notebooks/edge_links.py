# /// script
# requires-python = ">=3.13"
# dependencies = [
#     "marimo>=0.20,<0.21",
#     "pandas>=3,<4",
#     "pyarrow>=24",
# ]
# ///
"""Inspect saved links to Duke's unchanged tree; no acquisition or scoring."""

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
    links_dir = Path(os.environ.get("ARK_RESEARCH_RESULTS", Path(__file__).resolve().parents[1] / "data/edge/links"))
    mo.stop(not (links_dir / "report.json").is_file(), mo.md("No saved research report. Run the corresponding optional CLI or set `ARK_RESEARCH_RESULTS`."))
    report = json.loads((links_dir / "report.json").read_text())
    for output_name, output_receipt in report["outputs"].items():
        assert hashlib.sha256((links_dir / output_name).read_bytes()).hexdigest() == output_receipt["sha256"], output_name
    species_links = pd.read_parquet(links_dir / "species_tree_links.parquet")
    coverage = pd.read_parquet(links_dir / "coverage_by_class.parquet")
    name_audit = pd.read_parquet(links_dir / "name_link_audit.parquet")
    accepted = species_links[species_links.join_status == "linked"]
    assert len(species_links) == report["assessments"]
    assert len(accepted) == report["linked"]
    assert species_links.iucn_sis_id.is_unique
    assert accepted.tree_tip.is_unique
    assert accepted.ott_rank.eq("species").all()
    assert accepted.current_ncbi_rank.eq("species").all()
    assert accepted.ott_id.ne("0").all()
    assert accepted.safe_for_automatic_species_trait_transfer.eq(True).all()
    assert species_links.loc[species_links.join_status.ne("linked"), "tree_tip"].isna().all()
    assert coverage.assessments.sum() == report["assessments"]
    assert coverage.linked.sum() == report["linked"]
    assert not any("edge2" in name for name in species_links.columns)
    report_text = (links_dir / "report.md").read_text()
    return coverage, name_audit, report, report_text, species_links


@app.cell
def _(coverage, mo, report, report_text):
    mo.vstack([
        mo.md(report_text),
        mo.md(f"**{report['linked']:,} checked links**. This table establishes identity and input availability, not scientific acceptance of dates or EDGE scores."),
        mo.ui.table(coverage.sort_values("assessments", ascending=False), selection=None),
    ])
    return


@app.cell
def _(mo):
    species_search = mo.ui.text(value="", label="Scientific name contains")
    species_search
    return (species_search,)


@app.cell
def _(mo, name_audit, species_links, species_search):
    selected_links = species_links[species_links.iucn_scientific_name.str.contains(species_search.value, case=False, regex=False, na=False)].head(100)
    selected_audit = name_audit[name_audit.iucn_sis_id.isin(selected_links.iucn_sis_id)]
    mo.ui.tabs({
        "Ark species links": mo.ui.table(selected_links, selection=None),
        "Exact-name evidence": mo.ui.table(selected_audit, selection=None),
    })
    return


if __name__ == "__main__":
    app.run()
