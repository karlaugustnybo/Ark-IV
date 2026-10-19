from __future__ import annotations

import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier, Lock
from time import sleep
from unittest.mock import patch

import h3
import shapely
from shapely.geometry import mapping, shape

from ark_pipeline.spatial.boundaries import JurisdictionIndex, cell_geometry


class BoundaryCacheTests(unittest.TestCase):
    def test_concurrent_cell_and_point_lookups_serialize_prepared_predicates(self):
        cell = h3.latlng_to_cell(55.7, 12.6, 7)
        polygon = cell_geometry(cell)
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "boundary.json"
            path.write_text(json.dumps({"features": [{
                "properties": {"code": "TEST", "name": "Test"},
                "geometry": mapping(polygon),
            }]}))
            index = JurisdictionIndex(path)
            active = 0
            maximum_active = 0
            counter_lock = Lock()

            class CheckedGeometry:
                def predicate(self, name, geometry):
                    nonlocal active, maximum_active
                    with counter_lock:
                        active += 1
                        maximum_active = max(maximum_active, active)
                    try:
                        # Release the GIL as native GEOS predicates do. Call a
                        # separate unprepared geometry so a regression fails
                        # an assertion instead of crashing the test process.
                        sleep(0.001)
                        return getattr(polygon, name)(geometry)
                    finally:
                        with counter_lock:
                            active -= 1

                def intersects(self, geometry):
                    return self.predicate("intersects", geometry)

                def covers(self, geometry):
                    return self.predicate("covers", geometry)

            index._geometries = [CheckedGeometry()]
            start = Barrier(8)

            def lookup(worker):
                start.wait(timeout=5)
                for _ in range(16):
                    if worker % 2:
                        self.assertEqual(index.code_for_point(55.7, 12.6), "TEST")
                    else:
                        self.assertEqual(index.codes_for_cell(cell), ("TEST",))

            with ThreadPoolExecutor(max_workers=8) as executor:
                list(executor.map(lookup, range(8)))
            self.assertEqual(maximum_active, 1)

    def test_prepared_and_cached_memberships_match_unprepared_intersections(self):
        # Exact cell polygons exercise edge/vertex contact and a hole whose
        # boundary coincides with a cell, alongside polar/dateline pentagons.
        centre = h3.latlng_to_cell(55.7, 12.6, 3)
        polygon = cell_geometry(centre)
        features = [
            {"type": "Feature", "properties": {"code": code, "name": code},
             "geometry": mapping(geometry)}
            for code, geometry in (
                ("Z", polygon),
                ("A", polygon.buffer(2).difference(polygon)),
                ("B", polygon.buffer(1)),
            )
        ]
        geometries = [shape(feature["geometry"]) for feature in features]
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "boundaries.json"
            path.write_text(json.dumps({"type": "FeatureCollection", "features": features}))
            index = JurisdictionIndex(path)
            self.assertTrue(all(shapely.is_prepared(index._geometries)))
            for resolution in (3, 7):
                cells = sorted(set(h3.grid_disk(h3.latlng_to_cell(55.7, 12.6, resolution), 1)) | {
                    h3.latlng_to_cell(lat, lon, resolution)
                    for lat in (-89, 0, 89) for lon in (-179.999, 0, 179.999)
                } | set(h3.get_pentagons(resolution)))
                for cell in cells:
                    geometry = cell_geometry(cell)
                    expected = tuple(
                        feature["properties"]["code"]
                        for feature, boundary in zip(features, geometries, strict=True)
                        if boundary.intersects(geometry)
                    )
                    self.assertEqual(index.codes_for_cell(cell), expected)
                    self.assertEqual(index.codes_for_cell(cell), expected)

    def test_cache_reuses_empty_results_and_is_local_bounded_and_coarse_only(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "empty.json"
            path.write_text('{"type":"FeatureCollection","features":[]}')
            first, second = JurisdictionIndex(path), JurisdictionIndex(path)
            coarse = h3.latlng_to_cell(55.7, 12.6, 3)
            fine = h3.latlng_to_cell(55.7, 12.6, 7)
            other = h3.latlng_to_cell(-55.7, 12.6, 3)
            with patch("ark_pipeline.spatial.boundaries.COARSE_MEMBERSHIP_CACHE_SIZE", 1), patch(
                "ark_pipeline.spatial.boundaries.cell_geometry", wraps=cell_geometry,
            ) as build_cell:
                first.codes_for_cell(coarse)
                first.codes_for_cell(coarse)
                self.assertEqual(build_cell.call_count, 1)
                second.codes_for_cell(coarse)
                self.assertEqual(build_cell.call_count, 2)
                first.codes_for_cell(fine)
                first.codes_for_cell(fine)
                self.assertEqual(build_cell.call_count, 4)
                first.codes_for_cell(other)
                first.codes_for_cell(other)
                self.assertEqual(build_cell.call_count, 6)
                self.assertEqual(first._coarse_codes, {coarse: ()})


if __name__ == "__main__":
    unittest.main()
