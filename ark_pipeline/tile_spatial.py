"""Boundary indexes safe to preload without importing threaded database libraries."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import shapely

from ark_pipeline.spatial.boundaries import JurisdictionIndex


class BoundaryBatchIndex:
    """Index individual boundary parts; retain catalogue order and membership."""

    def __init__(self, index: JurisdictionIndex):
        self.codes = index.codes
        # A country's distant islands can give its MultiPolygon a near-global
        # envelope. Index parts separately so unrelated cells never enter the
        # expensive predicate, then map hits back to their catalogue entries.
        self.geometries, self.boundaries = shapely.get_parts(
            np.asarray(index._geometries, dtype=object), return_index=True,
        )
        self.tree = shapely.STRtree(self.geometries)
        shapely.prepare(self.geometries)
        self._region_states: dict[tuple, int] = {}

    @classmethod
    def from_path(cls, path: Path) -> BoundaryBatchIndex:
        return cls(JurisdictionIndex(path))

    def codes_for_geometries(self, cells: np.ndarray) -> list[tuple[str, ...]]:
        left, right = self.tree.query(cells)
        if len(cells) >= 32 and len(right):
            # Prove small spatial rectangles inside/outside boundary parts once
            # and reuse those proofs across nearby cells and successive batches.
            # Snap OUTWARD from actual geometry bounds. H3 parent polygons are
            # not safe substitutes: their children can protrude beyond them.
            bounds = shapely.bounds(cells) * 4
            regions = np.column_stack((np.floor(bounds[:, :2]), np.ceil(bounds[:, 2:]))).astype(np.int64)
            # Compare fixed-width integer records as bytes. NumPy's structured
            # axis sort otherwise performs Python field lookups per comparison.
            records = np.ascontiguousarray(np.column_stack((regions[left], right)))
            packed = records.view(np.dtype((np.void, records.dtype.itemsize * 5))).ravel()
            unique, inverse = np.unique(packed, return_inverse=True)
            pairs = unique.view(np.int64).reshape(-1, 5)
            states = np.empty(len(pairs), dtype=np.int8)
            missing, keys = [], [tuple(pair) for pair in pairs]
            for offset, key in enumerate(keys):
                state = self._region_states.get(key)
                if state is None:
                    missing.append(offset)
                else:
                    states[offset] = state
            if missing:
                rectangles = shapely.box(*(pairs[missing, :4].T / 4))
                boundaries = self.geometries[pairs[missing, 4]]
                intersects = shapely.intersects(boundaries, rectangles)
                covered = shapely.covers(boundaries, rectangles)
                states[missing] = np.where(covered, 1, np.where(intersects, 0, -1))
                # Bounded independently of the global number of cells. Clearing
                # only loses cached proofs; it cannot change a boundary result.
                if len(self._region_states) + len(missing) > 8192:
                    self._region_states.clear()
                self._region_states.update((keys[i], int(states[i])) for i in missing[:8192])
            hits = states[inverse] == 1
            uncertain = states[inverse] == 0
            hits[uncertain] = shapely.intersects(
                self.geometries[right[uncertain]], cells[left[uncertain]],
            )
        else:
            hits = shapely.intersects(self.geometries[right], cells[left])
        left, right = left[hits], self.boundaries[right[hits]]
        order = np.lexsort((right, left))
        left, right = left[order], right[order]
        unique = np.ones(len(left), dtype=bool)
        unique[1:] = (left[1:] != left[:-1]) | (right[1:] != right[:-1])
        codes: list[list[str]] = [[] for _ in cells]
        for cell, boundary in zip(left[unique], right[unique], strict=True):
            codes[cell].append(self.codes[boundary])
        return [tuple(value) for value in codes]
