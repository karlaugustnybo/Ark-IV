"""Independent branch enumeration and matched-input EDGE2 benchmarks."""

from __future__ import annotations

import math
import re
from collections import Counter

import numpy as np
import pandas as pd


def validate_tree(tree):
    leaves = list(tree.leaves())
    if len(leaves) < 2 or len({leaf.name for leaf in leaves}) != len(leaves):
        raise ValueError("Tree requires at least two uniquely named tips")
    if tree.dist != 0:
        raise ValueError("Benchmark requires an explicitly excluded crown stem")
    if any(not math.isfinite(node.dist) or node.dist < 0 for node in tree.traverse()):
        raise ValueError("Invalid branch length")
    return leaves


def benchmark_newick(tree) -> str:
    """Round-trip branch lengths without ETE's default six-digit formatting."""
    validate_tree(tree)
    parts = {}
    for node in tree.traverse("postorder"):
        if node.is_leaf:
            if re.search(r"[\s(),:;\[\]'\"]", node.name):
                raise ValueError("Benchmark tip needs unsupported Newick quoting")
            content = node.name
        else:
            content = "(" + ",".join(parts[child] for child in node.children) + ")"
        parts[node] = content + ":" + format(node.dist, ".17g")
    return parts[tree] + ";\n"


def fixed_engine_scores(tree, risks: dict[str, float]) -> pd.DataFrame:
    from dated_complete_tree import tree_metrics as engine

    leaves = validate_tree(tree)
    if set(risks) != {leaf.name for leaf in leaves}:
        raise ValueError("Risk vector and tree tips differ")
    if any(not math.isfinite(p) or p <= 0 or p > 1 for p in risks.values()):
        raise ValueError("Duke benchmark requires positive probabilities no greater than one")
    for leaf in leaves:
        leaf.add_prop("pext", risks[leaf.name])
    engine.compute_edge2_scores(tree)
    return pd.DataFrame(
        [
            {
                "species": leaf.name,
                "pext": risks[leaf.name],
                "terminal_myr": float(leaf.dist),
                "ed2_myr": float(leaf.props["ed2_score"]),
                "edge2_myr": float(leaf.props["edge2_score"]),
            }
            for leaf in leaves
        ]
    )


def independent_branch_scores(tree, risks: dict[str, float]) -> pd.DataFrame:
    """Enumerate every branch and multiply the OTHER descendants' risks.

    Prefix/suffix products avoid division by the focal risk. This does not call
    Duke's descendant products or ancestral recurrence, and supports zero risk.
    """
    leaves = validate_tree(tree)
    if set(risks) != {leaf.name for leaf in leaves}:
        raise ValueError("Risk vector and tree tips differ")
    if any(not math.isfinite(p) or not 0 <= p <= 1 for p in risks.values()):
        raise ValueError("Invalid extinction probability")
    indices = {leaf.name: i for i, leaf in enumerate(leaves)}
    scores = np.zeros(len(leaves))
    for branch in tree.traverse():
        if branch is tree or branch.dist == 0:
            continue
        descendants = [leaf.name for leaf in branch.leaves()]
        probabilities = np.array([risks[name] for name in descendants])
        prefix = np.concatenate(([1.0], np.cumprod(probabilities[:-1])))
        suffix = np.concatenate((np.cumprod(probabilities[:0:-1])[::-1], [1.0]))
        scores[[indices[name] for name in descendants]] += branch.dist * prefix * suffix
    return pd.DataFrame(
        [
            {
                "species": leaf.name,
                "pext": risks[leaf.name],
                "ed2_myr": float(scores[i]),
                "edge2_myr": float(scores[i] * risks[leaf.name]),
            }
            for i, leaf in enumerate(leaves)
        ]
    )


