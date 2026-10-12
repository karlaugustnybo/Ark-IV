"""Build a coarse passage-season supplement for the species range overlay.

This is direct resolution-3 any-touch coverage, not a second richness/priority
profile. The default map remains the resolution-7-derived resident/seasonal
profile. Keeping the supplement separate avoids changing assessed identities.
"""

from __future__ import annotations

import argparse
import json
import math
import os
from collections import defaultdict
from dataclasses import replace
from pathlib import Path

import duckdb
import pyogrio

from ark_pipeline.cli.spatial_pairs import DEFAULT_PROFILE
from ark_pipeline.runtime.provenance import atomic_json, sha256
from ark_pipeline.spatial.coverage import exact_intersecting_cells_native, load_spatial_profile


def build(root: Path, profile_path: Path, species_path: Path, output: Path) -> dict:
    # Bind receipts to the inputs observed around intake, not paths rehashed
    # after the scan. Managed writers replace inputs atomically.
    def identity(path: Path):
        stat = path.stat()
        return (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)

    consumed = {path: (identity(path), sha256(path)) for path in (profile_path, species_path)}

    def verify_inputs():
        for path, (version, digest) in consumed.items():
            if identity(path) != version or sha256(path) != digest or identity(path) != version:
                raise ValueError(f"Passage input changed during build: {path}")

    profile = replace(
        load_spatial_profile(profile_path), resolution=3, decision_simplification_degrees=0.0
    )
    manifest = json.loads((root / "acquisition/current.json").read_text())
    with duckdb.connect() as con:
        columns = set(
            con.execute("DESCRIBE SELECT * FROM read_parquet(?)", [str(species_path)]).df()[
                "column_name"
            ]
        )
        if "iucn_sis_id" not in columns:
            raise ValueError("Passage coverage requires explicit iucn_sis_id metadata")
        identifiers = con.execute(
            "SELECT iucn_sis_id, gbif_accepted_id FROM read_parquet(?)", [str(species_path)]
        ).fetchall()
    verify_inputs()
    mapping = {}
    for sis, serving in identifiers:
        if sis is None:
            continue
        key = str(sis)
        if key in mapping and mapping[key] != str(serving):
            raise ValueError("Ambiguous IUCN SIS to serving identifier mapping")
        mapping[key] = str(serving)
    cells = defaultdict(set)
    source_rows, accepted_rows, inputs = 0, 0, []
    for item in manifest["sources"]["iucn-spatial"]["files"]:
        path = root / item["path"]
        archive = "/vsizip/" + str(path.resolve())
        digest = sha256(path)
        if path.stat().st_size != item["bytes"] or digest != item["sha256"]:
            raise ValueError(f"Registered passage source changed: {path}")
        inputs.append({"path": str(path), "sha256": digest})
        for layer, geometry_type in pyogrio.list_layers(archive):
            if geometry_type not in ("Polygon", "MultiPolygon"):
                continue
            fields = set(pyogrio.read_info(archive, layer=layer)["fields"])
            if not {"id_no", "presence", "origin", "seasonal"} <= fields:
                raise ValueError(f"Missing seasonal attributes: {path}:{layer}")
            frame = pyogrio.read_dataframe(
                archive,
                layer=layer,
                where="seasonal = 4",
                columns=["id_no", "presence", "origin", "seasonal"],
            )
            if frame.crs is None:
                raise ValueError(f'Passage source has no CRS: {path}:{layer}')
            frame = frame.to_crs('EPSG:4326')
            source_rows += len(frame)
            for row in frame.itertuples():
                if row.id_no is None or not math.isfinite(float(row.id_no)) or row.geometry is None:
                    continue
                number = float(row.id_no)
                if not number.is_integer() or number <= 0:
                    continue
                sid = mapping.get(str(int(number)))
                if (
                    sid is None
                    or row.presence not in profile.presence
                    or row.origin not in profile.origin
                ):
                    continue
                coverage = exact_intersecting_cells_native(row.geometry, profile)
                cells[sid].update(map(int, coverage.cells))
                accepted_rows += 1
        if path.stat().st_size != item["bytes"] or sha256(path) != digest:
            raise ValueError(f"Registered passage source changed during scan: {path}")
        print(f"Passage scanned {item['logical_name']}", flush=True)
    output.parent.mkdir(parents=True, exist_ok=True)
    with duckdb.connect() as con:
        con.execute(
            "CREATE TABLE passage(gbif_accepted_id VARCHAR, resolution SMALLINT, h3_indexes BIGINT[])"
        )
        if cells:
            con.executemany(
                "INSERT INTO passage VALUES (?,3,?)",
                [(sid, sorted(indexes)) for sid, indexes in sorted(cells.items())],
            )
        temporary = output.with_name(output.name + ".tmp")
        target = "'" + str(temporary).replace("'", "''") + "'"
        con.execute(f"COPY passage TO {target} (FORMAT PARQUET, COMPRESSION ZSTD)")
    report = dict(
        status="passed",
        schema_version=1,
        scope="species-range-overlay",
        resolution=3,
        definition="Direct any-touch passage (seasonal=4), unioned with the default species range; map scores unchanged.",
        profile_sha256=consumed[profile_path][1],
        species_sha256=consumed[species_path][1],
        source_rows=source_rows,
        accepted_rows=accepted_rows,
        species=len(cells),
        relationships=sum(map(len, cells.values())),
        inputs=inputs,
        output_sha256=sha256(temporary),
    )
    try:
        verify_inputs()
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    os.replace(temporary, output)
    atomic_json(output.with_suffix(".report.json"), report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--profile", type=Path, default=DEFAULT_PROFILE)
    parser.add_argument("--species", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.root, args.profile, args.species, args.output), indent=2))


if __name__ == "__main__":
    main()
