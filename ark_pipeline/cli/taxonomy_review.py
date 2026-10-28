"""Compare pinned COL XR and legacy GBIF name concepts without accepting matches.

Consumes the matcher's gbif_review_input.json. Canonical-name corroboration is
review evidence, never proof of IUCN/NCBI identifier identity or trait transfer.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import zipfile
from collections import Counter, defaultdict
from pathlib import Path

from ark_pipeline.cli.serving_metadata import source_file
from ark_pipeline.cli.sources_acquire import atomic_json, iso_now, load_manifest, sha256


def normalise(value: str) -> str:
    return " ".join(value.casefold().split())


def taxon_rows(path: Path, selected_ids: set[str] | None = None):
    """Stream the provider's TSV without extracting or coercing string IDs."""
    csv.field_size_limit(16 * 1024 * 1024)
    with zipfile.ZipFile(path) as archive, archive.open("Taxon.tsv") as raw:
        with io.TextIOWrapper(raw, encoding="utf-8-sig", newline="") as handle:
            reader = csv.reader(handle, delimiter="\t", quoting=csv.QUOTE_NONE)
            header = next(reader, [])
            names = [name.removeprefix("dwc:").removeprefix("col:") for name in header]
            required = {
                "taxonID",
                "acceptedNameUsageID",
                "taxonomicStatus",
                "taxonRank",
                "kingdom",
                "family",
            }
            if len(set(names)) != len(names) or not required.issubset(names):
                raise ValueError(f"{path.name}: missing or duplicate taxonomy columns")
            if "canonicalName" not in names and not {"genericName", "specificEpithet"}.issubset(
                names
            ):
                raise ValueError(f"{path.name}: no supported canonical-name fields")
            fields = [
                (index, name)
                for index, name in enumerate(names)
                if name in required | {"canonicalName", "genericName", "specificEpithet", "notho"}
            ]
            id_index = names.index("taxonID")
            for values in reader:
                if len(values) > len(header):
                    raise ValueError(f"{path.name}: extra TSV columns")
                if id_index >= len(values) or not values[id_index].strip():
                    raise ValueError(f"{path.name}: missing taxon ID")
                if selected_ids is not None and values[id_index].strip() not in selected_ids:
                    continue
                row = {
                    name: values[index].strip() if index < len(values) else ""
                    for index, name in fields
                }
                yield row


def canonical_name(row: dict[str, str]) -> str:
    if normalise(row["taxonRank"]) != "species" or normalise(row.get("notho", "")) not in {
        "",
        "false",
        "0",
    }:
        return ""
    if row.get("canonicalName"):
        return normalise(row["canonicalName"])
    genus, epithet = row.get("genericName"), row.get("specificEpithet")
    return normalise(f"{genus} {epithet}") if genus and epithet else ""


def name_index(path: Path, names: set[str]):
    candidates = defaultdict(list)
    targets = set()
    if not names:
        return candidates, defaultdict(list)
    for row in taxon_rows(path):
        name = canonical_name(row)
        if name in names:
            candidates[name].append(row)
            targets.add(row["acceptedNameUsageID"] or row["taxonID"])
    accepted = defaultdict(list)
    for row in taxon_rows(path, targets):
        accepted[row["taxonID"]].append(row)
    return candidates, accepted


def concepts(name: str, lineage: dict[str, str], index) -> list[str]:
    candidates, accepted = index
    result = set()
    for candidate in candidates.get(normalise(name), []):
        if normalise(candidate["taxonomicStatus"]) not in {
            "accepted",
            "synonym",
            "homotypic synonym",
            "heterotypic synonym",
        }:
            continue
        target_id = candidate["acceptedNameUsageID"] or candidate["taxonID"]
        targets = accepted.get(target_id, [])
        if len(targets) != 1:
            continue
        target = targets[0]
        if (
            normalise(target["taxonomicStatus"]) != "accepted"
            or normalise(target["taxonRank"]) != "species"
        ):
            continue
        if target["acceptedNameUsageID"] not in {"", target_id}:
            continue
        if any(
            not target.get(rank) or normalise(target[rank]) != normalise(value)
            for rank, value in lineage.items()
            if value
        ):
            continue
        result.add(target_id)
    return sorted(result)


