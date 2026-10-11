"""Exhaustively compare Step 3 tree outputs with the local pair reducers.

Reads the isolated fixture partitions and writes only pilot artifacts. It never
selects or changes the global serving generation or reader contract.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import duckdb
import numpy as np
import pyarrow.parquet as pq

from ark_pipeline.aggregation.h3_tree import aggregate_cover_base
from ark_pipeline.aggregation.metrics import (
    METRIC_NAMES,
    aggregate_species_lists,
    species_metric_lookup,
)
from ark_pipeline.aggregation.pairs import aggregate_sorted_pairs
from ark_pipeline.builders.fine_metrics import prepare_species
from ark_pipeline.cli.h3_tree_pilot import PeakRSS, compare_parquet, sql_path
from ark_pipeline.runtime.provenance import (
    atomic_json,
    code_fingerprint,
    iso_now,
    runtime_identity,
    sha256,
)

ROOT = Path(__file__).resolve().parents[2]


def _size(root: Path) -> int:
    if not root.exists():
        return 0
    return sum(path.stat().st_size for path in root.rglob("*") if path.is_file())


def _source_sql(paths: list[Path]) -> str:
    return "[" + ",".join(sql_path(path) for path in paths) + "]"


def _verify_sources(source: Path, producer: dict) -> dict:
    if producer.get("status") != "passed":
        raise ValueError("producer report did not pass")
    dictionary_path = source / "species_dictionary.json"
    if sha256(dictionary_path) != producer["dictionary_sha256"]:
        raise ValueError("species dictionary hash differs from producer report")
    dictionary = json.loads(dictionary_path.read_text())
    if dictionary["source_fingerprint"] != producer["source_fingerprint"]:
        raise ValueError("species dictionary source fingerprint differs")
    species_ids = [row["iucn_sis_id"] for row in dictionary["species"]]
    if species_ids != sorted(set(species_ids)):
        raise ValueError("species dictionary is not canonical")
    if [row["ordinal"] for row in dictionary["species"]] != list(range(len(species_ids))):
        raise ValueError("species ordinals are not canonical")
    if len(species_ids) != producer["species_count"]:
        raise ValueError("species dictionary count differs")
    pair_rows = cover_rows = 0
    verified_files = set()
    for name in producer["chunk_receipts"]:
        receipt_path = source / name
        receipt = json.loads(receipt_path.read_text())
        if (
            receipt["status"] != "passed"
            or receipt["identity"] != producer["identity"]
            or receipt["source_fingerprint"] != producer["source_fingerprint"]
        ):
            raise ValueError(f"chunk receipt differs: {receipt_path}")
        for filename, info in receipt["files"].items():
            path = receipt_path.parent / filename
            verified_files.add(path.resolve())
            if path.stat().st_size != info["bytes"] or sha256(path) != info["sha256"]:
                raise ValueError(f"chunk file differs: {path}")
        pair_rows += receipt["pairs"]
        cover_rows += receipt["covers"]
    actual_files = {path.resolve() for path in source.glob("chunk_*/*.parquet")}
    if actual_files != verified_files:
        raise ValueError("source partition inventory differs from chunk receipts")
    if pair_rows != producer["pairs"] or cover_rows != producer["covers"]:
        raise ValueError("producer totals do not reconcile with chunk receipts")
    return dictionary


def _metadata_flags(connection, species_ids: list[int]) -> np.ndarray:
    keys, flags = species_metric_lookup(connection)
    positions = np.searchsorted(keys, species_ids)
    if np.any(positions >= len(keys)) or not np.array_equal(keys[positions], species_ids):
        missing = sorted(set(species_ids) - set(map(int, keys)))
        raise ValueError(f"species metadata ID universe differs: {missing[:20]}")
    return np.stack(
        [
            np.fromiter((flag[position] for flag in flags), dtype=np.uint8, count=len(METRIC_NAMES))
            for position in positions
        ]
    )


def _output_hashes(folder: Path) -> dict:
    return {
        str(path.relative_to(folder)): {"bytes": path.stat().st_size, "sha256": sha256(path)}
        for path in sorted(folder.rglob("*.parquet"))
    }


def run(
    source: Path,
    species_path: Path,
    systems_path: Path,
    output: Path,
    *,
    base_cell: int | None = None,
    memory_limit: str = "1GB",
) -> dict:
    source = source.resolve()
    output = output.resolve()
    producer_path = source / "producer_report.json"
    producer = json.loads(producer_path.read_text())
    dictionary = _verify_sources(source, producer)
    species_ids = [int(row["iucn_sis_id"]) for row in dictionary["species"]]
    bases = sorted(map(int, producer["by_base"]))
    if base_cell is not None:
        if base_cell not in bases:
            raise ValueError("selected base has no fixture coverage")
        bases = [base_cell]
    output.mkdir(parents=True, exist_ok=True)
    identity = {
        "producer_report_sha256": sha256(producer_path),
        "species_sha256": sha256(species_path),
        "systems_sha256": sha256(systems_path),
        "code_sha256": code_fingerprint(
            [
                Path(__file__),
                ROOT / "ark_pipeline/aggregation/h3_tree.py",
                ROOT / "ark_pipeline/aggregation/pairs.py",
                ROOT / "ark_pipeline/aggregation/metrics.py",
                ROOT / "ark_pipeline/spatial/coverage.py",
                ROOT / "ark_pipeline/cli/h3_tree_pilot.py",
                ROOT / "ark_pipeline/builders/fine_metrics.py",
                ROOT / "ark_pipeline/builders/coarse_cache.py",
            ]
        ),
        "memory_limit": memory_limit,
        "workers": 1,
    }
    started = time.perf_counter()
    source_bytes = _size(source)
    peak_disk = source_bytes + _size(output)
    connection = duckdb.connect()
    try:
        scratch = output / "scratch"
        scratch.mkdir(exist_ok=True)
        connection.execute(f"SET memory_limit = '{memory_limit}'")
        connection.execute(f"SET temp_directory = {sql_path(scratch)}")
        connection.execute("SET threads = 1")
        prepare_species(connection, species_path, systems_path)
        flags = _metadata_flags(connection, species_ids)
        summaries = []
        for base in bases:
            folder = output / f"base_{base:03d}"
            receipt_path = folder / "receipt.json"
            if receipt_path.exists():
                receipt = json.loads(receipt_path.read_text())
                if receipt["status"] != "passed" or receipt["identity"] != identity:
                    raise ValueError(f"stale base receipt: {receipt_path}")
                for name, info in receipt["outputs"].items():
                    path = folder / name
                    if path.stat().st_size != info["bytes"] or sha256(path) != info["sha256"]:
                        raise ValueError(f"damaged base output: {path}")
                summaries.append(receipt)
                continue
            if folder.exists() and any(folder.iterdir()):
                raise ValueError(f"incomplete base requires manual review: {folder}")
            folder.mkdir(exist_ok=True)
            cover_paths = sorted(source.glob(f"chunk_*/base_{base:03d}_cover.parquet"))
            pair_paths = sorted(source.glob(f"chunk_*/base_{base:03d}_pairs.parquet"))
            if len(cover_paths) != len(pair_paths) or not cover_paths:
                raise ValueError(f"base {base} source partitions differ")
            expected = producer["by_base"][str(base)]
            if sum(pq.read_metadata(path).num_rows for path in cover_paths) != expected["covers"]:
                raise ValueError(f"base {base} cover count differs")
            if sum(pq.read_metadata(path).num_rows for path in pair_paths) != expected["pairs"]:
                raise ValueError(f"base {base} pair count differs")
            cover_path = folder / "cover_sorted.parquet"
            tick = time.perf_counter()
            connection.execute(f"""
                COPY (
                    SELECT kind, h3_index, resolution, species_ordinal, provenance_key, proof
                    FROM read_parquet({_source_sql(cover_paths)}, hive_partitioning=false)
                    ORDER BY h3_index, species_ordinal, provenance_key
                ) TO {sql_path(cover_path)} (FORMAT PARQUET, COMPRESSION ZSTD)
            """)
            cover_sort_seconds = time.perf_counter() - tick
            if pq.read_metadata(cover_path).num_rows != expected["covers"]:
                raise ValueError(f"base {base} sorted cover lost rows")
            baseline = folder / "baseline"
            candidate = folder / "candidate"
            baseline.mkdir()
            candidate.mkdir()
            pair_sql = _source_sql(pair_paths)
            order = ("baseline", "candidate") if base % 2 == 0 else ("candidate", "baseline")
            elapsed = {}
            peaks = {}
            route_summaries = {}
            for route in order:
                tick = time.perf_counter()
                with PeakRSS() as monitor:
                    if route == "baseline":
                        fine = aggregate_sorted_pairs(
                            connection,
                            pair_sql,
                            baseline / "fine.parquet",
                            resolution=7,
                            base_cell=base,
                            deduplicate=True,
                        )
                        coarse = aggregate_sorted_pairs(
                            connection,
                            pair_sql,
                            baseline / "coarse.parquet",
                            resolution=3,
                            base_cell=base,
                            derive_res3=True,
                        )
                        aggregate_species_lists(
                            connection, baseline / "fine.parquet", baseline / "metrics.parquet"
                        )
                        route_summaries[route] = {"fine": fine, "coarse": coarse}
                    else:
                        route_summaries[route] = aggregate_cover_base(
                            cover_path,
                            candidate / "fine.parquet",
                            candidate / "coarse.parquet",
                            candidate / "metrics.parquet",
                            base_cell=base,
                            species_ids=species_ids,
                            metric_flags=flags,
                        )
                elapsed[route] = time.perf_counter() - tick
                peaks[route] = monitor.peak
                peak_disk = max(peak_disk, source_bytes + _size(output))
            comparisons = {
                name: compare_parquet(baseline / f"{name}.parquet", candidate / f"{name}.parquet")
                for name in ("fine", "coarse", "metrics")
            }
            fine = route_summaries["baseline"]["fine"]
            coarse = route_summaries["baseline"]["coarse"]
            tree = route_summaries["candidate"]
            if (
                fine["input_relationships"] != expected["pairs"]
                or fine["cells"] != tree["fine_cells"]
                or fine["relationships"] != tree["fine_relationships"]
                or coarse["cells"] != tree["coarse_cells"]
                or coarse["relationships"] != tree["coarse_relationships"]
            ):
                raise ValueError(f"base {base} relationship counts differ")
            receipt = {
                "status": "passed",
                "created_at": iso_now(),
                "identity": identity,
                "base_cell": base,
                "order": list(order),
                "input_rows": expected,
                "route_summaries": route_summaries,
                "compared_rows": comparisons,
                "cover_sort_seconds": cover_sort_seconds,
                "route_seconds": elapsed,
                "route_peak_rss_bytes": peaks,
                "scratch_bytes_after": _size(scratch),
                "outputs": _output_hashes(folder),
            }
            atomic_json(receipt_path, receipt)
            summaries.append(receipt)
            print(
                json.dumps(
                    {
                        "base": base,
                        "validated": comparisons,
                        "seconds": elapsed,
                        "cover_sort_seconds": cover_sort_seconds,
                    }
                ),
                flush=True,
            )
    finally:
        connection.close()
    complete = base_cell is None and len(summaries) == len(producer["by_base"])
    if complete:
        if sum(r["input_rows"]["pairs"] for r in summaries) != producer["pairs"]:
            raise ValueError("full fixture input pair total differs")
        if sum(r["input_rows"]["covers"] for r in summaries) != producer["covers"]:
            raise ValueError("full fixture input cover total differs")
    report = {
        "status": "passed",
        "complete_fixture": complete,
        "created_at": iso_now(),
        "identity": identity,
        "runtime": runtime_identity(ROOT),
        "fixture_rows": producer["fixture_rows"],
        "included_rows": producer["included_rows"],
        "species_count": len(species_ids),
        "cross_source_species": producer["cross_source_species"],
        "bases_validated": len(summaries),
        "fine_cells": sum(r["compared_rows"]["fine"] for r in summaries),
        "coarse_cells": sum(r["compared_rows"]["coarse"] for r in summaries),
        "metric_rows": sum(r["compared_rows"]["metrics"] for r in summaries),
        "baseline_seconds": sum(r["route_seconds"]["baseline"] for r in summaries),
        "candidate_seconds": sum(r["route_seconds"]["candidate"] for r in summaries),
        "cover_sort_seconds": sum(r["cover_sort_seconds"] for r in summaries),
        "producer_kernel_seconds": producer["kernel_seconds"],
        "producer_write_seconds": producer["write_seconds"],
        "elapsed_seconds": time.perf_counter() - started,
        "peak_disk_bytes_sampled": peak_disk,
        "source_bytes": source_bytes,
        "output_bytes": _size(output),
        "partition_receipts": [
            str((output / f"base_{r['base_cell']:03d}" / "receipt.json").relative_to(output))
            for r in summaries
        ],
    }
    atomic_json(output / "validation_report.json", report)
    if complete:
        marker = {
            "schema_version": 1,
            "generation": output.name,
            "validation_report_sha256": sha256(output / "validation_report.json"),
            "scope": "isolated fixture pilot; not global preview",
        }
        atomic_json(output / "pilot_generation.json", marker)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--species", type=Path, required=True)
    parser.add_argument("--species-systems", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--base-cell", type=int)
    parser.add_argument("--memory-limit", default="1GB")
    args = parser.parse_args()
    report = run(
        args.source,
        args.species,
        args.species_systems,
        args.output,
        base_cell=args.base_cell,
        memory_limit=args.memory_limit,
    )
    print(
        json.dumps(
            {
                k: report[k]
                for k in (
                    "status",
                    "complete_fixture",
                    "bases_validated",
                    "fine_cells",
                    "baseline_seconds",
                    "candidate_seconds",
                    "cover_sort_seconds",
                    "peak_disk_bytes_sampled",
                )
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
