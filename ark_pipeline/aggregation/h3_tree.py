"""Pilot base-cell traversal of exact mixed-resolution species covers.

The input is one bounded base-cell partition. Production use still requires a
spillable partitioner and validation of complete global generations.
"""

from __future__ import annotations

from collections import OrderedDict, defaultdict
from itertools import chain
from pathlib import Path

import h3.api.numpy_int as h3
import h3ronpy
import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from ark_pipeline.aggregation.metrics import METRIC_NAMES, METRIC_SCHEMA
from ark_pipeline.aggregation.pairs import LIST_SCHEMA
from ark_pipeline.spatial.coverage import native_parents


def aggregate_cover_base(
    source: Path,
    fine_target: Path,
    coarse_target: Path,
    metric_target: Path,
    *,
    base_cell: int,
    species_ids: list[int],
    metric_flags: np.ndarray,
    batch_cells: int = 16_384,
    write_relationships: int = 1_000_000,
    max_cover_records: int = 1_000_000,
) -> dict[str, int]:
    """Expand inherited sets once while writing canonical serving-compatible rows.

    ``metric_flags`` has one row per species ordinal and one column per metric,
    using the existing SQL reducer's flags. Sparse paths are pruned. Output
    metric buffers are capped by ``batch_cells`` and list writes by
    ``write_relationships``. The cover-event index is capped by
    ``max_cover_records`` independently of available machine RAM.
    """
    if (
        not 0 <= base_cell < 122
        or batch_cells < 1
        or write_relationships < 1
        or max_cover_records < 1
    ):
        raise ValueError("invalid base cell or memory limit")
    if len(species_ids) != len(set(species_ids)) or any(value < 0 for value in species_ids):
        raise ValueError("species dictionary must contain unique nonnegative IDs")
    flags = np.asarray(metric_flags, dtype=np.uint8)
    if flags.shape != (len(species_ids), len(METRIC_NAMES)):
        raise ValueError("metric flag matrix does not match species dictionary")
    parquet = pq.ParquetFile(source)
    if parquet.metadata.num_rows > max_cover_records:
        raise ValueError("cover partition exceeds the configured record cap")
    table = parquet.read()
    nodes = table["h3_index"].to_numpy()
    resolutions = table["resolution"].to_numpy()
    ordinals = table["species_ordinal"].to_numpy()
    kinds = table["kind"].to_pylist()
    provenance = table["provenance_key"].to_pylist()
    proofs = table["proof"].to_pylist()
    if not len(nodes):
        raise ValueError("empty cover partition")
    if np.any(nodes[1:] < nodes[:-1]):
        raise ValueError("cover records must be ordered by H3 index")
    events: dict[int, set[int]] = defaultdict(set)
    active: set[int] = set()
    root = int(h3.get_res0_cells()[base_cell])
    if h3.get_base_cell_number(root) != base_cell:
        raise RuntimeError("H3 base-cell order changed")
    for node, resolution, ordinal, kind, source, proof in zip(
        nodes, resolutions, ordinals, kinds, provenance, proofs
    ):
        node = int(node)
        resolution = int(resolution)
        ordinal = int(ordinal)
        if (
            (node >> 45) & 127 != base_cell
            or not h3.is_valid_cell(node)
            or (node >> 52) & 15 != resolution
            or resolution > 7
            or kind != ("leaf" if resolution == 7 else "subtree")
            or ordinal < 0
            or ordinal >= len(species_ids)
            or not source
            or proof != "exact-leaf-compaction"
        ):
            raise ValueError("invalid cover record")
        events[node].add(ordinal)
    for parent_resolution in range(7):
        descendants = nodes[resolutions > parent_resolution]
        if descendants.size:
            active.update(np.unique(native_parents(descendants, parent_resolution)).tolist())

    fine_target.parent.mkdir(parents=True, exist_ok=True)
    coarse_target.parent.mkdir(parents=True, exist_ok=True)
    metric_target.parent.mkdir(parents=True, exist_ok=True)
    coarse: dict[int, set[int]] = defaultdict(set)
    cells: list[int] = []
    species_sets: list[tuple[int, ...]] = []
    fine_cells = relationships = 0
    last_cell = -1
    counts_cache: OrderedDict[tuple[int, ...], np.ndarray] = OrderedDict()
    metric_cache_misses = 0
    metric_cache_limit = 4096
    ordered_cache: dict[frozenset[int], tuple[int, ...]] = {}
    ordered_cache_limit = 16_384
    species_id_array = np.asarray(species_ids, dtype=np.int64)
    pending_fine: list[pa.Table] = []
    pending_relationships = 0
    last_coarse_parent = -1
    last_coarse_group: tuple[int, ...] | None = None

    def ordered(ordinal_set: set[int] | frozenset[int]) -> tuple[int, ...]:
        key = ordinal_set if isinstance(ordinal_set, frozenset) else frozenset(ordinal_set)
        group = ordered_cache.get(key)
        if group is None:
            group = tuple(sorted(key, key=species_ids.__getitem__))
            if len(ordered_cache) < ordered_cache_limit:
                ordered_cache[key] = group
        return group

    def write_batch(fine_writer: pq.ParquetWriter, metric_writer: pq.ParquetWriter) -> None:
        nonlocal fine_cells, relationships, last_cell, pending_relationships
        nonlocal metric_cache_misses, last_coarse_parent, last_coarse_group
        if not cells:
            return
        ordinal_values = np.fromiter(chain.from_iterable(species_sets), dtype=np.int32)
        values = species_id_array[ordinal_values]
        offsets = np.zeros(len(cells) + 1, dtype=np.int32)
        offsets[1:] = np.cumsum([len(group) for group in species_sets], dtype=np.int32)
        native_cells = np.asarray(cells, dtype=np.uint64)
        if np.any(native_cells[1:] <= native_cells[:-1]) or int(native_cells[0]) <= last_cell:
            raise RuntimeError("tree traversal did not produce canonical cell order")
        last_cell = int(native_cells[-1])
        pending_fine.append(
            pa.Table.from_arrays(
                [
                    pa.array(native_cells),
                    pa.ListArray.from_arrays(pa.array(offsets), pa.array(values)),
                ],
                schema=LIST_SCHEMA,
            )
        )
        pending_relationships += len(values)
        if pending_relationships >= write_relationships:
            fine_writer.write_table(pa.concat_tables(pending_fine))
            pending_fine.clear()
            pending_relationships = 0
        metric_rows = []
        for group in species_sets:
            counts = counts_cache.get(group)
            if counts is None:
                counts = flags[list(group)].sum(axis=0, dtype=np.int32)
                counts_cache[group] = counts
                metric_cache_misses += 1
                if len(counts_cache) > metric_cache_limit:
                    counts_cache.popitem(last=False)
            else:
                counts_cache.move_to_end(group)
            metric_rows.append(counts)
        matrix = np.stack(metric_rows)
        metric_writer.write_table(
            pa.Table.from_arrays(
                [
                    pa.array(h3ronpy.cells_to_string(pa.array(native_cells))),
                    *(pa.array(matrix[:, index]) for index in range(matrix.shape[1])),
                ],
                schema=METRIC_SCHEMA,
            )
        )
        parents = native_parents(native_cells, 3)
        for parent, group in zip(parents, species_sets):
            parent = int(parent)
            if parent != last_coarse_parent or group != last_coarse_group:
                coarse[parent].update(group)
                last_coarse_parent = parent
                last_coarse_group = group
        fine_cells += len(cells)
        relationships += len(values)
        cells.clear()
        species_sets.clear()

    def traverse(node: int, inherited: frozenset[int], fine_writer, metric_writer) -> None:
        local = events.get(node)
        membership = inherited | local if local else inherited
        resolution = (node >> 52) & 15
        if resolution == 7:
            if membership:
                cells.append(node)
                species_sets.append(ordered(membership))
                if len(cells) >= batch_cells:
                    write_batch(fine_writer, metric_writer)
            return
        for child in sorted(map(int, h3.cell_to_children(node, resolution + 1))):
            if membership or child in active or child in events:
                traverse(child, membership, fine_writer, metric_writer)

    with (
        pq.ParquetWriter(fine_target, LIST_SCHEMA, compression="zstd") as fine_writer,
        pq.ParquetWriter(metric_target, METRIC_SCHEMA, compression="zstd") as metric_writer,
    ):
        traverse(root, frozenset(), fine_writer, metric_writer)
        write_batch(fine_writer, metric_writer)
        if pending_fine:
            fine_writer.write_table(pa.concat_tables(pending_fine))
    if not fine_cells:
        raise RuntimeError("nonempty cover produced no fine cells")

    coarse_cells = sorted(coarse)
    coarse_groups = [ordered(coarse[cell]) for cell in coarse_cells]
    coarse_offsets = np.zeros(len(coarse_cells) + 1, dtype=np.int32)
    coarse_offsets[1:] = np.cumsum([len(group) for group in coarse_groups], dtype=np.int32)
    coarse_values = np.fromiter(
        (species_ids[ordinal] for group in coarse_groups for ordinal in group), dtype=np.int64
    )
    pq.write_table(
        pa.Table.from_arrays(
            [
                pa.array(np.asarray(coarse_cells, dtype=np.uint64)),
                pa.ListArray.from_arrays(pa.array(coarse_offsets), pa.array(coarse_values)),
            ],
            schema=LIST_SCHEMA,
        ),
        coarse_target,
        compression="zstd",
    )
    return {
        "fine_cells": fine_cells,
        "fine_relationships": relationships,
        "coarse_cells": len(coarse_cells),
        "coarse_relationships": len(coarse_values),
        "metric_cache_entries": len(counts_cache),
        "metric_cache_misses": metric_cache_misses,
    }
