"""Conservative name/lineage bridges for taxonomies carrying older NCBI identifiers."""

from __future__ import annotations

import json
import re
import shutil
import tarfile
from pathlib import Path

import duckdb
import pandas as pd

from ark_pipeline.edge import canonical_taxid
from ark_pipeline.runtime.provenance import atomic_json, sha256


def normalize_name(value) -> str:
    if value is None or pd.isna(value):
        return ""
    return " ".join(str(value).lower().strip().split())


def build_ott_index(archive: Path, output: Path) -> Path:
    receipt_path = output.with_suffix(".receipt.json")
    identity = {"schema_version": 2, "archive_sha256": sha256(archive)}
    if output.is_file() and receipt_path.is_file():
        receipt = json.loads(receipt_path.read_text())
        if receipt.get("input") == identity:
            if sha256(output) != receipt["output_sha256"]:
                raise ValueError("OTT index checksum changed")
            return output
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(".taxonomy.part")
    try:
        with tarfile.open(archive) as tar:
            members = [item for item in tar.getmembers() if item.name.endswith("/taxonomy.tsv")]
            if len(members) != 1:
                raise ValueError("Expected one OTT taxonomy.tsv")
            with tar.extractfile(members[0]) as source, temporary.open("wb") as target:
                shutil.copyfileobj(source, target)
        with duckdb.connect() as connection:
            connection.execute("SET memory_limit='1GB'")
            columns = {
                "uid": "ott_id",
                "parent_uid": "parent_id",
                "name": "taxon_name",
                "rank": "taxon_rank",
                "sourceinfo": "sourceinfo",
                "flags": "flags",
            }
            selection = ",".join(
                f"trim({column}, chr(9)||chr(10)||chr(13)||' ') AS {alias}"
                for column, alias in columns.items()
            )
            connection.execute(
                "CREATE TEMP TABLE taxonomy AS SELECT "
                + selection
                + " FROM read_csv(?, delim='|', quote='', header=true, columns={'uid':'VARCHAR','parent_uid':'VARCHAR','name':'VARCHAR','rank':'VARCHAR','sourceinfo':'VARCHAR','uniqname':'VARCHAR','flags':'VARCHAR','padding':'VARCHAR'}) WHERE nullif(trim(uid, chr(9)||chr(10)||chr(13)||' '),'') IS NOT NULL",
                [str(temporary)],
            )
            count, unique = connection.execute(
                "SELECT count(*),count(DISTINCT ott_id) FROM taxonomy"
            ).fetchone()
            if count != unique:
                raise ValueError("Duplicate OTT identifiers")
            connection.execute(
                "COPY taxonomy TO ? (FORMAT PARQUET, COMPRESSION ZSTD)", [str(output)]
            )
        atomic_json(
            receipt_path, {"input": identity, "output_sha256": sha256(output), "rows": count}
        )
    finally:
        temporary.unlink(missing_ok=True)
    return output


def tree_taxonomy(tree, index: Path) -> pd.DataFrame:
    tips = pd.DataFrame(
        [
            {"tree_tip": leaf.name, "ott_id": match[1]}
            for leaf in tree.leaves()
            if (match := re.search(r"_ott(\d+)$", leaf.name)) and int(match[1]) > 0
        ]
    )
    return tip_taxonomy(tips, index)


