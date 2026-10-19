"""Synthetic high-richness lists exercise the production metric worker memory bounds."""

from __future__ import annotations

import argparse
import multiprocessing
import resource
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import duckdb
import pyarrow.parquet as pq

from ark_pipeline.builders.fine_metrics import aggregate_worker, initialize_aggregate_worker
from ark_pipeline.runtime.provenance import atomic_json, sha256


def main(
    root,
    out,
    report_path,
    *,
    species_limit=1000,
    row_limit=131072,
    partition_limit=6,
):
    if out.exists() or min(species_limit, row_limit, partition_limit) <= 0:
        raise ValueError("Fresh output and positive limits required")
    out.mkdir(parents=True)
    species = root / "species/species.parquet"
    systems = root / "species/species_systems.parquet"
    connection = duckdb.connect()
    connection.execute("SET memory_limit='2GB'")
    connection.execute("SET threads=1")
    ids = [
        r[0]
        for r in connection.execute(
            "SELECT try_cast(gbif_accepted_id AS BIGINT) FROM read_parquet(?) WHERE try_cast(gbif_accepted_id AS BIGINT) IS NOT NULL ORDER BY 1 LIMIT ?",
            [str(species), species_limit],
        ).fetchall()
    ]
    if not ids:
        raise ValueError("Too few species to exercise high richness")
    parts = sorted(
        (root / "spatial/serving/current/res7_merged_parts").glob("base_*.parquet"),
        key=lambda p: pq.read_metadata(p).num_rows,
        reverse=True,
    )[:partition_limit]
    if not parts:
        raise ValueError("No list partitions to exercise")
    dense = []
    for part in parts:
        target = out / part.name
        literal = "[" + ",".join(map(str, ids)) + "]::BIGINT[]"
        query = f"COPY (SELECT h3_cell, {literal} AS species_ids FROM read_parquet(?) LIMIT {row_limit}) TO '{str(target).replace(chr(39), chr(39) * 2)}' (FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 122880)"
        connection.execute(query, [str(part)])
        dense.append(target)
    connection.close()
    trials = []
    for workers in (4, 6):
        before = resource.getrusage(resource.RUSAGE_CHILDREN)
        started = time.monotonic()
        with ProcessPoolExecutor(
            max_workers=workers,
            mp_context=multiprocessing.get_context("spawn"),
            initializer=initialize_aggregate_worker,
            initargs=(species, systems, out / "scratch" / str(workers), "2GB", 1),
        ) as pool:
            futures = [
                pool.submit(
                    aggregate_worker,
                    int(p.stem.removeprefix("base_")),
                    p,
                    out / ("metrics-" + str(workers)) / p.name,
                )
                for p in dense
            ]
            results = [f.result() for f in futures]
        ended = time.monotonic()
        after = resource.getrusage(resource.RUSAGE_CHILDREN)
        trials.append(
            {
                "workers": workers,
                "started_monotonic": started,
                "ended_monotonic": ended,
                "seconds": ended - started,
                "child_cpu_seconds": after.ru_utime
                + after.ru_stime
                - before.ru_utime
                - before.ru_stime,
                "parts": results,
            }
        )
        print("Dense metric workers", workers, "seconds", ended - started, flush=True)
    for p in dense:
        if sha256(out / "metrics-4" / p.name) != sha256(out / "metrics-6" / p.name):
            raise RuntimeError("Dense metric concurrency changed output")
    atomic_json(
        report_path,
        {
            "status": "passed",
            "species_per_cell": len(ids),
            "partitions": len(dense),
            "rows": sum(r["rows"] for _, r in results),
            "relationships": sum(r["relationships"] for _, r in results),
            "trials": trials,
            "outputs": "Byte-identical with four and six workers",
            "scope": "Synthetic density stress using real IDs/metadata and production worker/validation; not a geographic or full-run throughput forecast",
            "cache_note": "Four-worker run precedes six-worker run on the same input files; cache warming can affect the comparison.",
        },
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--species-limit", type=int, default=1000)
    parser.add_argument("--row-limit", type=int, default=131072)
    parser.add_argument("--partition-limit", type=int, default=6)
    args = parser.parse_args()
    main(
        args.root,
        args.output,
        args.report,
        species_limit=args.species_limit,
        row_limit=args.row_limit,
        partition_limit=args.partition_limit,
    )
