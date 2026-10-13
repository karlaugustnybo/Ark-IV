"""Rooted marginal branch coverage for an experimental sequencing priority."""

from __future__ import annotations

import math

import pandas as pd

from ark_pipeline.edge_benchmark import validate_tree
from ark_pipeline.edge_matching import normalize_name


def genome_evidence(goat: pd.DataFrame) -> pd.DataFrame:
    """Preserve the website rule and expose assembly evidence separately.

    Taxon aggregates may describe different assemblies; this pilot cannot check
    co-occurrence, QV, contamination, or inherited field provenance. Samples and
    project progress count under the website rule but not the assembly proxy.
    """
    if not goat.taxon_id.is_unique:
        raise ValueError("Duplicate GoaT taxon IDs")
    result = goat.copy()
    result["assembly_reported"] = result.assembly_level.map(normalize_name).ne("")
    result["busco_numeric"] = pd.to_numeric(result.busco_completeness, errors="coerce")
    result["qualifying_genome_proxy"] = result.assembly_level.map(normalize_name).isin(
        {"chromosome", "complete genome"}
    ) & result.busco_numeric.between(90, 100)
    result["sample_reported"] = result.sample_acquired.notna() & result.sample_acquired.ne(
        ""
    ).fillna(False)
    result["project_reported"] = result.in_progress.notna() & result.in_progress.ne("").fillna(
        False
    )
    result["resampling_reported"] = result.resampling_required.map(normalize_name).ne("")
    result["legacy_broad_evidence"] = (
        (
            result.assembly_level.fillna("").str.lower().isin({"chromosome", "complete genome"})
            & result.busco_numeric.ge(90)
        )
        | result.sample_reported
        | result.project_reported
        | (result.ebp_standard_criteria.notna() & result.ebp_standard_criteria.ne("").fillna(False))
    )
    result["genome_evidence_state"] = "no_qualifying_genome_recorded"
    result.loc[result.assembly_reported, "genome_evidence_state"] = "assembly_without_quality_proxy"
    result.loc[result.qualifying_genome_proxy, "genome_evidence_state"] = "qualifying_genome_proxy"
    return result


def marginal_gaps(tree, sequenced: set[str]) -> pd.DataFrame:
    """PD(S union {s}) - PD(S), rooted at an explicitly stem-free crown.

    Retain all tree tips. Mark each branch covered when it has a represented
    descendant; then accumulate uncovered lengths along each root-to-tip path.
    Two distance passes also resolve the nearest represented tip, with
    lexical tip labels breaking exact distance ties reproducibly.
    """
    leaves = validate_tree(tree)
    if not sequenced.issubset({leaf.name for leaf in leaves}):
        raise ValueError("Sequenced baseline contains a tip outside the tree")
    covered, inside = {}, {}
    for node in tree.traverse("postorder"):
        covered[node] = (
            node.name in sequenced if node.is_leaf else any(covered[c] for c in node.children)
        )
        options = [
            (inside[c][0] + c.dist, inside[c][1]) for c in node.children if inside[c] is not None
        ]
        inside[node] = (
            (0.0, node.name)
            if node.is_leaf and node.name in sequenced
            else min(options, default=None)
        )
    gap, outside = {tree: 0.0}, {tree: None}
    for node in tree.traverse("preorder"):
        best = sorted(
            (inside[c][0] + c.dist, inside[c][1], i)
            for i, c in enumerate(node.children)
            if inside[c] is not None
        )[:2]
        for i, child in enumerate(node.children):
            gap[child] = 0.0 if covered[child] else gap[node] + child.dist
            choices = [outside[node]] if outside[node] is not None else []
            choices += [(d, name) for d, name, index in best if index != i]
            other = min(choices, default=None)
            outside[child] = None if other is None else (other[0] + child.dist, other[1])
    records = []
    for leaf in leaves:
        nearest = min([v for v in (inside[leaf], outside[leaf]) if v is not None], default=None)
        records.append(
            dict(
                tree_tip=leaf.name,
                website_dna_on_tip=leaf.name in sequenced,
                genomic_gap_myr=gap[leaf],
                nearest_dna_tip=nearest[1] if nearest else None,
                nearest_dna_distance_myr=nearest[0] if nearest else None,
            )
        )
    return pd.DataFrame(records)


def rooted_pd(tree, selected: set[str]) -> float:
    """Independent path-union oracle for tests and scoped result checks."""
    leaves = {leaf.name: leaf for leaf in validate_tree(tree)}
    if not selected.issubset(leaves):
        raise ValueError("Selected tip outside tree")
    branches = set()
    for name in selected:
        node = leaves[name]
        while node is not tree:
            branches.add(node)
            node = node.up
    return math.fsum(node.dist for node in branches)


def greedy_targets(tree, sequenced, candidates: pd.DataFrame, budget: int):
    """A threat-weighted greedy heuristic; recompute gain after each choice."""
    if budget < 0 or not candidates.tree_tip.is_unique:
        raise ValueError("Invalid selection budget or competing candidate tips")
    baseline = set(sequenced)
    remaining = candidates[["iucn_sis_id", "tree_tip", "threat_weight"]].copy()
    chosen = []
    for step in range(budget):
        current = marginal_gaps(tree, baseline)[["tree_tip", "genomic_gap_myr"]]
        ranked = remaining.merge(current, on="tree_tip", validate="one_to_one")
        ranked["selection_priority"] = ranked.threat_weight * ranked.genomic_gap_myr
        ranked = ranked[ranked.selection_priority > 0].sort_values(
            ["selection_priority", "iucn_sis_id"], ascending=[False, True]
        )
        if ranked.empty:
            break
        choice = ranked.iloc[0].to_dict()
        chosen.append({"step": step + 1, **choice})
        baseline.add(choice["tree_tip"])
        remaining = remaining[remaining.tree_tip.ne(choice["tree_tip"])]
    return pd.DataFrame(
        chosen,
        columns=[
            "step",
            "iucn_sis_id",
            "tree_tip",
            "threat_weight",
            "genomic_gap_myr",
            "selection_priority",
        ],
    )
