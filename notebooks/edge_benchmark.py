# /// script
# requires-python = ">=3.13"
# dependencies = [
#     "marimo>=0.20,<0.21",
#     "pandas>=3,<4",
#     "pyarrow>=24",
# ]
# ///
"""Inspect saved matched-input EDGE2 checks; does not acquire or alter inputs."""

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
    benchmark_dir = Path(os.environ.get("ARK_RESEARCH_RESULTS", Path(__file__).resolve().parents[1] / "data/edge/benchmark/results"))
    mo.stop(not (benchmark_dir / "report.json").is_file(), mo.md("No saved research report. Run the corresponding optional CLI or set `ARK_RESEARCH_RESULTS`."))
    report = json.loads((benchmark_dir / "report.json").read_text())
    for output_name, output_receipt in report["outputs"].items():
        assert hashlib.sha256((benchmark_dir / output_name).read_bytes()).hexdigest() == output_receipt["sha256"], output_name
    calculator = pd.read_parquet(benchmark_dir / "calculator_agreement.parquet")
    pairs = pd.read_parquet(benchmark_dir / "paired_tree_comparisons.parquet")
    species = pd.read_parquet(benchmark_dir / "species_tree_comparison.parquet")
    mappings = pd.read_parquet(benchmark_dir / "reference_tip_mappings.parquet")
    assert report["calculator_cases_passed"]
    assert calculator.groupby("case").species.nunique().sum() == report["species_case_records"]
    assert len(species) == report["shared_reference_species"]
    assert species.species.is_unique
    assert len(pairs) == report["upham_source_trees"] * len(report["tree_comparison"])
    report_text = (benchmark_dir / "report.md").read_text()
    return calculator, mappings, pairs, report, report_text, species


@app.cell
def _(calculator, mo, report, report_text):
    checks = [dict(case=case["case"], **case["authors_R"]) for case in report["calculator_cases"]]
    mo.vstack([
        mo.md(report_text),
        mo.md("The comparisons below use identical saved trees and probability vectors. ED2 and EDGE2 errors are floating-point residuals, in millions of years."),
        mo.ui.table(checks, selection=None),
        mo.ui.table(calculator.groupby("case", as_index=False)[["ed2_absolute_error_myr", "edge2_absolute_error_myr"]].max(), selection=None),
    ])
    return


@app.cell
def _(mo, pairs):
    mo.vstack([
        mo.md("## Tree effects with fixed threats\n\nEach tree is pruned to the same species, preserving branches and excluding the crown stem. This is a diagnostic cohort, not production mammal scoring. All trees receive exactly the same published category-median risks. Top-100 cutoffs include ties."),
        mo.ui.table(pairs.groupby("duke_tree")[["edge2_rank_correlation", "threatened_top100_overlap"]].agg(["min", "median", "max"]).reset_index(), selection=None),
        mo.ui.table(pairs, selection=None),
    ])
    return


@app.cell
def _(mappings, mo, species):
    mo.ui.tabs({
        "Species and branches": mo.ui.table(species, selection=None),
        "Reference identity matches": mo.ui.table(mappings, selection=None),
    })
    return


if __name__ == "__main__":
    app.run()
