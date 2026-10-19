"""Documented name changes, without accepting unreviewed species lumping."""

from __future__ import annotations

import json
import shutil
import tarfile
from pathlib import Path

import duckdb
import pandas as pd

from ark_pipeline.edge import canonical_taxid
from ark_pipeline.edge_link_extension import extend_iucn_links
from ark_pipeline.edge_matching import normalize_name
from ark_pipeline.runtime.provenance import atomic_json, sha256


def build_synonym_index(archive: Path, output: Path) -> Path:
    stamp = output.with_suffix(".receipt.json")
    identity = {"schema_version": 1, "archive_sha256": sha256(archive)}
    if output.is_file() and stamp.is_file():
        saved = json.loads(stamp.read_text())
        if saved.get("input") == identity:
            if sha256(output) != saved["output_sha256"]:
                raise ValueError("OTT synonym index checksum changed")
            return output
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(".synonyms.part")
    indexed = output.with_suffix(".part")
    try:
        with tarfile.open(archive) as tar:
            members = [m for m in tar.getmembers() if m.name.endswith("/synonyms.tsv")]
            if len(members) != 1:
                raise ValueError("Expected one OTT synonyms.tsv")
            with tar.extractfile(members[0]) as source, temporary.open("wb") as target:
                shutil.copyfileobj(source, target)
        with duckdb.connect() as c:
            c.execute("SET memory_limit='1GB'")
            c.execute(
                """CREATE TEMP TABLE synonyms AS
                SELECT lower(regexp_replace(trim(name,chr(9)||chr(10)||chr(13)||' '),'\\s+',' ','g')) AS normalized_name,
                       trim(uid,chr(9)||chr(10)||chr(13)||' ') AS ott_id,
                       trim(type,chr(9)||chr(10)||chr(13)||' ') AS alias_type,
                       trim(sourceinfo,chr(9)||chr(10)||chr(13)||' ') AS alias_sourceinfo
                FROM read_csv(?,delim='|',quote='',header=true,
                  columns={'name':'VARCHAR','uid':'VARCHAR','type':'VARCHAR','uniqname':'VARCHAR','sourceinfo':'VARCHAR','padding':'VARCHAR'})
                WHERE nullif(trim(uid,chr(9)||chr(10)||chr(13)||' '),'') IS NOT NULL""",
                [str(temporary)],
            )
            count = c.execute("SELECT count(*) FROM synonyms").fetchone()[0]
            c.execute("COPY synonyms TO ? (FORMAT PARQUET,COMPRESSION ZSTD)", [str(indexed)])
        indexed.replace(output)
        atomic_json(stamp, {"input": identity, "rows": count, "output_sha256": sha256(output)})
    finally:
        temporary.unlink(missing_ok=True)
        indexed.unlink(missing_ok=True)
    return output


def ncbi_names_for_iucn(path: Path, names: set[str], merges: dict[str, str]) -> pd.DataFrame:
    """Record exact scientific/synonym/equivalent name evidence; no fuzzy search."""
    records = []
    with path.open() as handle:
        for line in handle:
            parts = [part.strip() for part in line.split("|")]
            name = normalize_name(parts[1])
            if name in names and parts[3] in {"scientific name", "synonym", "equivalent name"}:
                records.append(
                    dict(
                        normalized_name=name,
                        ncbi_taxid=canonical_taxid(parts[0], merges),
                        name_class=parts[3],
                    )
                )
    return pd.DataFrame(
        records, columns=["normalized_name", "ncbi_taxid", "name_class"]
    ).drop_duplicates()


