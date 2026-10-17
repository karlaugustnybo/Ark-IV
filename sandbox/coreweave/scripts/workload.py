"""Synthetic probe of the production geometry kernel and disk-backed SQL."""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import os
import platform
import shutil
import subprocess
import time
from pathlib import Path

import duckdb
import h3
import numpy as np
import psutil
import pyarrow as pa
import pyarrow.parquet as pq
from shapely.geometry import Polygon, box

from ark_pipeline.aggregation.pairs import aggregate_sorted_pairs
from ark_pipeline.spatial.coverage import exact_intersecting_cells_native, load_spatial_profile


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--scratch", type=Path, required=True)
    parser.add_argument("--rows", type=int, default=2_000_000)
    parser.add_argument("--require-tiles", action="store_true")
    args = parser.parse_args()
    if args.rows <= 0 or args.scratch.exists():
        parser.error("Use positive rows and a fresh synthetic scratch root")
    args.scratch.mkdir(parents=True)
    root = (
        Path(__file__)
        .resolve()
        .parents[3 if Path(__file__).resolve().parent.name == "scripts" else 2]
    )
    report = {
        "platform": platform.platform(),
        "machine": platform.machine(),
        "versions": {
            name: importlib.metadata.version(name)
            for name in ["duckdb", "pyarrow", "h3", "h3ronpy", "shapely"]
        },
        "cpu_count_visible": os.cpu_count(),
        "disk": shutil.disk_usage(args.scratch)._asdict(),
    }
    for name in ["cpu.max", "memory.max"]:
        path = Path("/sys/fs/cgroup") / name
        if path.exists():
            report[name] = path.read_text().strip()
    profile = load_spatial_profile(root / "config/spatial_semantics_iucn_richness_v3.toml")
    hole = box(12.45, 55.55, 12.50, 55.60)
    shapes = [
        box(12.4, 55.5, 12.7, 55.8),
        Polygon(box(12.4, 55.5, 12.7, 55.8).exterior.coords, [hole.exterior.coords]),
        box(179.8, -0.1, 180.2, 0.1),
    ]
    started = time.monotonic()
    cells, species = [], []
    for sid, geometry in enumerate(shapes, start=1):
        first = exact_intersecting_cells_native(geometry, profile).cells
        second = exact_intersecting_cells_native(geometry, profile).cells
        np.testing.assert_array_equal(first, second)
        assert len(first) > 0 and len(np.unique(first)) == len(first)
        cells.extend(first)
        species.extend([sid] * len(first))
    table = pa.table(
        {
            "h3_index": pa.array(cells, type=pa.uint64()),
            "iucn_sis_id": pa.array(species, type=pa.int64()),
        }
    )
    pairs = args.scratch / "synthetic-pairs.parquet"
    pq.write_table(table, pairs, compression="zstd")
    assert pq.read_table(pairs).equals(table)
    report["geometry"] = {
        "seconds": time.monotonic() - started,
        "pairs": len(cells),
        "parquet_bytes": pairs.stat().st_size,
        "roundtrip": True,
        "repeatable": True,
        "includes_holes_and_antimeridian": True,
    }
    con = duckdb.connect()
    con.execute("SET threads=2")
    con.execute("SET memory_limit='64MB'")
    con.execute("SET temp_directory=?", [str(args.scratch / "duckdb-spill")])
    source_literal = "'" + str(pairs).replace("'", "''") + "'"
    report["aggregation"] = {}
    for resolution in [7, 3]:
        report["aggregation"][str(resolution)] = aggregate_sorted_pairs(
            con,
            source_literal,
            args.scratch / f"lists-res{resolution}.parquet",
            resolution=resolution,
            deduplicate=True,
            derive_res3=resolution == 3,
            batch_pairs=37,
            write_pairs=100,
        )
    assert report["aggregation"]["7"]["relationships"] == len(cells)
    con.close()
    con = duckdb.connect()
    con.execute("SET threads=1")
    con.execute("SET memory_limit='128MB'")
    con.execute("SET preserve_insertion_order=false")
    con.execute("SET temp_directory=?", [str(args.scratch / "duckdb-spill-sort")])
    # Sorting deliberately exceeds the SQL memory budget, exercising real disk spill.
    con.execute("SET enable_profiling='json'")
    con.execute("SET profiling_output=?", [str(args.scratch / "duckdb-profile.json")])
    started = time.monotonic()
    output = args.scratch / "sorted.parquet"
    output_literal = "'" + str(output).replace("'", "''") + "'"
    con.execute(
        f"COPY (SELECT i, md5(i::VARCHAR) AS key FROM range(?) t(i) ORDER BY key) TO {output_literal} (FORMAT PARQUET, COMPRESSION ZSTD)",
        [args.rows],
    )
    profile_data = json.loads((args.scratch / "duckdb-profile.json").read_text())
    con.execute("SET enable_profiling='no_output'")
    count = con.execute("SELECT count(*) FROM read_parquet(?)", [str(output)]).fetchone()[0]
    assert count == args.rows
    report["sort"] = {
        "rows": count,
        "seconds": time.monotonic() - started,
        "parquet_bytes": output.stat().st_size,
        "peak_spill_bytes": profile_data.get("system_peak_temp_dir_size"),
        "peak_duckdb_memory_bytes": profile_data.get("system_peak_buffer_memory"),
    }
    tile_bin = shutil.which("tippecanoe")
    report["tiles"] = {"available": bool(tile_bin)}
    if tile_bin:
        geojson = args.scratch / "synthetic.geojson"
        features = []
        for cell in cells[:200]:
            lat, lon = h3.cell_to_latlng(h3.int_to_str(int(cell)))
            features.append(
                {
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [lon, lat]},
                    "properties": {"score": 1},
                }
            )
        geojson.write_text(json.dumps({"type": "FeatureCollection", "features": features}))
        tiles = args.scratch / "synthetic.pmtiles"
        subprocess.run(
            [tile_bin, "-f", "-z", "7", "-o", str(tiles), str(geojson)],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        assert tiles.read_bytes()[:7] == b"PMTiles"
        report["tiles"].update({"pmtiles_bytes": tiles.stat().st_size, "header_valid": True})
    elif args.require_tiles:
        raise RuntimeError("Tippecanoe missing")
    report["process_rss_bytes"] = psutil.Process().memory_info().rss
    report["status"] = "passed"
    (args.scratch / "workload-report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