def pair_review(row: dict, index) -> dict:
    lineage = {rank: row.get(f"iucn_{rank}", "") for rank in ("kingdom", "family")}
    left = concepts(row["iucn_scientific_name"], lineage, index)
    right = concepts(row["goat_scientific_name"], lineage, index)
    if not left or not right:
        status = "unresolved"
    elif len(left) != 1 or len(right) != 1:
        status = "ambiguous"
    elif left != right:
        status = "concept_conflict"
    else:
        status = "shared_name_concept"
    return {
        "status": status,
        "iucn_name_concepts": left,
        "goat_name_concepts": right,
        "accepted_taxon_id": left[0] if status == "shared_name_concept" else None,
    }


def review(rows: list[dict], col_archive: Path, legacy_archive: Path) -> dict:
    seen = set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("review queue rows must be objects")
        for field in ("iucn_kingdom", "iucn_family"):
            if field in row and not isinstance(row[field], str):
                raise ValueError(f"queue requires string {field}")
        for field in ("iucn_scientific_name", "goat_scientific_name"):
            if not isinstance(row.get(field), str) or not row[field].strip():
                raise ValueError(f"queue requires nonempty {field}")
        if not row.get("iucn_sis_id") or not row.get("ncbi_taxid"):
            raise ValueError("queue requires IUCN and NCBI identifiers")
        identity = (str(row["iucn_sis_id"]), str(row["ncbi_taxid"]))
        if identity in seen:
            raise ValueError("duplicate IUCN/NCBI pair in review queue")
        seen.add(identity)
    names = {
        normalise(row[key])
        for row in rows
        for key in ("iucn_scientific_name", "goat_scientific_name")
    }
    col_index = name_index(col_archive, names)
    legacy_index = name_index(legacy_archive, names)
    results = []
    for row in rows:
        col, legacy = pair_review(row, col_index), pair_review(row, legacy_index)
        results.append(
            {
                "iucn_sis_id": str(row["iucn_sis_id"]),
                "ncbi_taxid": str(row["ncbi_taxid"]),
                "col_xr": col,
                "legacy_gbif": legacy,
                "identifier_identity_confirmed": False,
                "eligible_for_trait_transfer": False,
            }
        )
    return {
        "schema_version": 1,
        "generated_at": iso_now(),
        "reviewed_rows": len(rows),
        "policy": "canonical-name concept review only; no identifier bridge or crosswalk acceptance",
        "col_status_counts": dict(Counter(row["col_xr"]["status"] for row in results)),
        "legacy_status_counts": dict(Counter(row["legacy_gbif"]["status"] for row in results)),
        "rows": results,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--queue", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--limit", type=int, default=100, help="maximum candidate pairs to review (default 100)"
    )
    args = parser.parse_args(argv)
    if args.limit < 1:
        parser.error("--limit must be positive")
    manifest = load_manifest(args.root)
    paths, inputs = {}, {}
    for source_id, filename in (("col-xr", "col-xr-dwca.zip"), ("gbif-backbone", "backbone.zip")):
        if source_id not in manifest.get("sources", {}):
            raise ValueError(f"{source_id} is not registered; acquire it first")
        paths[source_id] = source_file(
            args.root, manifest, source_id, filename, args.root / "unused"
        )
        source = manifest["sources"][source_id]
        record = next(item for item in source["files"] if item["logical_name"] == filename)
        inputs[source_id] = {
            "release": source["release"],
            "sha256": record["sha256"],
            "namespace": "col-xr" if source_id == "col-xr" else "gbif-backbone",
        }
    queue_hash = sha256(args.queue)
    rows = json.loads(args.queue.read_text())
    if not isinstance(rows, list):
        raise ValueError("expected the matcher's JSON candidate array")
    result = review(rows[: args.limit], paths["col-xr"], paths["gbif-backbone"])
    if sha256(args.queue) != queue_hash:
        raise ValueError("review queue changed during comparison")
    latest = load_manifest(args.root)
    if any(latest.get("sources", {}).get(key) != manifest["sources"][key] for key in inputs):
        raise ValueError("selected taxonomy source changed during comparison")
    result.update(
        inputs=inputs, queue_sha256=queue_hash, input_rows=len(rows), limited=len(rows) > args.limit
    )
    atomic_json(args.output, result)
    print(
        json.dumps(
            {
                "status": "review-only",
                "reviewed_rows": result["reviewed_rows"],
                "input_rows": len(rows),
                "output": str(args.output),
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
