"""Split topology-preserving simplification into spatially separated ring groups.

Index short sections of the original boundaries. Expanded section envelopes
connect rings that may interact while simplifying. Keep original ring order
within each group, and rebuild the original polygon/component structure.
Callers must still validate the candidate before accepting it.
"""

from __future__ import annotations

import numpy as np
import shapely
from shapely.geometry import MultiPolygon, Polygon


def simplify_grouped(geometry, tolerance):
    x0, y0, x1, y1 = geometry.bounds
    # Avoid grouping overhead for smaller extents; keep their original method.
    if tolerance <= 0 or (x1 - x0) * (y1 - y0) < 3_000:
        return shapely.simplify(geometry, tolerance, preserve_topology=True)
    polygons = [geometry] if isinstance(geometry, Polygon) else list(geometry.geoms)
    rings = [ring for polygon in polygons for ring in (polygon.exterior, *polygon.interiors)]
    if len(rings) < 256:
        return shapely.simplify(geometry, tolerance, preserve_topology=True)
    counts = [1 + len(polygon.interiors) for polygon in polygons]
    sections, owners = [], []
    for index, ring in enumerate(rings):
        coordinates = np.asarray(ring.coords)
        for start in range(0, len(coordinates) - 1, 32):
            sections.append(shapely.LineString(coordinates[start:start + 33]))
            owners.append(index)
    tree = shapely.STRtree(sections)
    owners = np.asarray(owners)
    parents = list(range(len(rings)))

    def root(index):
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    bounds = shapely.bounds(sections)
    # Allow displacement on both rings, with a conservative extra margin.
    margin = tolerance * 2.1
    for first in range(0, len(sections), 1024):
        batch = bounds[first:first + 1024]
        windows = shapely.box(batch[:, 0] - margin, batch[:, 1] - margin,
                              batch[:, 2] + margin, batch[:, 3] + margin)
        pairs = tree.query(windows)
        edges = np.stack((owners[pairs[0] + first], owners[pairs[1]]), axis=1)
        edges = np.unique(edges[edges[:, 0] != edges[:, 1]], axis=0)
        for left, right in edges:
            parents[root(int(left))] = root(int(right))
    groups = {}
    for index in range(len(rings)):
        groups.setdefault(root(index), []).append(index)
    original = np.asarray(rings, dtype=object)
    simplified = np.empty(len(rings), dtype=object)
    for members in groups.values():
        reduced = shapely.simplify(shapely.GeometryCollection(original[members]), tolerance,
                                   preserve_topology=True)
        simplified[members] = list(reduced.geoms)
    output, offset = [], 0
    for count in counts:
        output.append(Polygon(simplified[offset], simplified[offset + 1:offset + count]))
        offset += count
    return output[0] if isinstance(geometry, Polygon) else MultiPolygon(output)
