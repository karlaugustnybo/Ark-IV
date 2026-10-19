# /// script
# requires-python = ">=3.13"
# dependencies = ["marimo>=0.20,<0.21", "pandas>=3,<4", "pyarrow>=24"]
# ///
"""Inspect checked name changes and remaining species-concept disagreements."""

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
    directory = Path(os.environ.get("ARK_RESEARCH_RESULTS", project / "data/edge/links-synonyms"))
    mo.stop(not (directory / "report.json").is_file(), mo.md("No saved research report. Run the corresponding optional CLI or set `ARK_RESEARCH_RESULTS`."))
    report = json.loads((directory / "report.json").read_text())
    for name, output in report["outputs"].items():
        assert hashlib.sha256((directory / name).read_bytes()).hexdigest() == output["sha256"], name
    links = pd.read_parquet(directory / "species_tree_links.parquet")
    baseline = pd.read_parquet(report["sources"]["expanded_baseline_species"]["path"])
    audit = pd.read_parquet(directory / "name_change_audit.parquet")
    review = pd.read_parquet(directory / "remaining_review.parquet")
    coverage = pd.read_parquet(directory / "coverage_by_class.parquet")
    accepted = links[links.tree_identity_verified]
    new = links[links.join_method.eq("documented_binomial_name_change")]
    evidence = audit[audit.accepted.astype(bool)]
    assert links.iucn_sis_id.is_unique and accepted.tree_tip.is_unique
    assert len(links) == report["assessments"]
    assert len(accepted) == report["linked"]
    assert len(new) == len(evidence) == report["new_links"]
    assert evidence.same_epithet.eq(True).all()
    assert evidence.conflicting_ranks.fillna("").eq("").all()
    assert evidence.candidate_block_reason.fillna("").eq("").all()
    assert evidence.identity_basis.str.contains("ott_synonym|current_ncbi_name").all()
    assert accepted.ott_rank.eq("species").all() and accepted.label_occurrences.eq(1).all()
    assert new.ncbi_to_tree_link_verified.eq(False).all()
    before = baseline.set_index("iucn_sis_id")
    after = links.set_index("iucn_sis_id")
    for column in ["safe_for_automatic_species_trait_transfer", "ncbi_to_tree_link_verified", "original_join_status"]:
        assert before[column].equals(after[column])
    previous = before.loc[before.tree_identity_verified, "tree_tip"]
    assert previous.equals(after.loc[previous.index, "tree_tip"])
    assert review.iucn_sis_id.is_unique
    assert set(review.iucn_sis_id) == set(links.loc[~links.tree_identity_verified, "iucn_sis_id"])
    assert len(review) == report["remaining"]
    assert coverage.linked.sum() == report["linked"]
    # Genus changes cannot replace family/order/class evidence.
    for ranks in evidence.loc[evidence.documented_genus_change & ~evidence.agreeing_ranks.str.contains("genus"), "agreeing_ranks"]:
        assert set(ranks.split(",")) == {"family", "order", "class"}
    text = (directory / "report.md").read_text()
    return audit, coverage, links, report, review, text


@app.cell
def _(coverage, mo, text):
    mo.vstack([mo.md(text), mo.ui.table(coverage.sort_values("assessments", ascending=False), selection=None)])
    return


@app.cell
def _(mo):
    search = mo.ui.text(value="", label="Scientific name contains")
    search
    return (search,)


@app.cell
def _(audit, links, mo, review, search):
    selected = links[links.iucn_scientific_name.str.contains(search.value, case=False, regex=False, na=False)].head(100)
    mo.ui.tabs({
        "Links": mo.ui.table(selected, selection=None),
        "Name evidence": mo.ui.table(audit[audit.iucn_sis_id.isin(selected.iucn_sis_id)], selection=None),
        "Remaining review": mo.ui.table(review[review.iucn_sis_id.isin(selected.iucn_sis_id)], selection=None),
    })
    return


if __name__ == "__main__":
    app.run()
