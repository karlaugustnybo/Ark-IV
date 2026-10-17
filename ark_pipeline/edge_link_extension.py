"""Independent IUCN-to-tree identity checks, separate from genomic transfer."""

from __future__ import annotations

import re
from collections import Counter

import pandas as pd

from ark_pipeline.edge_matching import normalize_name


def extend_iucn_links(
    baseline: pd.DataFrame,
    crosswalk: pd.DataFrame,
    iucn_taxonomy: pd.DataFrame,
    candidates: pd.DataFrame,
):
    """Recover only unique exact species names with three compatible lineage ranks.

    Original accepted links are reserved. Fresh source taxonomy, assessment/SIS
    agreement and global species scope are mandatory. This does not modify or
    make assertions about an unresolved IUCN-to-GoaT/NCBI genomic-data crosswalk.
    """
    if not baseline.iucn_sis_id.is_unique or not crosswalk.iucn_sis_id.is_unique:
        raise ValueError("Expected one row per assessment species")
    original = baseline[baseline.join_status == "linked"]
    if not original.tree_tip.is_unique:
        raise ValueError("Original links are not unique")
    result = baseline.copy()
    if "original_join_status" not in result:
        result["original_join_status"] = result.join_status
    if "ncbi_to_tree_link_verified" not in result:
        result["ncbi_to_tree_link_verified"] = result.join_status.eq("linked")
    # Preserve the upstream transfer decision; independent tree links do not
    # authorize joining or transferring any GoaT genomic attributes.
    if "tree_identity_verified" not in result:
        result["tree_identity_verified"] = result.join_status.eq("linked")
    if not result.tree_identity_verified.equals(result.join_status.eq("linked")):
        raise ValueError("Baseline identity verification disagrees with link status")
    cw = crosswalk.set_index("iucn_sis_id").to_dict("index")
    tax = iucn_taxonomy.copy()
    tax["iucn_sis_id"] = pd.to_numeric(tax.internalTaxonId, errors="raise").astype("int64")
    ambiguous_taxa = set(tax.loc[tax.iucn_sis_id.duplicated(keep=False), "iucn_sis_id"])
    tax = tax[~tax.iucn_sis_id.isin(ambiguous_taxa)].set_index("iucn_sis_id").to_dict("index")
    name_counts = candidates.normalized_name.value_counts().to_dict()
    lookup = {name: frame for name, frame in candidates.groupby("normalized_name")}
    reserved = set(original.tree_tip)
    audit, proposals = [], {}
    for row in result[result.join_status != "linked"].to_dict("records"):
        sis = row["iucn_sis_id"]
        fresh = tax.get(sis)
        source = cw[sis]
        base = {
            "iucn_sis_id": sis,
            "iucn_scientific_name": row["iucn_scientific_name"],
            "original_join_status": row["join_status"],
            "upstream_match_status": source["match_status"],
            "tree_tip": None,
            "reason": None,
            "accepted": False,
        }
        scopes = row.get("scopes")
        valid_assessment = str(row.get("assessment_sis_id")) == str(sis) and bool(
            re.search(r"\bGlobal\b", scopes if isinstance(scopes, str) else "", re.I)
        )
        if (
            not valid_assessment
            or fresh is None
            or normalize_name(fresh["scientificName"])
            != normalize_name(row["iucn_scientific_name"])
        ):
            audit.append({**base, "reason": "assessment_or_taxonomy_identity_conflict"})
            continue
        if (
            normalize_name(fresh.get("infraType"))
            or normalize_name(fresh.get("subpopulationName"))
            or "subpopulation" in row["iucn_scientific_name"].lower()
        ):
            audit.append({**base, "reason": "not_global_species_taxon"})
            continue
        if (
            row["safe_for_automatic_species_trait_transfer"] is not True
            and source["match_status"] == "MATCHED"
        ):
            audit.append({**base, "reason": "unsafe_existing_ncbi_species_concept"})
            continue
        name = normalize_name(fresh["scientificName"])
        frame = lookup.get(name)
        if frame is None:
            audit.append({**base, "reason": "no_exact_species_leaf"})
            continue
        for tip in frame.to_dict("records"):
            record = {
                **base,
                "tree_tip": tip["tree_tip"],
                "ott_id": tip["ott_id"],
                "name_candidates": name_counts[name],
                "label_occurrences": tip.get("label_occurrences", 1),
                "documented_genus_change": bool(tip.get("documented_genus_change", False)),
                "identity_basis": tip.get("identity_basis", "exact_ott_name"),
            }
            agree, conflict = [], []
            ancestors = {normalize_name(value) for value in tip.get("ott_ancestor_names", [])}
            membership_ranks = []
            for rank in ["genus", "family", "order", "class"]:
                a, b = (
                    normalize_name(fresh.get(rank + "Name")),
                    normalize_name(tip.get("ott_" + rank)),
                )
                record["iucn_" + rank], record["ott_" + rank] = a or None, b or None
                if rank in {"order", "class"} and a and a in ancestors:
                    agree.append(rank)
                    if a != b:
                        membership_ranks.append(rank)
                elif rank == "genus" and a and b and a != b and record["documented_genus_change"]:
                    # A scoped alias stage may supply a documented binomial
                    # recombination. This does not assert equivalent genera;
                    # family, order and class must then supply three matches.
                    pass
                elif a and b:
                    (agree if a == b else conflict).append(rank)
            record["agreeing_ranks"] = ",".join(agree)
            record["conflicting_ranks"] = ",".join(conflict)
            record["ancestor_membership_ranks"] = ",".join(membership_ranks)
            reason = "eligible"
            if tip.get("label_occurrences", 1) != 1:
                reason = "repeated_tree_label"
            elif name_counts[name] != 1:
                reason = "ambiguous_exact_tree_name"
            elif normalize_name(tip.get("candidate_block_reason")):
                reason = normalize_name(tip["candidate_block_reason"])
            elif conflict:
                reason = "lineage_conflict"
            elif (
                ("genus" not in agree and not record["documented_genus_change"])
                or "family" not in agree
                or len(agree) < 3
            ):
                reason = "insufficient_lineage_evidence"
            elif tip["tree_tip"] in reserved:
                reason = "reserved_existing_tree_tip"
            record["reason"] = reason
            audit.append(record)
            if reason == "eligible":
                proposals[sis] = tip["tree_tip"]
    counts = Counter(proposals.values())
    for entry in audit:
        if entry["reason"] == "eligible":
            if counts[entry["tree_tip"]] != 1:
                entry["reason"] = "competing_iucn_species"
            else:
                entry["accepted"] = True
    accepted = {entry["iucn_sis_id"]: entry["tree_tip"] for entry in audit if entry["accepted"]}
    for index, row in result.iterrows():
        if row.iucn_sis_id in accepted:
            result.loc[
                index, ["tree_tip", "join_status", "join_method", "tree_identity_verified"]
            ] = [accepted[row.iucn_sis_id], "linked", "exact_iucn_name_three_lineage_ranks", True]
    result["extant_edge_input_candidate"] = (
        result.tree_identity_verified & result.redlist_category.ne("EX")
    )
    verified = result[result.tree_identity_verified]
    if not verified.tree_tip.is_unique:
        raise ValueError("Extension created competing tree links")
    if (
        not result.loc[
            result.join_method.eq("exact_iucn_name_three_lineage_ranks"),
            "ncbi_to_tree_link_verified",
        ]
        .eq(False)
        .all()
    ):
        raise ValueError("Independent IUCN links cannot assert NCBI tree identity")
    columns = [
        "iucn_sis_id", "iucn_scientific_name", "original_join_status", "upstream_match_status",
        "tree_tip", "reason", "accepted", "ott_id", "name_candidates", "label_occurrences",
        "documented_genus_change", "identity_basis", "agreeing_ranks", "conflicting_ranks",
        "ancestor_membership_ranks",
    ]
    audit_frame = pd.DataFrame(audit).reindex(columns=list(dict.fromkeys(columns + [key for row in audit for key in row])))
    audit_frame["accepted"] = audit_frame.accepted.astype(bool)
    return result, audit_frame
