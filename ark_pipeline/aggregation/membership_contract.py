"""Prove serving-list uniqueness without materializing a global pair hash set."""

from __future__ import annotations

import re
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq


def canonical_memberships(paths: list[Path], resolution: int) -> bool:
    """Validate disjoint, sorted base-cell parts; otherwise request SQL DISTINCT.

    Conventional serving parts must satisfy their contract, including sorted
    unique species within each cell. Arbitrary/overlapping inputs retain the
    generic DISTINCT path. Bounded Arrow batches keep memory independent of the
    number of relationships in the complete generation.
    """
    matches = [re.fullmatch(r'base_(\d+)\.parquet', p.name) for p in paths]
    if resolution != 7 or not paths or not all(matches):
        return False
    bases = [int(m.group(1)) for m in matches if m]
    if len(set(bases)) != len(bases):
        return False
    for path, base in zip(paths, bases):
        if not 0 <= base <= 121:
            raise ValueError('Invalid canonical membership base cell')
        previous = None
        for batch in pq.ParquetFile(path).iter_batches(batch_size=1024, columns=['h3_cell', 'species_ids']):
            cells, lists = batch.column(0), batch.column(1)
            if (cells.null_count or lists.null_count or
                    not pa.types.is_uint64(cells.type) or
                    not (pa.types.is_list(lists.type) or pa.types.is_large_list(lists.type)) or
                    not pa.types.is_integer(lists.type.value_type)):
                raise ValueError('Invalid canonical membership schema/nulls')
            indices = cells.to_numpy(zero_copy_only=False)
            if len(indices):
                if (np.any(((indices >> 52) & 15) != resolution) or
                        np.any(((indices >> 45) & 127) != base) or
                        np.any(indices[1:] <= indices[:-1]) or
                        (previous is not None and int(indices[0]) <= previous)):
                    raise ValueError('Canonical membership cells must be sorted, unique and in their resolution/base')
                previous = int(indices[-1])
            offsets = lists.offsets.to_numpy(zero_copy_only=False)
            start = int(offsets[0])
            values = lists.values.slice(start, int(offsets[-1]) - start)
            if values.null_count:
                raise ValueError('Null canonical membership species')
            species = values.to_numpy(zero_copy_only=False)
            if pa.types.is_unsigned_integer(values.type) and np.any(species > np.iinfo(np.int64).max):
                raise ValueError('Canonical species ID exceeds signed BIGINT')
            if len(species) > 1:
                ordered = species[1:] > species[:-1]
                # Adjacent rows need not be ordered relative to one another.
                boundaries = offsets[1:-1] - start - 1
                boundaries = boundaries[(boundaries >= 0) & (boundaries < len(ordered))]
                ordered[boundaries] = True
                if not ordered.all():
                    raise ValueError('Canonical species lists must be sorted and unique')
    return True
