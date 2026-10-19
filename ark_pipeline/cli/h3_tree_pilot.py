"""Compare a dense exact-cover base cell with the existing pair reducers."""

from __future__ import annotations

import argparse
import hashlib
import json
import threading
import time
from pathlib import Path

import duckdb
import numpy as np
import psutil
import pyarrow as pa
import pyarrow.parquet as pq

from ark_pipeline.aggregation.h3_tree import aggregate_cover_base
from ark_pipeline.aggregation.metrics import (
    METRIC_NAMES,
    aggregate_species_lists,
    species_metric_lookup,
)
from ark_pipeline.aggregation.pairs import aggregate_sorted_pairs
from ark_pipeline.builders.fine_metrics import prepare_species
from ark_pipeline.cli.h3_cover_pilot import COVER_SCHEMA
from ark_pipeline.cli.spatial_pairs import PAIR_SCHEMA
from ark_pipeline.runtime.provenance import (
    atomic_json,
    code_fingerprint,
    iso_now,
    runtime_identity,
    sha256,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def sql_path(path: Path) -> str:
    return "'" + path.resolve().as_posix().replace("'", "''") + "'"


class PeakRSS:
    def __init__(self) -> None:
        self.process = psutil.Process()
        self.peak = self.process.memory_info().rss
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self.sample, daemon=True)

    def sample(self) -> None:
        while not self.stop.wait(0.05):
            self.peak = max(self.peak, self.process.memory_info().rss)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_):
        self.stop.set()
        self.thread.join()
        self.peak = max(self.peak, self.process.memory_info().rss)


def compare_parquet(left: Path, right: Path) -> int:
    """Compare all rows and fields with bounded Arrow batches; raise on first diff."""
    a, b = pq.ParquetFile(left), pq.ParquetFile(right)
    if not a.schema_arrow.equals(b.schema_arrow):
        raise RuntimeError(f"schema mismatch: {left.name} vs {right.name}")
    if a.metadata.num_rows != b.metadata.num_rows:
        raise RuntimeError(f"row-count mismatch: {left.name} vs {right.name}")
    left_batches = iter(a.iter_batches(batch_size=4096))
    right_batches = iter(b.iter_batches(batch_size=4096))
    left_batch = next(left_batches, None)
    right_batch = next(right_batches, None)
    compared = 0
    while left_batch is not None and right_batch is not None:
        count = min(left_batch.num_rows, right_batch.num_rows)
        left_part, right_part = left_batch.slice(0, count), right_batch.slice(0, count)
        if not left_part.equals(right_part):
            for index in range(count):
                if not left_part.slice(index, 1).equals(right_part.slice(index, 1)):
                    raise RuntimeError(
                        f"row {compared + index} differs in {left.name}: "
                        f"{left_part.slice(index, 1).to_pylist()} vs "
                        f"{right_part.slice(index, 1).to_pylist()}"
                    )
        compared += count
        left_batch = (
            left_batch.slice(count) if count < left_batch.num_rows else next(left_batches, None)
        )
        right_batch = (
            right_batch.slice(count) if count < right_batch.num_rows else next(right_batches, None)
        )
    if left_batch is not None or right_batch is not None or compared != a.metadata.num_rows:
        raise RuntimeError(f"stream length mismatch: {left.name} vs {right.name}")
    return compared


