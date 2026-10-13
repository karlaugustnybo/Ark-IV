"""Compare independent EDGE2 calculators and isolate mammal-tree effects."""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
import tomllib
from importlib.metadata import distribution
from pathlib import Path

import numpy as np
import pandas as pd

from ark_pipeline.cli.edge_prototype import receipt
from ark_pipeline.edge import acquire_inputs, load_clade, read_mammal_reference
from ark_pipeline.edge_benchmark import (
    agreement,
    benchmark_newick,
    empirical_risks,
    fixed_engine_scores,
    independent_branch_scores,
    normalize_scientific_name,
    reference_tip_map,
)
from ark_pipeline.runtime.provenance import atomic_json, iso_now, sha256

MEDIAN_RISK = {
    "LC": 0.060625,
    "NT": 0.12125,
    "VU": 0.2425,
    "EN": 0.485,
    "CR": 0.97,
    "EW": 0.97,
    "DD": 0.232,
    "NE": 0.232,
}
THREATENED = {"VU", "EN", "CR", "EW"}


def crown(path):
    from ete4 import Tree

    tree = Tree(path.read_text(), parser=1)
    tree.dist = 0
    return tree


def run(args):
    from dated_complete_tree.tree_fixing_utils import open_config_csv

    project = Path(__file__).resolve().parents[2]
    config = tomllib.loads(args.config.read_text())
    origin = json.loads(distribution("dated-complete-tree").read_text("direct_url.json") or "{}")
    if origin.get("vcs_info", {}).get("commit_id") != config["engine_commit"]:
        raise ValueError("Run uv sync --group edge for the pinned engine")
    sources = acquire_inputs(config, args.input_dir, args.download)
    core_config_path = project / "config/edge_prototype.toml"
    core_config = tomllib.loads(core_config_path.read_text())
    core = acquire_inputs(core_config, args.core_input_dir, False)
    birth = acquire_inputs(
        {"sources": core_config["validation_sources"]}, args.core_input_dir, False
    )
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    prepared = args.input_dir.resolve() / "upham-crowns"
    r_library = args.r_library.resolve()
    subprocess.run(
        [
            "Rscript",
            str(project / "scripts/edge_prepare_upham.R"),
            sources["upham-node-dated-100"]["path"],
            sources["upham-taxonomy"]["path"],
            str(prepared),
            str(r_library),
        ],
        check=True,
    )
    upham_paths = sorted(prepared.glob("*.tre"))
    if len(upham_paths) != 100:
        raise ValueError("Expected exactly 100 published sample trees")
    reference = read_mammal_reference(Path(core["mammal-reference"]["path"]))
    ref_by_key = {normalize_scientific_name(r["reference_name"]): r for r in reference}
    if len(ref_by_key) != len(reference):
        raise ValueError("Reference species names must be unique")
    previous = json.loads((args.reviewed_dir / "report.json").read_text())
    reviewed_path = args.reviewed_dir / "species_edge_validated_pilot.parquet"
    if sha256(reviewed_path) != previous["outputs"][reviewed_path.name]["sha256"]:
        raise ValueError("Reviewed mapping differs from its validation receipt")
    reviewed = pd.read_parquet(reviewed_path)
    duke_trees = {
        "duke-equal": load_clade(Path(core["dated-tree"]["path"]), core_config["clade"]),
        "duke-birth": load_clade(Path(birth["birth-model-median"]["path"]), core_config["clade"]),
    }
    mappings, mapping_audits = {}, {}
    for name, tree in duke_trees.items():
        mappings[name], mapping_audits[name] = reference_tip_map(tree, reference, reviewed)
    upham0 = crown(upham_paths[0])
    upham_map, upham_audit = reference_tip_map(upham0, reference)
    mapping_rows = []
    for name, audit in {**mapping_audits, "upham": upham_audit}.items():
        mapping_rows.extend({**row, "tree_group": name} for row in audit.pop("tip_mappings"))
    pd.DataFrame(mapping_rows).to_parquet(output / "reference_tip_mappings.parquet", index=False)
    with open_config_csv(None, "p_extinction.csv") as handle:
        risk_rows = list(csv.reader(handle))[1:]
        risk_receipt = receipt(Path(handle.name))
    # Calculator cases retain complete crowns and use exactly the same serialized inputs.
    case_dir = output / "calculator-cases"
    case_dir.mkdir(exist_ok=True)
    cases, python_checks = [], []
    full_trees = {"upham-000": upham0, "upham-001": crown(upham_paths[1]), **duke_trees}
    for index, (tree_name, tree) in enumerate(full_trees.items()):
        mapping = upham_map if tree_name.startswith("upham") else mappings[tree_name]
        keys = sorted(leaf.name for leaf in tree.leaves())
        categories = {tip: ref_by_key[key]["reference_category"] for tip, key in mapping.items()}
        point = np.array([MEDIAN_RISK[categories.get(key, "NE")] for key in keys])
        random = empirical_risks(risk_rows, keys, categories, args.seed + index, 1)[0]
        tree_path = case_dir / f"{tree_name}.tre"
        tree_path.write_text(benchmark_newick(tree))
        for kind, vector in [("published-median-risk", point), ("empirical-risk-draw", random)]:
            case = f"{tree_name}-{kind}"
            probability_path = case_dir / f"{case}-risks.csv"
            pd.DataFrame({"species": keys, "pext": vector}).to_csv(probability_path, index=False)
            # Read the actual CSV sent to R, including decimal conversion.
            probabilities = pd.read_csv(probability_path).set_index("species").pext.to_dict()
            same_tree = crown(tree_path)
            duke = fixed_engine_scores(same_tree, probabilities)
            oracle = independent_branch_scores(same_tree, probabilities)
            _, check = agreement(duke, oracle)
            python_checks.append({"case": case, "oracle": check})
            duke.to_parquet(case_dir / f"{case}-duke.parquet", index=False)
            cases.append(
                {
                    "case": case,
                    "tree": str(tree_path),
                    "probabilities": str(probability_path),
                    "output": str(case_dir / f"{case}-reference.csv"),
                }
            )
    case_file = output / "calculator_cases.csv"
    pd.DataFrame(cases).to_csv(case_file, index=False)
    print("Running unmodified authors' R calculator on eight matched-input cases", flush=True)
    subprocess.run(
        [
            "Rscript",
            str(project / "scripts/edge_reference_benchmark.R"),
            sources["reference-edge-r"]["path"],
            str(case_file),
            str(r_library),
        ],
        cwd=output,
        check=True,
    )
    all_comparisons = []
    for case, check in zip(cases, python_checks, strict=True):
        duke = pd.read_parquet(case_dir / f"{case['case']}-duke.parquet")
        original = pd.read_csv(case["output"]).rename(
            columns={"Species": "species", "ED": "ed2_myr", "EDGE": "edge2_myr"}
        )
        if not np.allclose(
            duke.set_index("species").pext.sort_index(),
            original.set_index("species").pext.sort_index(),
            atol=0,
            rtol=1e-14,
        ):
            raise ValueError("Reference calculator received different risk values")
        comparison, check["authors_R"] = agreement(duke, original)
        comparison["case"] = case["case"]
        all_comparisons.append(comparison)
    pd.concat(all_comparisons, ignore_index=True).to_parquet(
        output / "calculator_agreement.parquet", index=False
    )
    if not all(c[engine]["passed"] for c in python_checks for engine in ["oracle", "authors_R"]):
        raise ValueError("Calculator disagreement: inspect saved matched-input cases")
    print(
        "All three calculations agree. Isolating tree effects across 100 Upham trees.", flush=True
    )
    common = set(upham_map.values())
    for mapping in mappings.values():
        common &= set(mapping.values())
    if len(common) < 100:
        raise ValueError("Insufficient unique shared reference species")
    keys = sorted(common)
    canonical = {key: key.replace(" ", "_") for key in keys}
    risks = {canonical[key]: MEDIAN_RISK[ref_by_key[key]["reference_category"]] for key in keys}
    threat_categories = {canonical[key]: ref_by_key[key]["reference_category"] for key in keys}
    matched_rows, crown_receipts = [], []

    def compare_tree(tree, mapping, tree_id, group):
        current = {leaf.name for leaf in tree.leaves()}
        if not set(mapping).issubset(current):
            raise ValueError("Author sample changed its tip universe")
        keep = [tip for tip, key in mapping.items() if key in common]
        tree.prune(keep, preserve_branch_length=True)
        stem = float(tree.dist)
        tree.dist = 0
        for leaf in tree.leaves():
            leaf.name = canonical[mapping[leaf.name]]
        scored = fixed_engine_scores(tree, risks)
        scored["tree_id"], scored["tree_group"] = tree_id, group
        scored["reference_category"] = scored.species.map(threat_categories)
        scored["threatened_rank"] = scored.loc[
            scored.reference_category.isin(THREATENED), "edge2_myr"
        ].rank(ascending=False, method="min")
        scored["assessed_rank"] = scored.edge2_myr.rank(ascending=False, method="average")
        scored["in_threatened_top100"] = scored.threatened_rank.le(100)
        matched_rows.append(scored)
        crown_receipts.append(
            {"tree_id": tree_id, "shared_tips": len(tree), "excluded_crown_stem_myr": stem}
        )

    for name, tree in duke_trees.items():
        compare_tree(tree.copy(), mappings[name], name, name)
    original_upham_tips = {leaf.name for leaf in upham0.leaves()}
    for i, path in enumerate(upham_paths):
        tree = crown(path)
        if {leaf.name for leaf in tree.leaves()} != original_upham_tips:
            raise ValueError("Upham sample tip universes differ")
        compare_tree(tree, upham_map, f"upham-{i:03d}", "upham")
        if (i + 1) % 25 == 0:
            print(f"Compared {i + 1}/100 author sample trees", flush=True)
    matched = pd.concat(matched_rows, ignore_index=True)
    matched.to_parquet(output / "matched_tree_scores.parquet", index=False)
    upham = matched[matched.tree_group == "upham"]
    medians = upham.groupby("species", as_index=False).agg(
        upham_ed2_myr=("ed2_myr", "median"),
        upham_edge2_myr=("edge2_myr", "median"),
        upham_terminal_myr=("terminal_myr", "median"),
        upham_top100_trees=("in_threatened_top100", "sum"),
    )
    paired_stats = []
    for name in duke_trees:
        base = matched[matched.tree_id == name].set_index("species")
        medians = medians.merge(
            base[["ed2_myr", "edge2_myr", "terminal_myr"]]
            .add_prefix(name.replace("-", "_") + "_")
            .reset_index(),
            on="species",
            validate="one_to_one",
        )
        for tree_id, sampled in upham.groupby("tree_id"):
            pair = sampled.set_index("species").join(
                base[["edge2_myr", "in_threatened_top100"]], rsuffix="_duke", validate="one_to_one"
            )
            paired_stats.append(
                {
                    "duke_tree": name,
                    "upham_tree": tree_id,
                    "edge2_rank_correlation": pair.edge2_myr.corr(
                        pair.edge2_myr_duke, method="spearman"
                    ),
                    "threatened_top100_overlap": int(
                        (pair.in_threatened_top100 & pair.in_threatened_top100_duke).sum()
                    ),
                    "upham_top100_size": int(pair.in_threatened_top100.sum()),
                    "duke_top100_size": int(pair.in_threatened_top100_duke.sum()),
                }
            )
    pairs = pd.DataFrame(paired_stats)
    pairs.to_parquet(output / "paired_tree_comparisons.parquet", index=False)
    medians["reference_name"] = medians.species.map(
        {canonical[k]: ref_by_key[k]["reference_name"] for k in keys}
    )
    medians["reference_category"] = medians.species.map(threat_categories)
    medians["published_terminal_myr"] = medians.species.map(
        {canonical[k]: ref_by_key[k]["reference_terminal_myr"] for k in keys}
    )
    medians.to_parquet(output / "species_tree_comparison.parquet", index=False)
    tree_summary = {}
    for name, pair in pairs.groupby("duke_tree"):
        tree_summary[name] = {
            "edge2_rank_correlation_min": float(pair.edge2_rank_correlation.min()),
            "edge2_rank_correlation_median": float(pair.edge2_rank_correlation.median()),
            "edge2_rank_correlation_max": float(pair.edge2_rank_correlation.max()),
            "threatened_top100_overlap_min": int(pair.threatened_top100_overlap.min()),
            "threatened_top100_overlap_median": float(pair.threatened_top100_overlap.median()),
            "threatened_top100_overlap_max": int(pair.threatened_top100_overlap.max()),
            "top100_sizes": sorted(set(pair.upham_top100_size) | set(pair.duke_top100_size)),
        }
    taxonomy = pd.read_csv(sources["upham-taxonomy"]["path"])
    excluded_ref_species = taxonomy[taxonomy["extinct."] != 0].Species_Name
    excluded_ref_species = [
        name for name in excluded_ref_species if normalize_scientific_name(name) in ref_by_key
    ]
    report = {
        "schema_version": 1,
        "created_at": iso_now(),
        "status": "matched_input_calculators_agree_tree_effects_remain_experimental",
        "engine_commit": config["engine_commit"],
        "reference_commit": config["reference_commit"],
        "seed": args.seed,
        "calculator_cases": python_checks,
        "calculator_cases_passed": True,
        "species_case_records": sum(c["authors_R"]["species"] for c in python_checks),
        "shared_reference_species": len(common),
        "upham_source_trees": len(upham_paths),
        "risk_policy": {
            "point_probabilities": MEDIAN_RISK,
            "tree_comparison": "Same published category-median vector on every tree; no risk randomisation",
        },
        "mapping_audits": {**mapping_audits, "upham": upham_audit},
        "tree_comparison": tree_summary,
        "author_extinct_tips_present_in_2023_reference": excluded_ref_species,
        "crown_preparation": crown_receipts,
        "sources": {
            **sources,
            **core,
            **birth,
            "benchmark_config": receipt(args.config),
            "prototype_config": receipt(core_config_path),
            "engine_empirical_risks": risk_receipt,
            "reviewed_species": receipt(reviewed_path),
            "reviewed_report": receipt(args.reviewed_dir / "report.json"),
        },
        "prepared_trees": {p.name: receipt(p) for p in upham_paths},
        "code": {
            str(p.relative_to(project)): receipt(p)
            for p in [
                Path(__file__),
                project / "ark_pipeline/edge_benchmark.py",
                project / "ark_pipeline/edge.py",
                project / "scripts/edge_prepare_upham.R",
                project / "scripts/edge_reference_benchmark.R",
            ]
        },
        "limitations": [
            "The unmodified authors' EDGE2 score calculator is checked; their stochastic GE2 risk generator is not independently reproduced.",
            "The 100-tree author sample is not Gumbs' exact 1,000 trees augmented with 421 species. This is not a reproduction of published scores or rankings.",
            "The tree comparison prunes every tree to identical uniquely mapped species, preserving branch lengths and excluding the crown stem. This deliberately changes ED2's cohort; it is a diagnostic, not the complete-clade production policy.",
            "Author taxonomy flags exclude labelled extinct mammals and the specified reptile outgroup. Any excluded names present in the reference are recorded and remain outside the shared cohort.",
            "Published 2020 categories are held fixed. Unmapped tips use pooled .232 median risk in full-crown calculator cases; these cases test arithmetic, not species prioritisation.",
            "Duke's two median trees may differ in both topology and dates. Differences cannot be attributed to interpolation alone.",
            "The sample comparisons are descriptive, use ties at rank 100, and do not certify official EDGE lists or alter map weights.",
            "Exact-name and reviewed SIS matches establish the diagnostic cohort; correspondence of species concepts across taxonomy versions is not independently reviewed.",
        ],
    }
    report["outputs"] = {p.name: receipt(p) for p in sorted(output.glob("*.parquet"))}
    report["calculator_inputs_and_outputs"] = {
        p.name: receipt(p) for p in sorted(case_dir.iterdir())
    }
    report["R_session"] = receipt(output / "reference_R_session.txt")
    report["preparation_receipt"] = receipt(prepared / "preparation.csv")
    atomic_json(output / "report.json", report)
    text = [
        "# Matched-input mammal EDGE2 benchmark",
        "",
        "Duke's EDGE2 arithmetic agrees with the unmodified original authors' R calculator and an independent branch enumeration on all eight real-tree cases.",
        f"There are {report['species_case_records']:,} species-case comparisons. The fixed-threat tree diagnostic covers {len(common):,} shared mammals on 100 Upham trees and two Duke median trees.",
        "",
    ]
    for name, value in tree_summary.items():
        text.append(
            f"- {name}: median rank correlation {value['edge2_rank_correlation_median']:.3f}; threatened top-100 overlap {value['threatened_top100_overlap_min']}–{value['threatened_top100_overlap_max']} (median {value['threatened_top100_overlap_median']:g})."
        )
    text += [
        "",
        "The calculator works on matched inputs. Tree choice changes rankings even when species and threat probabilities are identical. Full scientific suitability and published-ranking reproduction remain unverified.",
        "",
        *[f"- {item}" for item in report["limitations"]],
    ]
    (output / "report.md").write_text("\n".join(text) + "\n")
    print(
        json.dumps(
            {
                "calculator_agreement": True,
                "species_case_records": report["species_case_records"],
                "shared_species": len(common),
                "tree_comparison": tree_summary,
            },
            indent=2,
        ),
        flush=True,
    )
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("config/edge_benchmark.toml"))
    parser.add_argument("--input-dir", type=Path, default=Path("data/edge/benchmark/inputs"))
    parser.add_argument("--core-input-dir", type=Path, default=Path("data/edge/inputs"))
    parser.add_argument("--reviewed-dir", type=Path, default=Path("data/edge/validation"))
    parser.add_argument("--output-dir", type=Path, default=Path("data/edge/benchmark/results"))
    parser.add_argument("--r-library", type=Path, default=Path("data/edge/benchmark/r-library"))
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--download", action="store_true")
    run(parser.parse_args())


if __name__ == "__main__":
    main()