def prepare_candidates(
    evidence: pd.DataFrame,
    metadata: pd.DataFrame,
    taxonomy: pd.DataFrame,
    ncbi_names: pd.DataFrame,
    merges: dict[str, str],
    ott_name_targets: dict[str, set[str]] | None = None,
) -> pd.DataFrame:
    """Keep all competing leaves before assessing evidence eligibility.

    Untyped OTT aliases are not proof of identical species circumscriptions.
    Only unchanged binomial epithets are eligible automatically. A different
    assessed IUCN species at the target accepted name blocks alias transfer.
    NCBI disagreements are held, rather than resolved by choosing a source.
    """
    if evidence.empty:
        return pd.DataFrame(columns=["tree_tip", "ott_id", "normalized_name"])
    meta = metadata.set_index("tree_tip").to_dict("index")
    assessed = set(taxonomy.scientificName.map(normalize_name))
    ncbi_lookup = ncbi_names.groupby("normalized_name").ncbi_taxid.agg(set).to_dict()
    records = []
    for (name, leaf), group in evidence.groupby(["normalized_name", "tree_tip"], sort=True):
        tip = meta[leaf]
        accepted = normalize_name(tip["taxon_name"])
        basis = set(group.identity_basis)
        old, new = name.split(), accepted.split()
        ncbi_ids = ncbi_lookup.get(name, set())
        leaf_ids = {
            canonical_taxid(s[5:], merges)
            for s in str(tip.get("sourceinfo") or "").split(",")
            if s.startswith("ncbi:")
        }
        block = None
        same_epithet = len(old) == len(new) == 2 and old[1] == new[1]
        documented = bool(basis & {"ott_synonym", "current_ncbi_name"})
        if accepted != name:
            if accepted in assessed:
                block = "different_assessed_iucn_species_at_target"
            elif not same_epithet:
                block = "species_concept_synonym_needs_review"
        if block is None and ncbi_ids and leaf_ids and not ncbi_ids.intersection(leaf_ids):
            block = "current_ncbi_identity_conflict"
        if block is None and (
            len(ncbi_ids) > 1 or (basis == {"current_ncbi_name"} and not ncbi_ids)
        ):
            block = "ambiguous_current_ncbi_name"
        if block is None and len((ott_name_targets or {}).get(name, set())) > 1:
            block = "ambiguous_ott_species_name"
        # Exact-name evidence is retained to expose homonyms even if a synonym
        # route would otherwise appear to be unique.
        records.append(
            {
                **tip,
                "tree_tip": leaf,
                "normalized_name": name,
                "identity_basis": ",".join(sorted(basis)),
                "alias_sourceinfo": ";".join(sorted(set(group.alias_sourceinfo.dropna()) - {""})),
                "same_epithet": same_epithet,
                "documented_genus_change": documented
                and same_epithet
                and old[0] != new[0]
                and block is None,
                "candidate_block_reason": block,
            }
        )
    return pd.DataFrame(records)


def extend_synonym_links(baseline, crosswalk, taxonomy, candidates):
    result, audit = extend_iucn_links(baseline, crosswalk, taxonomy, candidates)
    previous = set(baseline.loc[baseline.tree_identity_verified, "iucn_sis_id"])
    new = result.tree_identity_verified & ~result.iucn_sis_id.isin(previous)
    result.loc[new, "join_method"] = "documented_binomial_name_change"
    if not audit.empty:
        evidence = candidates[
            [
                "normalized_name",
                "tree_tip",
                "taxon_name",
                "identity_basis",
                "alias_sourceinfo",
                "same_epithet",
                "candidate_block_reason",
            ]
        ]
        audit["normalized_name"] = audit.iucn_scientific_name.map(normalize_name)
        audit = audit.drop(columns=["identity_basis"]).merge(
            evidence, how="left", on=["normalized_name", "tree_tip"], validate="many_to_one"
        )
    before = baseline.set_index("iucn_sis_id")
    after = result.set_index("iucn_sis_id")
    for column in [
        "safe_for_automatic_species_trait_transfer",
        "ncbi_to_tree_link_verified",
        "original_join_status",
    ]:
        if not before[column].equals(after[column]):
            raise ValueError(f"Name-change extension altered upstream {column}")
    original = before.loc[before.tree_identity_verified, "tree_tip"]
    if not original.equals(after.loc[original.index, "tree_tip"]):
        raise ValueError("Name-change extension changed an accepted link")
    return result, audit