def run(
    samples: list[Path],
    species_path: Path,
    systems_path: Path,
    output: Path,
    *,
    base_cell: int,
    duplicate_sample: int | None,
    first: str,
    memory_limit: str,
) -> dict:
    if first not in {"baseline", "candidate"}:
        raise ValueError("first must be baseline or candidate")
    if output.exists() and any(output.iterdir()):
        raise ValueError(f"output directory is not empty: {output}")
    output.mkdir(parents=True, exist_ok=True)
    cover_tables = []
    cover_species_ids = []
    pair_tables = []
    sources = []
    species_ids = []
    profiles = set()
    duplicate_found = False
    for sample in samples:
        receipt_path = sample / "validation_report.json"
        receipt = json.loads(receipt_path.read_text())
        if receipt["status"] != "passed" or receipt["base_cell"] != base_cell:
            raise ValueError(f"invalid or wrong-base receipt: {receipt_path}")
        profiles.add(receipt["identity"]["profile_sha256"])
        for name, item in receipt["outputs"].items():
            path = sample / name
            if path.stat().st_size != item["bytes"] or sha256(path) != item["sha256"]:
                raise ValueError(f"stale or corrupt input: {path}")
        species_id = int(receipt["source"]["iucn_sis_id"])
        species_ids.append(species_id)
        cover = pq.read_table(sample / f"base_{base_cell}_cover.parquet")
        pairs = pq.read_table(sample / f"base_{base_cell}_reference_pairs.parquet")
        cover_tables.append(cover)
        cover_species_ids.append(species_id)
        pair_tables.append(pairs)
        sources.append(
            {
                "sample_id": receipt["sample_id"],
                "species_id": species_id,
                "receipt_sha256": sha256(receipt_path),
            }
        )
        if receipt["sample_id"] == duplicate_sample:
            duplicate_found = True
            duplicate = cover.set_column(
                cover.schema.get_field_index("provenance_key"),
                "provenance_key",
                pa.array(
                    [value + "|duplicate-source" for value in cover["provenance_key"].to_pylist()]
                ),
            )
            cover_tables.append(duplicate)
            cover_species_ids.append(species_id)
            pair_tables.append(pairs)
    if duplicate_sample is not None and not duplicate_found:
        raise ValueError("duplicate sample is absent")
    if len(species_ids) != len(set(species_ids)):
        raise ValueError("sample species IDs must be distinct")
    if len(profiles) != 1:
        raise ValueError("cover inputs have different spatial profiles")
    canonical_ids = sorted(species_ids)
    combined_cover = pa.concat_tables(cover_tables)
    cover_ordinals = np.concatenate(
        [
            np.full(len(table), canonical_ids.index(species_id), dtype=np.uint32)
            for table, species_id in zip(cover_tables, cover_species_ids)
        ]
    )
    combined_cover = combined_cover.set_column(
        COVER_SCHEMA.get_field_index("species_ordinal"), "species_ordinal", pa.array(cover_ordinals)
    )
    order = np.argsort(combined_cover["h3_index"].to_numpy(), kind="stable")
    combined_cover = combined_cover.take(pa.array(order))
    cover_path = output / "cover.parquet"
    pq.write_table(combined_cover, cover_path, compression="zstd")
    pairs_path = output / "reference_pairs.parquet"
    pq.write_table(pa.concat_tables(pair_tables).cast(PAIR_SCHEMA), pairs_path, compression="zstd")
    dictionary = {
        "schema_version": 1,
        "source_fingerprint": hashlib.sha256(
            json.dumps(
                {
                    "sources": sources,
                    "profile_sha256": next(iter(profiles)),
                    "duplicate_sample": duplicate_sample,
                    "species_ids": canonical_ids,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest(),
        "species": [
            {"ordinal": ordinal, "iucn_sis_id": species_id}
            for ordinal, species_id in enumerate(canonical_ids)
        ],
    }
    dictionary_path = output / "species_dictionary.json"
    atomic_json(dictionary_path, dictionary)

    connection = duckdb.connect()
    try:
        scratch = output / "scratch"
        scratch.mkdir()
        connection.execute(f"SET memory_limit = '{memory_limit}'")
        connection.execute(f"SET temp_directory = {sql_path(scratch)}")
        connection.execute("SET threads = 1")
        prepare_species(connection, species_path, systems_path)
        keys, flags = species_metric_lookup(connection)
        positions = np.searchsorted(keys, canonical_ids)
        if np.any(positions >= len(keys)):
            raise ValueError("species metadata ID universe does not cover the selected pairs")
        if not np.array_equal(keys[positions], canonical_ids):
            raise ValueError("species metadata ID universe does not cover the selected pairs")
        metric_flags = np.stack(
            [
                np.fromiter((flag[position] for flag in flags), dtype=np.uint8, count=len(METRIC_NAMES))
                for position in positions
            ]
        )

        baseline = output / "baseline"
        candidate = output / "candidate"
        baseline.mkdir()
        candidate.mkdir()
        phase_timings = {}
        phase_peaks = {}
        summaries = {}
        for route in (first, "candidate" if first == "baseline" else "baseline"):
            started = time.perf_counter()
            with PeakRSS() as monitor:
                if route == "baseline":
                    fine = aggregate_sorted_pairs(
                        connection,
                        sql_path(pairs_path),
                        baseline / "fine.parquet",
                        resolution=7,
                        base_cell=base_cell,
                        deduplicate=True,
                    )
                    coarse = aggregate_sorted_pairs(
                        connection,
                        sql_path(pairs_path),
                        baseline / "coarse.parquet",
                        resolution=3,
                        base_cell=base_cell,
                        derive_res3=True,
                    )
                    aggregate_species_lists(
                        connection, baseline / "fine.parquet", baseline / "metrics.parquet"
                    )
                    summaries[route] = {"fine": fine, "coarse": coarse}
                else:
                    summaries[route] = aggregate_cover_base(
                        cover_path,
                        candidate / "fine.parquet",
                        candidate / "coarse.parquet",
                        candidate / "metrics.parquet",
                        base_cell=base_cell,
                        species_ids=canonical_ids,
                        metric_flags=metric_flags,
                    )
            phase_timings[route] = time.perf_counter() - started
            phase_peaks[route] = monitor.peak
    finally:
        connection.close()

    comparisons = {
        name: compare_parquet(baseline / f"{name}.parquet", candidate / f"{name}.parquet")
        for name in ("fine", "coarse", "metrics")
    }
    outputs = [
        cover_path,
        pairs_path,
        dictionary_path,
        *(
            root / f"{name}.parquet"
            for root in (baseline, candidate)
            for name in ("fine", "coarse", "metrics")
        ),
    ]
    report = {
        "status": "passed",
        "schema_version": 1,
        "created_at": iso_now(),
        "base_cell": base_cell,
        "order": first,
        "duplicate_sample": duplicate_sample,
        "sources": sources,
        "species_ids": canonical_ids,
        "metadata_sha256": sha256(species_path),
        "systems_sha256": sha256(systems_path),
        "code_sha256": code_fingerprint(
            [
                Path(__file__),
                REPOSITORY_ROOT / "ark_pipeline/aggregation/h3_tree.py",
                REPOSITORY_ROOT / "ark_pipeline/aggregation/pairs.py",
                REPOSITORY_ROOT / "ark_pipeline/aggregation/metrics.py",
                REPOSITORY_ROOT / "ark_pipeline/spatial/coverage.py",
                REPOSITORY_ROOT / "ark_pipeline/builders/fine_metrics.py",
                REPOSITORY_ROOT / "ark_pipeline/builders/coarse_cache.py",
            ]
        ),
        "runtime": runtime_identity(REPOSITORY_ROOT),
        "memory_limit": memory_limit,
        "workers": 1,
        "input_rows": {"cover": combined_cover.num_rows, "pairs": sum(len(t) for t in pair_tables)},
        "summaries": summaries,
        "compared_rows": comparisons,
        "route_seconds": phase_timings,
        "route_peak_rss_bytes": phase_peaks,
        "scratch_bytes_after": sum(
            path.stat().st_size for path in scratch.rglob("*") if path.is_file()
        ),
        "outputs": {
            str(path.relative_to(output)): {"bytes": path.stat().st_size, "sha256": sha256(path)}
            for path in outputs
        },
    }
    atomic_json(output / "validation_report.json", report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sample", type=Path, action="append", required=True)
    parser.add_argument("--species", type=Path, required=True)
    parser.add_argument("--species-systems", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--base-cell", type=int, required=True)
    parser.add_argument("--duplicate-sample", type=int)
    parser.add_argument("--first", choices=("baseline", "candidate"), default="baseline")
    parser.add_argument("--memory-limit", default="512MB")
    args = parser.parse_args()
    report = run(
        args.sample,
        args.species,
        args.species_systems,
        args.output,
        base_cell=args.base_cell,
        duplicate_sample=args.duplicate_sample,
        first=args.first,
        memory_limit=args.memory_limit,
    )
    print(
        json.dumps(
            {
                "status": report["status"],
                "compared_rows": report["compared_rows"],
                "route_seconds": report["route_seconds"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
