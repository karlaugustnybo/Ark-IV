"""Shared boundary intersection lookup for map builds and dynamic tiles."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from threading import Lock

import h3
import shapely
from shapely.affinity import translate
from shapely.geometry import Point, Polygon, box, shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union
from shapely.strtree import STRtree

COARSE_MEMBERSHIP_CACHE_SIZE = h3.get_num_cells(3)


def cell_geometry(h3_index: str) -> BaseGeometry:
    """Return an antimeridian-safe polygon for one H3 cell."""
    coordinates = [
        [longitude, latitude]
        for latitude, longitude in h3.cell_to_boundary(h3_index)
    ]
    if not coordinates:
        return Polygon()
    unwrapped = [coordinates[0]]
    for longitude, latitude in coordinates[1:]:
        previous = unwrapped[-1][0]
        while longitude - previous > 180:
            longitude -= 360
        while longitude - previous < -180:
            longitude += 360
        unwrapped.append([longitude, latitude])
    polygon = Polygon(unwrapped)
    minimum, _, maximum, _ = polygon.bounds
    if maximum > 180:
        west = polygon.intersection(box(-180, -90, 180, 90))
        east = translate(
            polygon.intersection(box(180, -90, 540, 90)), xoff=-360
        )
        return unary_union([part for part in (west, east) if not part.is_empty])
    if minimum < -180:
        east = polygon.intersection(box(-180, -90, 180, 90))
        west = translate(
            polygon.intersection(box(-540, -90, -180, 90)), xoff=360
        )
        return unary_union([part for part in (east, west) if not part.is_empty])
    return polygon


class JurisdictionIndex:
    def __init__(self, path: Path):
        catalogue = json.loads(path.read_text())
        features = catalogue.get("features", [])
        self.codes = tuple(str(feature["properties"]["code"]) for feature in features)
        self.names = {
            str(feature["properties"]["code"]): str(feature["properties"]["name"])
            for feature in features
        }
        self.properties = {
            str(feature["properties"]["code"]): feature["properties"]
            for feature in features
        }
        self._geometries = [shape(feature["geometry"]) for feature in features]
        self._tree = STRtree(self._geometries)
        # Each boundary is tested against many cells across several exports.
        # GEOS can reuse its segment index without changing intersection semantics.
        shapely.prepare(self._geometries)
        # GEOS prepared predicates mutate internal segment-intersection state.
        # The cached catalogue is shared by concurrent dynamic tile requests;
        # serialize its predicates to prevent native crashes (GEOS issue #825).
        self._predicate_lock = Lock()
        self._coarse_codes: dict[str, tuple[str, ...]] = {}

    def code_for_point(self, latitude: float, longitude: float) -> str:
        point = Point(longitude, latitude)
        with self._predicate_lock:
            for index in self._tree.query(point):
                if self._geometries[index].covers(point):
                    return self.codes[index]
        return ""

    def code_for_cell(self, h3_index: str) -> str:
        codes = self.codes_for_cell(h3_index)
        return codes[0] if codes else ""

    def codes_for_cell(self, h3_index: str) -> tuple[str, ...]:
        """Return every boundary touched by the H3 cell polygon."""
        with self._predicate_lock:
            return self._codes_for_cell(h3_index)

    def _codes_for_cell(self, h3_index: str) -> tuple[str, ...]:
        coarse = h3.get_resolution(h3_index) == 3
        if coarse:
            cached = self._coarse_codes.get(h3_index)
            if cached is not None:
                return cached
        cell = cell_geometry(h3_index)
        indexes = sorted(int(index) for index in self._tree.query(cell))
        codes = tuple(
            self.codes[index]
            for index in indexes
            if self._geometries[index].intersects(cell)
        )
        # Only the small coarse grid is reused across exports. Never retain the
        # millions of fine-cell lookups, and keep caches local to each catalogue.
        if coarse and len(self._coarse_codes) < COARSE_MEMBERSHIP_CACHE_SIZE:
            self._coarse_codes[h3_index] = codes
        return codes


@lru_cache(maxsize=8)
def load_jurisdiction_index(path_string: str) -> JurisdictionIndex:
    return JurisdictionIndex(Path(path_string))