def tip_taxonomy(
    tips: pd.DataFrame, index: Path, *, include_ancestor_names: bool = False
) -> pd.DataFrame:
    """Resolve OTT ancestors for a scoped tip table without loading a global tree."""
    with duckdb.connect() as connection:
        connection.register("tips", tips)
        connection.execute(
            "CREATE TEMP VIEW taxonomy AS SELECT * FROM read_parquet('"
            + str(index).replace("'", "''")
            + "')"
        )
        lineage = connection.execute("""
            WITH RECURSIVE ancestry AS (
                SELECT tips.tree_tip,t.ott_id,t.parent_id,t.taxon_name,t.taxon_rank,0 AS depth
                FROM tips JOIN taxonomy t USING (ott_id)
                UNION ALL
                SELECT a.tree_tip,t.ott_id,t.parent_id,t.taxon_name,t.taxon_rank,a.depth+1
                FROM ancestry a JOIN taxonomy t ON a.parent_id=t.ott_id WHERE a.depth<100
            ) SELECT * FROM ancestry ORDER BY tree_tip,depth
        """).fetchdf()
        leaf_data = connection.execute(
            "SELECT tips.* EXCLUDE(ott_id),t.* FROM tips JOIN taxonomy t USING(ott_id)"
        ).fetchdf()
    if leaf_data.empty:
        raise ValueError("No tree tips match the OTT taxonomy index")
    if (lineage.depth >= 100).any():
        raise ValueError("OTT ancestor traversal exceeded depth limit")
    for rank in ("genus", "family", "order", "class"):
        nearest = (
            lineage[lineage.taxon_rank == rank]
            .drop_duplicates("tree_tip")
            .set_index("tree_tip")
            .taxon_name
        )
        leaf_data["ott_" + rank] = leaf_data.tree_tip.map(nearest)
    if include_ancestor_names:
        ancestor_names = lineage.groupby("tree_tip").taxon_name.agg(list)
        leaf_data["ott_ancestor_names"] = leaf_data.tree_tip.map(ancestor_names)
    leaf_data["normalized_name"] = leaf_data.taxon_name.map(normalize_name)
    return leaf_data


def current_species_names(
    path: Path, taxids: set[str], merges: dict[str, str]
) -> dict[str, set[str]]:
    names = {}
    with path.open() as handle:
        for line in handle:
            parts = [item.strip() for item in line.split("|")]
            taxid = canonical_taxid(parts[0], merges)
            if (
                taxid in taxids
                and len(parts) > 3
                and parts[3] in {"scientific name", "synonym", "equivalent name"}
            ):
                names.setdefault(taxid, set()).add(normalize_name(parts[1]))
    return names


def current_species_lineages(nodes: Path, names: Path, taxids: set[str]) -> dict[str, dict]:
    """Resolve genus/family from the same registered taxonomy as rescue names."""
    parents = {}
    genera, families = set(), set()
    with nodes.open() as handle:
        for line in handle:
            parts = [part.strip() for part in line.split("|")[:3]]
            taxid, parent = int(parts[0]), int(parts[1])
            parents[taxid] = parent
            if parts[2] == "genus":
                genera.add(taxid)
            elif parts[2] == "family":
                families.add(taxid)
    ancestors = {}
    needed = set()
    for taxid in taxids:
        current = int(taxid)
        found = {}
        seen = set()
        while current in parents:
            if current in seen:
                raise ValueError("Cycle in NCBI lineage")
            seen.add(current)
            if current in genera and "ncbi_genus" not in found:
                found["ncbi_genus"] = current
            if current in families and "ncbi_family" not in found:
                found["ncbi_family"] = current
            if parents[current] == current:
                break
            current = parents[current]
        ancestors[taxid] = found
        needed.update(found.values())
    scientific_names = {}
    with names.open() as handle:
        for line in handle:
            parts = [part.strip() for part in line.split("|")]
            taxid = int(parts[0])
            if taxid in needed and parts[3] == "scientific name":
                if taxid in scientific_names:
                    raise ValueError("Duplicate NCBI ancestor scientific names")
                scientific_names[taxid] = parts[1]
    return {
        taxid: {
            column: scientific_names.get(ancestors[taxid].get(column))
            for column in ["ncbi_genus", "ncbi_family"]
        }
        for taxid in taxids
    }


