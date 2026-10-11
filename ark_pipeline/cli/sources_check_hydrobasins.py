"""Audit acquired basin geometries against existing normalized tables, without H3 filling."""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

import duckdb
import pyogrio
import shapely

from ark_pipeline.cli.sources_acquire import atomic_json, load_manifest
from ark_pipeline.cli.spatial_pairs import (
    HYDROBASIN_REGION_CODES,
    _hybas_region_level,
    hydrobasin_files,
    hydrobasin_records_by_code,
)


def audit(root: Path, relations_root: Path) -> dict:
    paths = sorted(relations_root.glob("archives/*/basin_species.parquet"))
    if not paths:
        raise ValueError(f"no normalized basin tables in {relations_root}")
    with duckdb.connect() as connection:
        connection.execute("SET threads=2")
        connection.execute("SET memory_limit='2GB'")
        relationships = dict(
            connection.execute(
                "SELECT hybas_id, count(DISTINCT iucn_sis_id) FROM read_parquet(?) GROUP BY hybas_id",
                [[str(path) for path in paths]],
            ).fetchall()
        )
    records = hydrobasin_files(root, load_manifest(root))
    by_code = hydrobasin_records_by_code(records)
    groups = defaultdict(set)
    unsupported = set()
    for identifier in relationships:
        try:
            groups[_hybas_region_level(identifier)].add(identifier)
        except ValueError:
            unsupported.add(identifier)
    found = set()
    standard_found = set()
    scans = []
    for (region, level), wanted in sorted(groups.items()):
        code = HYDROBASIN_REGION_CODES[region] if region else "islands"
        claimed = set()
        for record in by_code[code]:
            name = record["logical_name"]
            is_lake = bool(region) and "hybas_lake_" in name.lower()
            is_standard = bool(region) and not is_lake
            is_island = not region
            column = "hybas_id" if is_island else "HYBAS_ID"
            layer = (
                Path(name).stem
                if is_island
                else f"hybas_{'lake_' if is_lake else ''}{code}_lev{level:02d}_v1c"
            )
            matched = set()
            invalid = set()
            seen = set()
            with pyogrio.open_arrow(
                record["resolved_path"],
                layer=layer,
                columns=[column],
                batch_size=8192,
                use_pyarrow=True,
            ) as (metadata, reader):
                for batch in reader:
                    ids = batch[column].to_pylist()
                    selected = [
                        index for index, identifier in enumerate(ids) if identifier in wanted
                    ]
                    if not selected:
                        continue
                    geometry_column = metadata.get("geometry_name") or "wkb_geometry"
                    geometries = batch[geometry_column]
                    for index in selected:
                        identifier = ids[index]
                        if identifier not in claimed and identifier in seen:
                            raise ValueError(f"{layer}: duplicate HYBAS_ID values in geometry source")
                        seen.add(identifier)
                        wkb = geometries[index].as_py()
                        geometry = shapely.from_wkb(wkb) if wkb else None
                        if (
                            geometry is None
                            or shapely.is_empty(geometry)
                            or shapely.get_type_id(geometry) not in (3, 6)
                        ):
                            invalid.add(identifier)
                        else:
                            matched.add(identifier)
            # Presence selects the source even if its polygon is unusable. A
            # valid standard polygon cannot rescue a malformed winning lake row.
            found.update(matched - claimed)
            claimed.update(seen)
            if is_standard:
                standard_found.update(matched)
            scans.append(
                {
                    "archive": name,
                    "layer": layer,
                    "matched_ids": len(matched),
                    "empty_or_nonpolygon_ids": sorted(invalid),
                }
            )
            print(f"{layer}: {len(matched):,} matching polygon IDs", flush=True)
    missing = set(relationships) - found
    return {
        "status": "matched" if not missing else "partial-source-mismatch",
        "unique_requested_ids": len(relationships),
        "unique_standard_matching_ids": len(standard_found),
        "unique_original_missing_ids": len(relationships) - len(standard_found),
        "unique_matching_ids": len(found),
        "unique_recovered_ids": len(found - standard_found),
        "unique_missing_ids": len(missing),
        "missing_ids": sorted(missing),
        "unsupported_ids": sorted(unsupported),
        "missing_distinct_relationships": sum(relationships[i] for i in missing),
        "all_distinct_basin_species_relationships": sum(relationships.values()),
        "inputs": [
            {
                "source_id": r["source_id"],
                "logical_name": r["logical_name"],
                "sha256": r["sha256"],
                "bytes": r["bytes"],
            }
            for r in records
        ],
        "scans": scans,
        "scope": "Exact ID and nonempty polygon checks only; no H3 coverage or full pipeline execution.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--relations-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = audit(args.root, args.relations_root)
    atomic_json(args.output, report)
    print(
        json.dumps(
            {key: value for key, value in report.items() if key not in {"scans", "inputs"}},
            indent=2,
        )
    )
    return 0 if not report["unique_missing_ids"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
