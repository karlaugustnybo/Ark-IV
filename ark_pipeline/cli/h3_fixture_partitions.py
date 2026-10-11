"""Build exact, resumable base-cell cover and pair inputs from the local fixture.

This is a Step 3 validation tool. It writes an isolated pilot generation and
does not select or modify the global serving generation.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from collections import defaultdict
from pathlib import Path

import h3.api.numpy_int as h3
import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import shapely

from ark_pipeline.cli.h3_cover_pilot import COVER_SCHEMA, expand_cover
from ark_pipeline.cli.spatial_pairs import (
    DEFAULT_PROFILE,
    PAIR_SCHEMA,
    exclusion_reason_values,
)
from ark_pipeline.runtime.provenance import (
    atomic_json,
    code_fingerprint,
    iso_now,
    runtime_identity,
    sha256,
)
from ark_pipeline.spatial.coverage import exact_intersecting_cells_native, load_spatial_profile

ROOT = Path(__file__).resolve().parents[2]
COLUMNS = (
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
)


def _rows(path: Path):
    for batch in pq.ParquetFile(path).iter_batches(batch_size=16, columns=COLUMNS):
        yield from batch.to_pylist()


def _identity(fixture: Path, profile_path: Path) -> dict:
    return {
        "fixture_sha256": sha256(fixture),
        "profile_sha256": sha256(profile_path),
        "code_sha256": code_fingerprint(
            [
                Path(__file__),
                ROOT / "ark_pipeline/spatial/coverage.py",
                ROOT / "ark_pipeline/cli/spatial_pairs.py",
                ROOT / "ark_pipeline/cli/h3_cover_pilot.py",
            ]
        ),
    }


def run(
    fixture: Path,
    output: Path,
    profile_path: Path,
    *,
    chunk_size: int = 25,
    max_samples: int | None = None,
) -> dict:
    if chunk_size < 1 or (max_samples is not None and max_samples < 1):
        raise ValueError("chunk size and sample limit must be positive")
    profile = load_spatial_profile(profile_path)
    identity = {**_identity(fixture, profile_path), "chunk_size": chunk_size, "max_samples": max_samples}
    rows = list(_rows(fixture))
    if max_samples is not None:
        rows = rows[:max_samples]
    if len({row["sample_id"] for row in rows}) != len(rows):
        raise ValueError("fixture sample IDs are not unique")
    included, exclusions = [], defaultdict(int)
    for row in rows:
        reason = exclusion_reason_values(
            row["iucn_sis_id"],
            row["geometry_wkb"],
            row["presence"],
            row["origin"],
            profile,
            seasonal=row["seasonal"],
        )
        if reason:
            exclusions[reason] += 1
        else:
            included.append(row)
    species_ids = sorted({int(row["iucn_sis_id"]) for row in included})
    ordinals = {value: index for index, value in enumerate(species_ids)}
    source_fingerprint = hashlib.sha256(
        json.dumps(
            {
                "identity": identity,
                "species_ids": species_ids,
                "sample_ids": [row["sample_id"] for row in included],
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    output.mkdir(parents=True, exist_ok=True)
    dictionary = {
        "schema_version": 1,
        "source_fingerprint": source_fingerprint,
        "species": [{"ordinal": i, "iucn_sis_id": value} for i, value in enumerate(species_ids)],
    }
    dictionary_path = output / "species_dictionary.json"
    if dictionary_path.exists():
        if json.loads(dictionary_path.read_text()) != dictionary:
            raise ValueError("pilot output dictionary does not match current inputs")
    else:
        atomic_json(dictionary_path, dictionary)
    total_pairs = total_covers = total_kernel = total_write = 0
    by_base: dict[int, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    started = time.perf_counter()
    chunks = []
    for chunk_number, begin in enumerate(range(0, len(included), chunk_size)):
        chunk = included[begin : begin + chunk_size]
        folder = output / f"chunk_{chunk_number:04d}"
        receipt_path = folder / "receipt.json"
        if receipt_path.exists():
            receipt = json.loads(receipt_path.read_text())
            if (
                receipt.get("status") != "passed"
                or receipt.get("identity") != identity
                or receipt.get("source_fingerprint") != source_fingerprint
                or receipt.get("sample_ids") != [row["sample_id"] for row in chunk]
            ):
                raise ValueError(f"stale chunk receipt: {receipt_path}")
            for name, info in receipt["files"].items():
                path = folder / name
                if path.stat().st_size != info["bytes"] or sha256(path) != info["sha256"]:
                    raise ValueError(f"damaged chunk output: {path}")
            total_pairs += receipt["pairs"]
            total_covers += receipt["covers"]
            total_kernel += receipt["kernel_seconds"]
            total_write += receipt["write_seconds"]
            for base, values in receipt["by_base"].items():
                for key, value in values.items():
                    by_base[int(base)][key] += value
            chunks.append(receipt_path)
            continue
        if folder.exists() and any(folder.iterdir()):
            raise ValueError(f"incomplete chunk requires manual review: {folder}")
        folder.mkdir(exist_ok=True)
        cover_writers: dict[int, pq.ParquetWriter] = {}
        pair_writers: dict[int, pq.ParquetWriter] = {}
        chunk_pairs = chunk_covers = 0
        chunk_kernel = chunk_write = 0.0
        chunk_base: dict[int, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        try:
            for index, row in enumerate(chunk, start=begin + 1):
                tick = time.perf_counter()
                cells = exact_intersecting_cells_native(
                    shapely.from_wkb(row["geometry_wkb"]), profile
                ).cells
                chunk_kernel += time.perf_counter() - tick
                if cells.size and np.any(cells[1:] <= cells[:-1]):
                    raise RuntimeError("coverage kernel returned noncanonical cells")
                if not cells.size:
                    print(
                        json.dumps(
                            {
                                "chunk": chunk_number,
                                "sample": index,
                                "sample_id": row["sample_id"],
                                "pairs": 0,
                                "elapsed_seconds": round(time.perf_counter() - started, 2),
                            }
                        ),
                        flush=True,
                    )
                    continue
                bases = ((cells >> np.uint64(45)) & np.uint64(127)).astype(np.uint8)
                cuts = np.r_[0, np.flatnonzero(bases[1:] != bases[:-1]) + 1, len(cells)]
                for lo, hi in zip(cuts[:-1], cuts[1:]):
                    base = int(bases[lo])
                    fine = cells[lo:hi]
                    cover = np.sort(np.asarray(h3.compact_cells(fine), dtype=np.uint64))
                    if not np.array_equal(expand_cover(cover), fine):
                        raise RuntimeError(f"sample {row['sample_id']} base {base}: cover mismatch")
                    provenance = f"{row['logical_name']}|{row['source_layer']}|{row['source_row']}"
                    resolutions = ((cover >> np.uint64(52)) & np.uint64(15)).astype(np.uint8)
                    cover_table = pa.Table.from_arrays(
                        [
                            pa.array(np.where(resolutions == 7, "leaf", "subtree")),
                            pa.array(cover),
                            pa.array(resolutions),
                            pa.array(
                                np.full(
                                    len(cover), ordinals[int(row["iucn_sis_id"])], dtype=np.uint32
                                )
                            ),
                            pa.array([provenance] * len(cover)),
                            pa.array(["exact-leaf-compaction"] * len(cover)),
                        ],
                        schema=COVER_SCHEMA,
                    )
                    pair_table = pa.Table.from_arrays(
                        [
                            pa.array(fine),
                            pa.array(np.full(len(fine), int(row["iucn_sis_id"]), dtype=np.int64)),
                        ],
                        schema=PAIR_SCHEMA,
                    )
                    tick = time.perf_counter()
                    if base not in cover_writers:
                        cover_writers[base] = pq.ParquetWriter(
                            folder / f"base_{base:03d}_cover.parquet",
                            COVER_SCHEMA,
                            compression="zstd",
                        )
                        pair_writers[base] = pq.ParquetWriter(
                            folder / f"base_{base:03d}_pairs.parquet",
                            PAIR_SCHEMA,
                            compression="zstd",
                        )
                    cover_writers[base].write_table(cover_table)
                    pair_writers[base].write_table(pair_table)
                    chunk_write += time.perf_counter() - tick
                    chunk_pairs += len(fine)
                    chunk_covers += len(cover)
                    chunk_base[base]["pairs"] += len(fine)
                    chunk_base[base]["covers"] += len(cover)
                print(
                    json.dumps(
                        {
                            "chunk": chunk_number,
                            "sample": index,
                            "sample_id": row["sample_id"],
                            "pairs": len(cells),
                            "elapsed_seconds": round(time.perf_counter() - started, 2),
                        }
                    ),
                    flush=True,
                )
        finally:
            for writer in cover_writers.values():
                writer.close()
            for writer in pair_writers.values():
                writer.close()
        files = {
            path.name: {"bytes": path.stat().st_size, "sha256": sha256(path)}
            for path in sorted(folder.glob("*.parquet"))
        }
        receipt = {
            "status": "passed",
            "created_at": iso_now(),
            "identity": identity,
            "source_fingerprint": source_fingerprint,
            "sample_ids": [row["sample_id"] for row in chunk],
            "pairs": chunk_pairs,
            "covers": chunk_covers,
            "kernel_seconds": chunk_kernel,
            "write_seconds": chunk_write,
            "by_base": {str(base): dict(values) for base, values in chunk_base.items()},
            "files": files,
        }
        atomic_json(receipt_path, receipt)
        total_pairs += chunk_pairs
        total_covers += chunk_covers
        total_kernel += chunk_kernel
        total_write += chunk_write
        for base, values in chunk_base.items():
            for key, value in values.items():
                by_base[base][key] += value
        chunks.append(receipt_path)
    report = {
        "status": "passed",
        "created_at": iso_now(),
        "identity": identity,
        "runtime": runtime_identity(ROOT),
        "source_fingerprint": source_fingerprint,
        "dictionary_sha256": sha256(dictionary_path),
        "fixture_rows": len(rows),
        "included_rows": len(included),
        "exclusions": dict(exclusions),
        "species_count": len(species_ids),
        "cross_source_species": len(
            {
                species
                for species in species_ids
                if len({row["logical_name"] for row in included if row["iucn_sis_id"] == species})
                > 1
            }
        ),
        "pairs": total_pairs,
        "covers": total_covers,
        "kernel_seconds": total_kernel,
        "write_seconds": total_write,
        "elapsed_seconds": time.perf_counter() - started,
        "by_base": {str(base): dict(values) for base, values in by_base.items()},
        "chunk_receipts": [str(path.relative_to(output)) for path in chunks],
    }
    atomic_json(output / "producer_report.json", report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--profile", type=Path, default=DEFAULT_PROFILE)
    parser.add_argument("--chunk-size", type=int, default=25)
    parser.add_argument("--max-samples", type=int)
    args = parser.parse_args()
    report = run(
        args.fixture,
        args.output,
        args.profile,
        chunk_size=args.chunk_size,
        max_samples=args.max_samples,
    )
    print(
        json.dumps(
            {
                key: report[key]
                for key in (
                    "status",
                    "fixture_rows",
                    "included_rows",
                    "species_count",
                    "pairs",
                    "covers",
                    "elapsed_seconds",
                )
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
