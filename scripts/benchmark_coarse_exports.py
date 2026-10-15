"""Replay coarse exports from a pipeline benchmark without rebuilding spatial data.

The source database and boundary files are read-only. Each invocation requires
a new output directory; run before and after a change and compare artifact hashes.
This measures export work, not aggregation or Tippecanoe compilation.
"""

from __future__ import annotations

import argparse
import cProfile
import json
import platform
import pstats
import resource
import sys
import time
from pathlib import Path

import duckdb
import shapely

from ark_pipeline.builders.coarse_cache import (
    export_cell_boundaries,
    export_coarse_snapshot,
    export_map_metadata,
    stream_tile_features,
)
from ark_pipeline.runtime.provenance import atomic_json, sha256
from ark_pipeline.spatial.boundaries import JurisdictionIndex


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--benchmark-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--profile", action="store_true")
    args = parser.parse_args()
    source = args.benchmark_root.resolve()
    output = args.output_root.resolve()
    if output.exists():
        parser.error("Use a new output directory to preserve previous measurements")
    config = json.loads((source / "config.json").read_text())
    database = source / "build.duckdb"
    boundaries = {name: Path(path) for name, path in config["boundaries"].items()}
    report = {
        "database_sha256": sha256(database),
        "boundaries_sha256": {name: sha256(path) for name, path in boundaries.items()},
        "platform": platform.platform(),
        "python": platform.python_version(),
        "duckdb": duckdb.__version__,
        "shapely": shapely.__version__,
        "threads": 1,
        "profiled": args.profile,
        "phases": {},
    }
    output.mkdir(parents=True)
    profiler = cProfile.Profile() if args.profile else None

    def measure(name, function):
        started, cpu = time.perf_counter(), time.process_time()
        result = function()
        report["phases"][name] = {
            "wall_seconds": time.perf_counter() - started,
            "cpu_seconds": time.process_time() - cpu,
        }
        atomic_json(output / "benchmark.json", report)
        print(name, report["phases"][name], flush=True)
        return result

    if profiler:
        profiler.enable()
    with duckdb.connect(str(database), read_only=True, config={"threads": 1}) as connection:
        indexes = measure(
            "load_boundaries",
            lambda: {name: JurisdictionIndex(path) for name, path in boundaries.items()},
        )
        report["cells"] = connection.execute("SELECT count(*) FROM h3_res3_agg_all").fetchone()[0]
        measure("cell_boundaries", lambda: export_cell_boundaries(connection, output, indexes))
        snapshot = measure(
            "coarse_snapshot", lambda: export_coarse_snapshot(connection, output, indexes)
        )
        measure(
            "map_metadata",
            lambda: export_map_metadata(
                connection,
                output / "map-metadata.json",
                (3,),
                indexes,
                snapshot,
            ),
        )
        with (output / "features.geojsonseq").open("w") as stream:
            report["features"] = measure(
                "tile_features",
                lambda: stream_tile_features(
                    connection,
                    stream,
                    resolutions=(3,),
                    jurisdiction_index=indexes,
                ),
            )
    if profiler:
        profiler.disable()
        profiler.dump_stats(output / "exports.prof")
        with (output / "profile.txt").open("w") as stream:
            pstats.Stats(profiler, stream=stream).sort_stats("cumulative").print_stats(40)
    report["total_wall_seconds"] = sum(phase["wall_seconds"] for phase in report["phases"].values())
    peak_rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    report["peak_rss_bytes"] = peak_rss if sys.platform == "darwin" else peak_rss * 1024
    report["artifacts_sha256"] = {
        path.name: sha256(path)
        for path in sorted(output.iterdir())
        if path.suffix in (".parquet", ".arrow", ".geojsonseq") or path.name == "map-metadata.json"
    }
    atomic_json(output / "benchmark.json", report)
    print(f"Total: {report['total_wall_seconds']:.3f}s", flush=True)


if __name__ == "__main__":
    main()
