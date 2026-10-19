"""Bounded native reductions from per-cell species lists to serving metrics.

Evaluate the metric definitions once per species, then gather and sum small
integer arrays. H3 and species IDs remain integers throughout the expensive
relationship pass; only the final cell IDs become strings for the serving API.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from time import monotonic

import duckdb
import h3ronpy
import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from ark_pipeline.builders.coarse_cache import (
    DNA_SCORE_PREDICATES,
    METRICS,
    SYSTEM_PREDICATES,
    THREAT_SCORE_PREDICATES,
)

METRIC_NAMES = tuple(
    f"{metric}__{system.lower()}" for system in SYSTEM_PREDICATES for metric in METRICS
)
METRIC_SCHEMA = pa.schema(
    [("h3_index", pa.string()), *((name, pa.int32()) for name in METRIC_NAMES)]
)
THREAT_SUMMARIES = (
    "crit_endangered_count", "endangered_count", "vulnerable_count",
    "near_threatened_count", "data_deficient_count", "least_concern_count",
)
MISSING_METRICS = (
    "missing_species_dna", "missing_genus_dna", "missing_family_dna",
    "goat_data_deficient",
)
JOINT_METRICS = tuple(
    f"priority_{threat}_{dna}_count"
    for threat in THREAT_SCORE_PREDICATES for dna in DNA_SCORE_PREDICATES
)
METRIC_INDEX = {name: index for index, name in enumerate(METRIC_NAMES)}


def _exclusive_codes(
    flags: tuple[np.ndarray, ...], names: tuple[str, ...]
) -> np.ndarray:
    """Encode SQL predicates known to be mutually exclusive for each species."""
    columns = np.column_stack([flags[METRIC_INDEX[f"{name}__all"]] for name in names])
    if np.any(columns.sum(axis=1) > 1):
        raise ValueError("Metric predicates expected to be mutually exclusive")
    codes = columns.argmax(axis=1).astype(np.int16)
    codes[~columns.any(axis=1)] = -1
    return codes


def _reduce_metrics(
    flags: tuple[np.ndarray, ...],
    positions: np.ndarray,
    starts: np.ndarray,
    lengths: np.ndarray,
    threat_codes: np.ndarray,
    joint_codes: np.ndarray,
) -> list[pa.Array]:
    """Count category memberships once per system and cell.

    The six threat summaries and 30 threat/DNA joints have exclusive species
    categories. Four DNA summaries retain independent SQL flags, including
    their NULL behavior. This avoids gathering one relationship-sized array for
    every output column while preserving the existing 164-column schema.
    """
    cell_count = len(starts)
    cell_positions = np.repeat(np.arange(cell_count, dtype=np.int32), lengths)
    threats = threat_codes[positions]
    joints = joint_codes[positions]
    result: list[pa.Array] = []
    for system in SYSTEM_PREDICATES:
        suffix = system.lower()
        member = flags[METRIC_INDEX[f"total_species__{suffix}"]][positions].astype(bool)
        totals = np.bincount(cell_positions[member], minlength=cell_count).astype(np.int32)
        selected = member & (threats >= 0)
        threat_counts = np.bincount(
            cell_positions[selected].astype(np.int64) * len(THREAT_SUMMARIES)
            + threats[selected],
            minlength=cell_count * len(THREAT_SUMMARIES),
        ).reshape(cell_count, len(THREAT_SUMMARIES)).astype(np.int32)
        selected = member & (joints >= 0)
        joint_counts = np.bincount(
            cell_positions[selected].astype(np.int64) * len(JOINT_METRICS)
            + joints[selected],
            minlength=cell_count * len(JOINT_METRICS),
        ).reshape(cell_count, len(JOINT_METRICS)).astype(np.int32)
        columns: dict[str, np.ndarray] = {"total_species": totals}
        columns.update(zip(THREAT_SUMMARIES, threat_counts.T, strict=True))
        for name in MISSING_METRICS:
            columns[name] = np.add.reduceat(
                flags[METRIC_INDEX[f"{name}__all"]][positions] & member,
                starts,
                dtype=np.int32,
            )
        columns.update(zip(JOINT_METRICS, joint_counts.T, strict=True))
        result.extend(pa.array(columns[name]) for name in METRICS)
    return result


def species_metric_lookup(
    connection: duckdb.DuckDBPyConnection,
) -> tuple[np.ndarray, tuple[np.ndarray, ...]]:
    """Reuse the canonical SQL predicates, including their NULL semantics.

    Only canonical numeric IDs can match BIGINT source lists: accepting "001"
    or "1.5" as species 1 would change the old string join's meaning. A final
    zero row represents missing metadata, which the publication audit rejects
    through its relationship-count reconciliation.
    """
    expressions = ", ".join(
        f'coalesce(({system_predicate}) AND ({metric_predicate}), false)::UTINYINT '
        f'AS "{metric}__{system.lower()}"'
        for system, system_predicate in SYSTEM_PREDICATES.items()
        for metric, metric_predicate in METRICS.items()
    )
    lookup = connection.execute(f"""
        SELECT try_cast(gbif_accepted_id AS BIGINT) AS species_id, {expressions}
        FROM species
        WHERE cast(try_cast(gbif_accepted_id AS BIGINT) AS VARCHAR) = gbif_accepted_id
        ORDER BY species_id
    """).to_arrow_table()
    keys = lookup["species_id"].combine_chunks().to_numpy()
    flags = tuple(
        np.append(lookup[name].combine_chunks().to_numpy(), np.uint8(0))
        for name in METRIC_NAMES
    )
    return keys, flags


def aggregate_species_lists(
    connection: duckdb.DuckDBPyConnection,
    source: Path,
    target: Path,
    *,
    progress: Callable[[str, float | None], None] | None = None,
    batch_cells: int = 16_384,
    relationship_chunk: int = 250_000,
) -> None:
    """Write metrics with working arrays bounded by a batch, not a base cell.

    No cell or relationship is expanded into Python objects. The Arrow input
    batch caps cells; unusually rich batches are split again by relationship
    count, keeping a whole cell together. Output buffers flush at 16,384
    cell rows.
    """
    from ark_pipeline.runtime.progress import emit

    if batch_cells < 1 or relationship_chunk < 1:
        raise ValueError("batch_cells and relationship_chunk must be positive")
    keys, flags = species_metric_lookup(connection)
    threat_codes = _exclusive_codes(flags, THREAT_SUMMARIES)
    joint_codes = _exclusive_codes(flags, JOINT_METRICS)
    padded_keys = np.append(keys, np.int64(0))
    pending: list[pa.Table] = []
    pending_rows = 0
    processed = 0
    processed_relationships = 0
    last_progress = monotonic()
    if progress is not None:
        progress("Aggregating", 0.0)
    with pq.ParquetFile(source) as parquet, pq.ParquetWriter(
        target, METRIC_SCHEMA, compression="zstd"
    ) as writer:
        for batch in parquet.iter_batches(
            batch_size=batch_cells, columns=["h3_cell", "species_ids"]
        ):
            cells = batch.column("h3_cell")
            lists = batch.column("species_ids")
            offsets = lists.offsets.to_numpy()
            if cells.null_count or lists.null_count or np.any(np.diff(offsets) == 0):
                raise RuntimeError("Resolution-7 source contains null cells, null lists or empty lists")
            start = 0
            while start < len(cells):
                # One exceptionally rich cell may exceed the target; never split it.
                end = min(
                    len(cells),
                    max(start + 1, int(np.searchsorted(
                        offsets, offsets[start] + relationship_chunk, side="right"
                    )) - 1),
                )
                values = lists.values.slice(int(offsets[start]), int(offsets[end] - offsets[start]))
                valid = values.is_valid().to_numpy(zero_copy_only=False)
                ids = pc.fill_null(values, 0).to_numpy()
                positions = np.searchsorted(keys, ids)
                valid &= (positions < len(keys)) & (padded_keys[positions] == ids)
                positions[~valid] = len(keys)
                processed_relationships += len(ids)
                starts = offsets[start:end] - offsets[start]
                columns = [pa.array(h3ronpy.cells_to_string(cells.slice(start, end - start)))]
                columns.extend(
                    _reduce_metrics(
                        flags, positions, starts, np.diff(offsets[start:end + 1]),
                        threat_codes, joint_codes,
                    )
                )
                pending.append(pa.Table.from_arrays(columns, schema=METRIC_SCHEMA))
                pending_rows += end - start
                if pending_rows >= 16_384:
                    writer.write_table(pa.concat_tables(pending))
                    pending.clear()
                    pending_rows = 0
                start = end
            processed += len(cells)
            emit(task=source.stem, phase=f"{source.stem} · {processed_relationships:,} relationships", completed=processed,
                 total=parquet.metadata.num_rows, fraction=processed / max(1, parquet.metadata.num_rows), unit="partition cells")
            if progress is not None and monotonic() - last_progress >= 0.5:
                progress("Aggregating", processed / max(1, parquet.metadata.num_rows))
                last_progress = monotonic()
        if pending:
            writer.write_table(pa.concat_tables(pending))
    if progress is not None:
        progress("Aggregating", 1.0)