def agreement(
    duke: pd.DataFrame, other: pd.DataFrame, *, atol=1e-10, rtol=1e-10
) -> tuple[pd.DataFrame, dict]:
    if (
        not duke.species.is_unique
        or not other.species.is_unique
        or set(duke.species) != set(other.species)
    ):
        raise ValueError("Calculator comparison requires identical unique species")
    merged = duke.merge(
        other, on="species", validate="one_to_one", suffixes=("_duke", "_reference")
    )
    summary = {"species": len(merged), "absolute_tolerance": atol, "relative_tolerance": rtol}
    for component in ["ed2", "edge2"]:
        x, y = merged[f"{component}_myr_duke"], merged[f"{component}_myr_reference"]
        if not np.isfinite(x).all() or not np.isfinite(y).all():
            raise ValueError("Nonfinite calculator scores")
        merged[f"{component}_absolute_error_myr"] = (x - y).abs()
        summary[f"{component}_max_absolute_error_myr"] = float((x - y).abs().max())
        summary[f"{component}_mismatches"] = int((~np.isclose(x, y, atol=atol, rtol=rtol)).sum())
    summary["passed"] = summary["ed2_mismatches"] == summary["edge2_mismatches"] == 0
    return merged, summary


def normalize_scientific_name(name: str) -> str:
    return " ".join(re.sub(r"_ott\d+$", "", name).replace("_", " ").lower().split())


def reference_tip_map(
    tree, reference: list[dict], reviewed: pd.DataFrame | None = None
) -> tuple[dict[str, str], dict]:
    ref = pd.DataFrame(reference)
    names = {normalize_scientific_name(row["reference_name"]): row for row in reference}
    if len(names) != len(reference):
        raise ValueError("Reference scientific names are not unique")
    sis = (
        ref[ref.iucn_sis_id.notna() & ~ref.iucn_sis_id.duplicated(keep=False)]
        .set_index("iucn_sis_id")
        .reference_name.to_dict()
    )
    reviewed_by_tip = (
        {}
        if reviewed is None
        else reviewed[reviewed.join_status == "scored"].set_index("tree_tip").iucn_sis_id.to_dict()
    )
    proposals, methods = {}, {}
    for leaf in tree.leaves():
        key = normalize_scientific_name(leaf.name)
        if leaf.name in reviewed_by_tip and reviewed_by_tip[leaf.name] in sis:
            key = normalize_scientific_name(sis[reviewed_by_tip[leaf.name]])
            method = "reviewed_sis_identity"
        else:
            method = "unique_exact_reference_name"
        if key in names:
            proposals[leaf.name] = key
            methods[leaf.name] = method
    counts = Counter(proposals.values())
    mapping = {tip: key for tip, key in proposals.items() if counts[key] == 1}
    return mapping, {
        "reference_mapped_tips": len(mapping),
        "unmapped_or_ambiguous_tips": len(tree) - len(mapping),
        "ambiguous_reference_assignments": sum(counts[key] > 1 for key in proposals.values()),
        "methods": dict(Counter(methods[tip] for tip in mapping)),
        "tip_mappings": [
            {"tree_tip": tip, "reference_key": key, "method": methods[tip]}
            for tip, key in mapping.items()
        ],
    }


def empirical_risks(
    risk_rows: list[list[str]], keys: list[str], categories: dict[str, str], seed: int, draws: int
) -> np.ndarray:
    if not risk_rows or draws < 1 or len(keys) != len(set(keys)):
        raise ValueError("Require nonempty risks, positive draw count and unique species")
    lookup = {
        category: np.array([float(row[1]) for row in risk_rows if row[0] == category])
        for category in {row[0] for row in risk_rows}
    }
    pooled = np.array([float(row[1]) for row in risk_rows])
    if not np.isfinite(pooled).all() or (pooled <= 0).any() or (pooled > 1).any():
        raise ValueError("Invalid empirical extinction probabilities")
    uniforms = np.random.default_rng(seed).random((draws, len(keys)))
    return np.column_stack(
        [
            values[(uniforms[:, i] * len(values)).astype(int)]
            for i, key in enumerate(keys)
            if len(
                values := lookup.get(
                    "CR" if categories.get(key) == "EW" else categories.get(key), pooled
                )
            )
        ]
    )
