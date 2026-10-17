"""Review missing GoaT baseline identities without changing the dated tree."""

from collections import Counter

import pandas as pd

from ark_pipeline.edge import canonical_taxid
from ark_pipeline.edge_matching import normalize_name
from ark_pipeline.extinction_status import extinction_status


def review_baseline_links(
    baseline,
    candidates,
    name_targets,
    current_names,
    ranks,
    lineages,
    direct,
    repeated,
    merges,
    crown_tips,
    internal_ott_ids,
    ncbi_name_ids=None,
    iucn_identities=None,
):
    """Accept unique documented binomial identities with agreeing lineage.

    Multiple leaves carrying one current NCBI concept remain unresolved, even
    if one leaf has the preferred name. No grafting, pruning or parent copies.
    """
    if not baseline.taxon_id.is_unique:
        raise ValueError("Expected unique GoaT taxon IDs")
    result = baseline.copy()
    reserved = set(baseline.loc[baseline.tree_join_status.eq("linked"), "tree_tip"])
    if not baseline.loc[baseline.tree_join_status.eq("linked"), "tree_tip"].is_unique:
        raise ValueError("Baseline contains competing leaf identities")
    missing = baseline[baseline.legacy_broad_evidence & baseline.tree_join_status.ne("linked")]
    candidate_groups = {k: g for k, g in candidates.groupby("subject_ncbi_taxid")}
    target_groups = {k: g for k, g in name_targets.groupby("subject_ncbi_taxid")}
    summary, evidence = [], []
    for row in missing.to_dict("records"):
        taxid = row["canonical_ncbi_taxid"]
        name = normalize_name(row["scientific_name"])
        frame = candidate_groups.get(taxid, candidates.iloc[:0])
        targets = target_groups.get(taxid, name_targets.iloc[:0])
        extinct_flags = (
            targets.get("flags", pd.Series(index=targets.index, dtype="str"))
            .fillna("")
            .str.contains(r"(?:^|,)extinct(?:_inherited)?(?:,|$)")
            .any()
        )
        raw_flags = ",".join(
            sorted(
                {
                    flag.strip()
                    for value in targets.get("flags", pd.Series(dtype="str")).dropna()
                    for flag in value.split(",")
                    if flag.strip()
                }
            )
        )
        identity = (iucn_identities or {}).get(taxid, {})
        resolved = extinction_status(identity.get("iucn_category"), raw_flags)
        leaf_count = frame.tree_tip.nunique()
        base = dict(
            taxon_id=row["taxon_id"],
            canonical_ncbi_taxid=taxid,
            scientific_name=row["scientific_name"],
            genus=row["genus"],
            family=row["family"],
            previous_status=row["tree_join_status"],
            current_ncbi_rank=ranks.get(taxid),
            direct_leaf_count=len(direct.get(taxid, set())),
            documented_leaf_count=leaf_count,
            recovered=False,
            tree_tip=None,
            ott_extinction_flags_evidence=raw_flags,
            iucn_sis_id=identity.get("iucn_sis_id"),
            iucn_assessment_id=identity.get("iucn_assessment_id"),
            iucn_scientific_name=identity.get("iucn_scientific_name"),
            iucn_category=identity.get("iucn_category"),
            **resolved,
        )
        reason = None
        if " x " in name or " × " in name:
            reason = "hybrid_not_single_species"
        elif len(name.split()) != 2 or name.split()[1] in {"sp.", "sp", "cf.", "aff."}:
            reason = "unresolved_species_name"
        elif ranks.get(taxid) != "species":
            reason = "not_in_registered_ncbi_species"
        elif name not in current_names.get(taxid, set()):
            reason = "goat_name_not_confirmed_by_current_ncbi"
        elif ncbi_name_ids is not None and ncbi_name_ids.get(name) != {taxid}:
            reason = "ambiguous_current_ncbi_name"
        elif taxid in repeated or len(direct.get(taxid, set())) > 1:
            reason = "multiple_tree_tips_for_current_ncbi_species"
        else:
            ncbi = lineages.get(taxid, {})
            if any(
                not normalize_name(ncbi.get("ncbi_" + rank))
                or normalize_name(ncbi.get("ncbi_" + rank)) != normalize_name(row[rank])
                for rank in ["genus", "family"]
            ):
                reason = "goat_current_ncbi_lineage_conflict"
        if reason is None and leaf_count == 0:
            if targets.ott_id.isin(internal_ott_ids).any():
                reason = "named_taxon_is_internal_tree_node"
            elif extinct_flags:
                reason = (
                    "iucn_status_override_tree_leaf_missing"
                    if resolved["ott_extinction_overridden"]
                    else "ott_extinct_flag_and_no_dated_leaf"
                )
            elif targets.taxon_rank.eq("species").any():
                reason = "ott_species_without_dated_leaf"
            elif not targets.empty:
                reason = "only_nonspecies_ott_name"
            else:
                reason = "no_documented_ott_name"
        if reason is None and (
            leaf_count > 1 or targets.loc[targets.taxon_rank.eq("species"), "ott_id"].nunique() > 1
        ):
            reason = "multiple_documented_species_concepts"
        checked = []
        for tip in frame.drop_duplicates("tree_tip").to_dict("records"):
            agree, conflict = [], []
            accepted_name = normalize_name(tip["taxon_name"])
            old, new = name.split(), accepted_name.split()
            same_epithet = len(old) == len(new) == 2 and old[1] == new[1]
            ancestors = {normalize_name(v) for v in tip["ott_ancestor_names"]}
            for rank in ["genus", "family", "order", "class"]:
                source, target = normalize_name(row[rank]), normalize_name(tip.get("ott_" + rank))
                if source and rank in {"order", "class"} and source in ancestors:
                    agree.append(rank)
                elif source and target:
                    if source == target:
                        agree.append(rank)
                    elif rank != "genus" or not same_epithet:
                        conflict.append(rank)
            source_ids = {
                canonical_taxid(s[5:], merges)
                for s in str(tip.get("sourceinfo") or "").split(",")
                if s.startswith("ncbi:")
            }
            block = reason
            if block is None and (
                tip["tree_tip"] not in crown_tips or tip["label_occurrences"] != 1
            ):
                block = "outside_crown_or_repeated_label"
            elif block is None and tip["taxon_rank"] != "species":
                block = "not_ott_species_leaf"
            elif block is None and source_ids and taxid not in source_ids:
                block = "current_ncbi_tree_identity_conflict"
            elif block is None and accepted_name != name and not same_epithet:
                block = "species_concept_alias_needs_review"
            elif block is None and conflict:
                block = "tree_lineage_conflict"
            elif block is None and (
                "family" not in agree
                or len(agree) < 3
                or ("genus" not in agree and not same_epithet)
            ):
                block = "insufficient_lineage_evidence"
            elif block is None and tip["tree_tip"] in reserved:
                block = "reserved_baseline_tip"
            checked.append(
                dict(
                    **base,
                    candidate_tip=tip["tree_tip"],
                    ott_id=tip["ott_id"],
                    taxon_name=tip["taxon_name"],
                    identity_basis=tip["identity_basis"],
                    queried_names=tip["queried_names"],
                    agreeing_ranks=",".join(agree),
                    conflicting_ranks=",".join(conflict),
                    source_ncbi_ids=",".join(sorted(source_ids)),
                    candidate_reason=block or "eligible",
                    accepted=False,
                )
            )
        evidence.extend(checked)
        if reason is None:
            eligible = [v for v in checked if v["candidate_reason"] == "eligible"]
            if len(eligible) == 1:
                base.update(recovered=True, tree_tip=eligible[0]["candidate_tip"])
                reason = "recovered_documented_name_lineage"
            else:
                reason = checked[0]["candidate_reason"] if checked else "no_documented_ott_name"
        summary.append({**base, "review_reason": reason})
    counts = Counter(r["tree_tip"] for r in summary if r["recovered"])
    for row in summary:
        if row["recovered"] and counts[row["tree_tip"]] != 1:
            row.update(recovered=False, tree_tip=None, review_reason="competing_goat_species")
        if row["recovered"]:
            mask = result.taxon_id.eq(row["taxon_id"])
            result.loc[mask, ["tree_tip", "tree_join_status", "tree_join_route"]] = [
                row["tree_tip"],
                "linked",
                "reviewed_goat_name_lineage",
            ]
    recovered_ids = {r["taxon_id"] for r in summary if r["recovered"]}
    for row in evidence:
        row["accepted"] = row["taxon_id"] in recovered_ids and row["candidate_reason"] == "eligible"
    summary_columns = [
        "taxon_id", "canonical_ncbi_taxid", "scientific_name", "genus", "family",
        "previous_status", "current_ncbi_rank", "direct_leaf_count", "documented_leaf_count",
        "recovered", "tree_tip", "ott_extinction_flags_evidence", "ott_extinct_flag",
        "effective_extinct", "extinction_status_source", "extinction_status_conflict",
        "ott_extinction_overridden", "review_reason", "iucn_sis_id", "iucn_assessment_id",
        "iucn_scientific_name", "iucn_category",
    ]
    evidence_columns = summary_columns + [
        "candidate_tip", "ott_id", "taxon_name", "identity_basis", "queried_names",
        "agreeing_ranks", "conflicting_ranks", "source_ncbi_ids", "candidate_reason", "accepted",
    ]
    summary_frame = pd.DataFrame(summary) if summary else pd.DataFrame(columns=summary_columns)
    evidence_frame = pd.DataFrame(evidence) if evidence else pd.DataFrame(columns=evidence_columns)
    for column in ["recovered", "ott_extinction_overridden"]:
        summary_frame[column] = summary_frame[column].astype(bool)
    evidence_frame["accepted"] = evidence_frame.accepted.astype(bool)
    return result, summary_frame, evidence_frame


