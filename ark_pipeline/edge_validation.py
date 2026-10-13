"""Inspectable diagnostics and descriptive stability summaries for the mammal pilot."""

from __future__ import annotations

import hashlib

import numpy as np
import pandas as pd


def topology_signature(tree) -> str:
    """Order-independent rooted topology identity, ignoring dates/internal labels."""
    digests = {}
    for node in tree.traverse("postorder"):
        payload = (
            ("tip:" + node.name).encode()
            if node.is_leaf
            else b"node:" + b"".join(sorted(digests[child] for child in node.children))
        )
        digests[node] = hashlib.sha256(payload).digest()
    return digests[tree].hex()


def align_mammal_crown(tree, expected: set[str], metadata: pd.DataFrame) -> list[str]:
    actual = {leaf.name for leaf in tree.leaves()}
    if expected - actual:
        raise ValueError("Alternative tree is missing original mammal tips")
    extra = actual - expected
    by_tip = metadata.set_index("tree_tip")
    for tip in extra:
        if (
            tip not in by_tip.index
            or not str(by_tip.loc[tip, "taxon_name"]).endswith("environmental sample")
            or pd.notna(by_tip.loc[tip, "ott_class"])
        ):
            raise ValueError(f"Unexpected extra mammal tip: {tip}")
    if extra:
        tree.prune(sorted(expected), preserve_branch_length=True)
        tree.add_prop("excluded_environmental_crown_stem_myr", float(tree.dist))
        tree.dist = 0
    return sorted(extra)


def scored_species(joined: list[dict], scores: list[dict]) -> pd.DataFrame:
    by_tip = {row["tree_tip"]: row for row in scores}
    records = []
    for original in joined:
        row = dict(original)
        if row["join_status"] == "joined":
            if row["redlist_category"] == "EX":
                row["join_status"] = "excluded_extinct"
            else:
                row.update(by_tip[row["tree_tip"]], join_status="scored")
        records.append(row)
    return pd.DataFrame(records)


def rank_tree(species: pd.DataFrame, tree_id: str, group: str) -> pd.DataFrame:
    frame = species[species.join_status == "scored"].copy()
    frame["tree_id"] = tree_id
    frame["tree_group"] = group
    frame["assessed_rank"] = frame.ark_edge2_myr_median.rank(ascending=False, method="min")
    threatened = frame.redlist_category.isin(["CR", "EN", "VU"])
    frame["threatened_rank"] = np.nan
    frame.loc[threatened, "threatened_rank"] = frame.loc[threatened, "ark_edge2_myr_median"].rank(
        ascending=False, method="min"
    )
    return frame


def stability_summary(results: pd.DataFrame, baseline: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Pilot ranges, never population confidence intervals or official EDGE lists."""
    base = baseline.set_index("iucn_sis_id")
    if results.duplicated(["tree_id", "iucn_sis_id"]).any() or not base.index.is_unique:
        raise ValueError("Duplicate species within a tree comparison")
    summaries = []
    details = {}
    for group, frame in results.groupby("tree_group", sort=True):
        tree_ids = frame.tree_id.unique()
        expected = set(base.index)
        if any(set(part.iucn_sis_id) != expected for _, part in frame.groupby("tree_id")):
            raise ValueError("Alternative trees must score the same assessed species cohort")
        for sis, part in frame.groupby("iucn_sis_id"):
            row = base.loc[sis]
            summaries.append(
                {
                    "iucn_sis_id": sis,
                    "iucn_scientific_name": row.iucn_scientific_name,
                    "redlist_category": row.redlist_category,
                    "tree_group": group,
                    "trees": len(part),
                    "rank_min": float(part.assessed_rank.min()),
                    "rank_max": float(part.assessed_rank.max()),
                    "rank_median": float(part.assessed_rank.median()),
                    "baseline_rank": float(row.assessed_rank),
                    "edge2_min_myr": float(part.ark_edge2_myr_median.min()),
                    "edge2_max_myr": float(part.ark_edge2_myr_median.max()),
                    "terminal_min_myr": float(part.terminal_branch_myr.min()),
                    "terminal_max_myr": float(part.terminal_branch_myr.max()),
                    "top100_threatened_trees": int((part.threatened_rank <= 100).sum()),
                }
            )
        base_top = set(base[base.threatened_rank <= 100].index)
        overlaps = [
            len(base_top & set(part.loc[part.threatened_rank <= 100, "iucn_sis_id"]))
            for _, part in frame.groupby("tree_id")
        ]
        rank_correlations = [
            float(
                base.assessed_rank.rank().corr(part.set_index("iucn_sis_id").assessed_rank.rank())
            )
            for _, part in frame.groupby("tree_id")
        ]
        # Average tied ranks here give a Spearman comparison; display ranks use min.
        details[group] = {
            "trees": len(tree_ids),
            "baseline_top100_threatened_size": len(base_top),
            "top100_threatened_overlap_min": min(overlaps),
            "top100_threatened_overlap_max": max(overlaps),
            "rank_correlation_min": min(rank_correlations),
            "rank_correlation_max": max(rank_correlations),
        }
    summary = pd.DataFrame(summaries)
    for group in details:
        part = summary[summary.tree_group == group]
        details[group]["always_top100_threatened"] = int(
            (part.top100_threatened_trees == part.trees).sum()
        )
        details[group]["median_rank_range"] = float((part.rank_max - part.rank_min).median())
        details[group]["max_rank_range"] = float((part.rank_max - part.rank_min).max())
    return summary, details


def outlier_diagnostics(comparison: pd.DataFrame, stability: pd.DataFrame) -> pd.DataFrame:
    frame = comparison.copy()
    frame["terminal_ratio_to_reference"] = (
        frame.terminal_branch_myr / frame.reference_terminal_myr.replace(0, np.nan)
    )
    frame["terminal_fraction_of_ed2"] = (
        frame.terminal_branch_myr / frame.ark_ed2_myr_median.replace(0, np.nan)
    )
    frame["terminal_difference_myr"] = frame.terminal_branch_myr - frame.reference_terminal_myr
    frame["edge2_relative_to_reference"] = (
        frame.ark_edge2_myr_median / frame.reference_edge2_myr.replace(0, np.nan)
    )
    # Assess rank differences within the same-category cohort, not across unequal cohorts.
    same = frame.category_agrees
    for component in ["ed2", "edge2"]:
        frame[f"{component}_same_category_rank_delta"] = np.nan
        frame.loc[same, f"{component}_same_category_rank_delta"] = frame.loc[
            same, f"ark_{component}_myr_median"
        ].rank(ascending=False) - frame.loc[same, f"reference_{component}_myr"].rank(
            ascending=False
        )
    frame["diagnostic"] = np.where(
        ~same,
        "threat category changed; compare the same-category cohort",
        np.where(
            (frame.terminal_ratio_to_reference >= 5) | (frame.terminal_ratio_to_reference <= 0.2),
            "terminal branch differs by at least fivefold; tree dating/placement is a plausible contributor",
            "inspect shared branches, tree placement and risk assumptions",
        ),
    )
    groups = ["birth-topology", "birth-dates"]
    for group in groups:
        part = stability[stability.tree_group == group].set_index("iucn_sis_id")
        for column in ["rank_min", "rank_max", "terminal_min_myr", "terminal_max_myr"]:
            frame[group.replace("-", "_") + "_" + column] = frame.iucn_sis_id.map(part[column])
    return frame.sort_values("edge2_residual_myr", key=lambda values: values.abs(), ascending=False)
