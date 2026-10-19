"""Isolated, exact mixed-resolution cover experiment for a fixture polygon.

This command never changes the pair or serving pipeline. A subtree is emitted
only by compacting cells already accepted by the production resolution-7 kernel.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import resource
import time
from pathlib import Path

import h3.api.numpy_int as h3
import numpy as np
import psutil
import pyarrow as pa
import pyarrow.parquet as pq
import shapely

from ark_pipeline.cli.spatial_pairs import DEFAULT_PROFILE, PAIR_SCHEMA, exclusion_reason_values
from ark_pipeline.runtime.provenance import (
    atomic_json,
    code_fingerprint,
    iso_now,
    runtime_identity,
    sha256,
)
from ark_pipeline.spatial.coverage import (
    exact_intersecting_cells_native,
    load_spatial_profile,
    native_parents,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
COVER_VERSION = 1
COVER_SCHEMA = pa.schema(
    [
        ("kind", pa.string()),
        ("h3_index", pa.uint64()),
        ("resolution", pa.uint8()),
        ("species_ordinal", pa.uint32()),
        ("provenance_key", pa.string()),
        ("proof", pa.string()),
    ]
)


def fixture_row(path: Path, sample_id: int) -> dict:
    columns = [
        "sample_id",
        "logical_name",
        "source_sha256",
        "source_layer",
        "source_row",
        "iucn_sis_id",
        "presence",
        "origin",
        "seasonal",
        "geometry_wkb",
        "size_bin",
    ]
    for batch in pq.ParquetFile(path).iter_batches(batch_size=64, columns=columns):
        ids = batch.column("sample_id").to_numpy()
        matches = np.flatnonzero(ids == sample_id)
        if matches.size:
            return batch.slice(int(matches[0]), 1).to_pylist()[0]
    raise ValueError(f"sample_id {sample_id} is absent from {path}")


def compact_exact_cells(cells: np.ndarray) -> np.ndarray:
    """Compact only a canonical, proven-exact resolution-7 set."""
    cells = np.asarray(cells, dtype=np.uint64)
    if cells.size and (np.any(cells[1:] <= cells[:-1]) or np.any(((cells >> 52) & 15) != 7)):
        raise ValueError("expected sorted, unique resolution-7 cells")
    return np.sort(np.asarray(h3.compact_cells(cells), dtype=np.uint64))


def expand_cover(cover: np.ndarray) -> np.ndarray:
    return np.sort(np.asarray(h3.uncompact_cells(cover, 7), dtype=np.uint64))


def run(fixture: Path, sample_id: int, output: Path, profile_path: Path, base: int | None) -> dict:
    profile = load_spatial_profile(profile_path)
    row = fixture_row(fixture, sample_id)
    reason = exclusion_reason_values(
        row["iucn_sis_id"],
        row["geometry_wkb"],
        row["presence"],
        row["origin"],
        profile,
        seasonal=row["seasonal"],
    )
    if reason is not None:
        raise ValueError(f"fixture row is excluded under {profile.profile_id}: {reason}")
    if output.exists() and any(output.iterdir()):
        raise ValueError(f"output directory is not empty: {output}")
    output.mkdir(parents=True, exist_ok=True)

    started = time.perf_counter()
    geometry = shapely.from_wkb(row["geometry_wkb"])
    result = exact_intersecting_cells_native(geometry, profile)
    kernel_seconds = time.perf_counter() - started
    all_bases = (result.cells >> np.uint64(45)) & np.uint64(127)
    base_counts = np.bincount(all_bases.astype(np.uint8), minlength=122)
    if base is None:
        base = int(np.argmax(base_counts))
    if not 0 <= base < 122 or base_counts[base] == 0:
        raise ValueError(f"base cell {base} has no reference coverage")
    reference = result.cells[all_bases == base]
    del all_bases

    started = time.perf_counter()
    cover = compact_exact_cells(reference)
    compact_seconds = time.perf_counter() - started
    started = time.perf_counter()
    expanded = expand_cover(cover)
    missing = np.setdiff1d(reference, expanded, assume_unique=True)
    extra = np.setdiff1d(expanded, reference, assume_unique=True)
    validation_seconds = time.perf_counter() - started
    if missing.size or extra.size:
        raise RuntimeError(f"cover mismatch: {missing.size} missing, {extra.size} extra")
    if not np.array_equal(
        np.unique(native_parents(reference, 3)), np.unique(native_parents(expanded, 3))
    ):
        raise RuntimeError("resolution-3 membership mismatch")
    del expanded

    provenance = f"{row['logical_name']}|{row['source_layer']}|{row['source_row']}"
    resolutions = ((cover >> np.uint64(52)) & np.uint64(15)).astype(np.uint8)
    kinds = np.where(resolutions == 7, "leaf", "subtree")
    proof = np.repeat("exact-leaf-compaction", cover.size)
    cover_table = pa.Table.from_arrays(
        [
            pa.array(kinds),
            pa.array(cover),
            pa.array(resolutions),
            pa.array(np.zeros(cover.size, dtype=np.uint32)),
            pa.array(np.repeat(provenance, cover.size)),
            pa.array(proof),
        ],
        schema=COVER_SCHEMA,
    )
    pair_table = pa.Table.from_arrays(
        [
            pa.array(reference),
            pa.array(np.full(reference.size, row["iucn_sis_id"], dtype=np.int64)),
        ],
        schema=PAIR_SCHEMA,
    )
    started = time.perf_counter()
    cover_path = output / f"base_{base}_cover.parquet"
    pq.write_table(cover_table, cover_path, compression="zstd")
    cover_write_seconds = time.perf_counter() - started
    started = time.perf_counter()
    pair_path = output / f"base_{base}_reference_pairs.parquet"
    pq.write_table(pair_table, pair_path, compression="zstd")
    pair_write_seconds = time.perf_counter() - started
    started = time.perf_counter()
    saved_cover = pq.read_table(cover_path)
    saved_pairs = pq.read_table(pair_path)
    saved_nodes = saved_cover["h3_index"].to_numpy()
    saved_reference = saved_pairs["h3_index"].to_numpy()
    if not np.array_equal(saved_nodes, cover) or not np.array_equal(saved_reference, reference):
        raise RuntimeError("written cover or reference pairs differ from the validated arrays")
    if not np.array_equal(expand_cover(saved_nodes), saved_reference):
        raise RuntimeError("written cover does not expand to the written reference pairs")
    if not np.all(saved_cover["species_ordinal"].to_numpy() == 0):
        raise RuntimeError("written cover has an unexpected species ordinal")
    if not np.all(saved_pairs["iucn_sis_id"].to_numpy() == row["iucn_sis_id"]):
        raise RuntimeError("written reference pairs have an unexpected species ID")
    disk_validation_seconds = time.perf_counter() - started

    fingerprint = hashlib.sha256(f"{row['source_sha256']}|{profile.digest}".encode()).hexdigest()
    dictionary = {
        "schema_version": COVER_VERSION,
        "source_fingerprint": fingerprint,
        "species": [{"ordinal": 0, "iucn_sis_id": row["iucn_sis_id"]}],
    }
    atomic_json(output / "species_dictionary.json", dictionary)
    report = {
        "status": "passed",
        "schema_version": COVER_VERSION,
        "created_at": iso_now(),
        "sample_id": sample_id,
        "base_cell": base,
        "identity": {
            "fixture": str(fixture.resolve()),
            "fixture_sha256": sha256(fixture),
            "source_sha256": row["source_sha256"],
            "geometry_sha256": hashlib.sha256(row["geometry_wkb"]).hexdigest(),
            "profile": profile.profile_id,
            "profile_sha256": profile.digest,
            "code_sha256": code_fingerprint(
                [
                    Path(__file__),
                    REPOSITORY_ROOT / "ark_pipeline/spatial/coverage.py",
                    REPOSITORY_ROOT / "ark_pipeline/cli/spatial_pairs.py",
                ]
            ),
            "runtime": runtime_identity(REPOSITORY_ROOT),
            "cpu_model": platform.processor(),
            "workers": 1,
        },
        "source": {
            key: row[key]
            for key in ("logical_name", "source_layer", "source_row", "iucn_sis_id", "size_bin")
        },
        "counts": {
            "polygon_res7_cells": int(result.cells.size),
            "polygon_candidates": result.candidate_cells,
            "base_res7_cells": int(reference.size),
            "cover_records": int(cover.size),
            "subtrees": int(np.count_nonzero(resolutions < 7)),
            "leaves": int(np.count_nonzero(resolutions == 7)),
            "compression_ratio": round(reference.size / cover.size, 3),
            "base_res3_cells": int(np.unique(native_parents(reference, 3)).size),
            "missing": int(missing.size),
            "extra": int(extra.size),
        },
        "timings_seconds": {
            "kernel": kernel_seconds,
            "compaction": compact_seconds,
            "validation": validation_seconds,
            "cover_write": cover_write_seconds,
            "reference_pair_write": pair_write_seconds,
            "disk_validation": disk_validation_seconds,
        },
        "peak_rss_bytes": (
            resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            * (1024 if platform.system() == "Linux" else 1)
        ),
        "rss_at_report_bytes": psutil.Process().memory_info().rss,
        "outputs": {
            path.name: {"bytes": path.stat().st_size, "sha256": sha256(path)}
            for path in (cover_path, pair_path, output / "species_dictionary.json")
        },
        "scope": "single polygon, single base cell; no lists, metrics, or serving validation",
    }
    atomic_json(output / "validation_report.json", report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--sample-id", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--profile", type=Path, default=DEFAULT_PROFILE)
    parser.add_argument("--base-cell", type=int)
    args = parser.parse_args()
    report = run(args.fixture, args.sample_id, args.output, args.profile, args.base_cell)
    print(
        json.dumps(
            {
                "status": report["status"],
                "counts": report["counts"],
                "timings_seconds": report["timings_seconds"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