def apply_reviewed_baseline(current, reviewed, review):
    """Transfer only reviewed link columns onto freshly checked GoaT evidence."""
    if (
        not current.taxon_id.is_unique
        or not reviewed.taxon_id.is_unique
        or not review.taxon_id.is_unique
    ):
        raise ValueError("Duplicate baseline-review identities")
    before = current.set_index("taxon_id")
    after = reviewed.set_index("taxon_id")
    if set(before.index) != set(after.index):
        raise ValueError("Review baseline species scope differs")
    after = after.loc[before.index]
    for column in ["canonical_ncbi_taxid", "scientific_name", "legacy_broad_evidence"]:
        if not before[column].equals(after[column]):
            raise ValueError("Review differs from fresh GoaT evidence: " + column)
    recovered = review[review.recovered].set_index("taxon_id")
    result = current.copy().set_index("taxon_id")
    approved = set(recovered.index)
    for taxid in before.index:
        old, new = before.loc[taxid], after.loc[taxid]
        if taxid in approved:
            if (
                old.tree_join_status == "linked"
                or not old.legacy_broad_evidence
                or new.tree_join_status != "linked"
                or new.tree_join_route != "reviewed_goat_name_lineage"
                or new.tree_tip != recovered.loc[taxid, "tree_tip"]
            ):
                raise ValueError("Invalid recovered baseline witness")
            result.loc[taxid, ["tree_tip", "tree_join_status", "tree_join_route"]] = [
                new.tree_tip,
                new.tree_join_status,
                new.tree_join_route,
            ]
        elif not old[["tree_tip", "tree_join_status", "tree_join_route"]].equals(
            new[["tree_tip", "tree_join_status", "tree_join_route"]]
        ):
            raise ValueError("Review changed a baseline link without approval")
    return result.reset_index()