def improve_matches(
    joined: list[dict],
    crosswalk: Path,
    tip_data: pd.DataFrame,
    names: dict[str, set[str]],
    merges: dict[str, str] | None = None,
    *,
    current_lineages: dict[str, dict] | None = None,
) -> tuple[list[dict], pd.DataFrame]:
    """Recover only exact unique current-NCBI names with agreeing genus AND family.

    Existing direct joins reserve their tree tips. Homonyms, unresolved multiple
    names, rank conflicts and every upstream unsafe record remain unaccepted.
    """
    with duckdb.connect() as connection:
        lineages = (
            connection.execute(
                "SELECT iucn_sis_id,ncbi_genus,ncbi_family FROM read_parquet(?)", [str(crosswalk)]
            )
            .fetchdf()
            .set_index("iucn_sis_id")
            .to_dict("index")
        )
    species_tips = tip_data[tip_data.taxon_rank == "species"].copy()
    lookup = {name: frame for name, frame in species_tips.groupby("normalized_name")}
    improved = [
        dict(
            row,
            baseline_join_status=row["join_status"],
            join_method="ncbi_identifier" if row["join_status"] == "joined" else None,
        )
        for row in joined
    ]
    reserved = {row["tree_tip"] for row in joined if row["join_status"] == "joined"}
    proposals = {}
    audit = []
    for row in improved:
        if row["join_status"] != "ncbi_not_in_clade":
            continue
        taxid = row["canonical_ncbi_taxid"]
        expected = (
            current_lineages.get(taxid, {"ncbi_genus": None, "ncbi_family": None})
            if current_lineages is not None
            else lineages[row["iucn_sis_id"]]
        )
        accepted = []
        candidate_names = names.get(taxid, set())
        for name in sorted(candidate_names):
            frame = lookup.get(name)
            if frame is None:
                continue
            for candidate in frame.to_dict("records"):
                genus_agrees = bool(normalize_name(expected["ncbi_genus"])) and normalize_name(
                    candidate["ott_genus"]
                ) == normalize_name(expected["ncbi_genus"])
                family_agrees = bool(normalize_name(expected["ncbi_family"])) and normalize_name(
                    candidate["ott_family"]
                ) == normalize_name(expected["ncbi_family"])
                existing_taxids = {
                    canonical_taxid(source[5:], merges or {})
                    for source in str(candidate.get("sourceinfo") or "").split(",")
                    if source.startswith("ncbi:")
                }
                reason = "lineage_conflict_or_missing"
                if candidate.get("label_occurrences", 1) > 1:
                    reason = "repeated_tree_label"
                elif existing_taxids and existing_taxids != {taxid}:
                    reason = "ott_identifier_conflict"
                elif len(frame) > 1:
                    reason = "ambiguous_exact_name"
                elif genus_agrees and family_agrees:
                    reason = "eligible"
                    accepted.append(candidate["tree_tip"])
                audit.append(
                    {
                        "iucn_sis_id": row["iucn_sis_id"],
                        "iucn_scientific_name": row["iucn_scientific_name"],
                        "canonical_ncbi_taxid": taxid,
                        "matched_name": name,
                        "tree_tip": candidate["tree_tip"],
                        "ott_genus": candidate["ott_genus"],
                        "ott_family": candidate["ott_family"],
                        "ncbi_genus": expected["ncbi_genus"],
                        "ncbi_family": expected["ncbi_family"],
                        "reason": reason,
                    }
                )
        accepted = set(accepted)
        if len(accepted) == 1:
            proposals[row["iucn_sis_id"]] = next(iter(accepted))
    proposed_counts = pd.Series(list(proposals.values()), dtype=str).value_counts().to_dict()
    for row in improved:
        tip = proposals.get(row["iucn_sis_id"])
        if tip and tip not in reserved and proposed_counts[tip] == 1:
            row.update(
                tree_tip=tip, join_status="joined", join_method="exact_ncbi_name_genus_family"
            )
    accepted_pairs = {
        (row["iucn_sis_id"], row["tree_tip"])
        for row in improved
        if row["join_method"] == "exact_ncbi_name_genus_family"
    }
    for record in audit:
        record["accepted"] = (record["iucn_sis_id"], record["tree_tip"]) in accepted_pairs
        if record["reason"] == "eligible" and not record["accepted"]:
            record["reason"] = "reserved_tip_or_multiple_proposals"
    return improved, pd.DataFrame(
        audit,
        columns=[
            "iucn_sis_id",
            "iucn_scientific_name",
            "canonical_ncbi_taxid",
            "matched_name",
            "tree_tip",
            "ott_genus",
            "ott_family",
            "ncbi_genus",
            "ncbi_family",
            "reason",
            "accepted",
        ],
    )
