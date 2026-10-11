from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

import h3
import numpy as np
from shapely import affinity
from shapely.geometry import Point, Polygon, box, mapping, shape

from ark_pipeline.builders.coarse_cache import antimeridian_safe_polygon
from ark_pipeline.spatial.boundaries import JurisdictionIndex, cell_geometry
from ark_pipeline.spatial.coverage import (
    cell_polygon,
    exact_intersecting_cells_native,
    load_spatial_profile,
)
from ark_pipeline.tiles import BoundaryBatchIndex, geometries_for_cells

ROOT = Path(__file__).resolve().parents[1]


class SpatialGeometryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.profile = load_spatial_profile(ROOT / "config/spatial_semantics_any_touch_v2.toml")

    def test_polar_geometry_agrees_across_scalar_batch_coverage_and_geojson(self):
        for resolution in (3, 7):
            for latitude in (-90, 90):
                with self.subTest(resolution=resolution, latitude=latitude):
                    cell = h3.latlng_to_cell(latitude, 0, resolution)
                    centre_lat, centre_lon = h3.cell_to_latlng(cell)
                    scalar = cell_geometry(cell)
                    local = cell_polygon(cell)
                    exported = shape(antimeridian_safe_polygon([
                        [lon, lat] for lat, lon in h3.cell_to_boundary(cell)
                    ]))
                    geojson, batch = geometries_for_cells([cell])
                    self.assertTrue(scalar.is_valid)
                    self.assertTrue(local.is_valid)
                    self.assertTrue(scalar.covers(Point(centre_lon, centre_lat)))
                    for longitude in (-180, -90, 0, 90, 180):
                        self.assertTrue(scalar.covers(Point(longitude, latitude)))
                    self.assertGreater(scalar.area, 0)
                    self.assertGreaterEqual(scalar.bounds[0], -180)
                    self.assertLessEqual(scalar.bounds[2], 180)
                    self.assertTrue(scalar.equals(exported))
                    self.assertTrue(scalar.equals(shape(geojson[0])))
                    self.assertTrue(scalar.equals(batch[0]))
                    # Canonical scalar copies and the continuous coverage ring
                    # describe the same cap after longitude-window clipping.
                    pieces = [affinity.translate(local, xoff=turn * 360).intersection(
                        box(-180, -90, 180, 90)) for turn in (-1, 0, 1)]
                    from shapely import union_all
                    # h3ronpy and the scalar H3 binding differ by floating-point
                    # roundoff in boundary coordinates; the cap contract agrees.
                    self.assertLess(scalar.hausdorff_distance(union_all(pieces)), 1e-9)
                    cap = box(-180, 89.99, 180, 90) if latitude > 0 else box(-180, -90, 180, -89.99)
                    with tempfile.TemporaryDirectory() as directory:
                        path = Path(directory) / "polar.json"
                        path.write_text(json.dumps({"features": [{
                            "properties": {"code": "POLE", "name": "Synthetic cap"},
                            "geometry": mapping(cap),
                        }]}))
                        index = JurisdictionIndex(path)
                        self.assertEqual(index.codes_for_cell(cell), ("POLE",))
                        self.assertEqual(BoundaryBatchIndex(index).codes_for_geometries(batch), [("POLE",)])
                    actual = exact_intersecting_cells_native(local, replace(self.profile, resolution=resolution))
                    expected = sorted(h3.str_to_int(value) for value in h3.grid_disk(cell, 1))
                    self.assertEqual(actual.cells.tolist(), expected)

    def test_exact_edges_include_every_neighbour_at_dateline_and_high_latitude(self):
        for resolution in (3, 7):
            for latitude, longitude in ((55, 90), (84, 179.999), (-84, -179.999), (0, 179.999)):
                with self.subTest(resolution=resolution, latitude=latitude, longitude=longitude):
                    cell = h3.latlng_to_cell(latitude, longitude, resolution)
                    actual = exact_intersecting_cells_native(
                        cell_polygon(cell), replace(self.profile, resolution=resolution))
                    expected = sorted(h3.str_to_int(value) for value in h3.grid_disk(cell, 1))
                    self.assertEqual(actual.cells.tolist(), expected)

    def test_vertex_contact_is_included_without_accepting_the_whole_halo(self):
        cell = "870b9332affffff"
        polygon = cell_polygon(cell)
        vertex = polygon.exterior.coords[0]
        centre = polygon.centroid
        # A triangle inside the cell touches only this vertex of its boundary.
        source = Polygon([vertex, (centre.x + 0.0001, centre.y),
                          (centre.x, centre.y + 0.0001)])
        expected = []
        vertex_contacts = []
        for neighbour in h3.grid_disk(cell, 1):
            intersection = source.intersection(cell_polygon(neighbour))
            if not intersection.is_empty:
                expected.append(h3.str_to_int(neighbour))
                if intersection.geom_type == "Point":
                    vertex_contacts.append(neighbour)
        self.assertEqual(len(vertex_contacts), 2)
        actual = exact_intersecting_cells_native(source, self.profile)
        self.assertEqual(actual.cells.tolist(), sorted(expected))
        self.assertEqual(actual.cells.size, 3)

    def test_accepted_seven_degree_grid_preserves_both_sides_of_date_line(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "seven.toml"
            path.write_text((ROOT / "config/spatial_semantics_any_touch_v2.toml").read_text().replace(
                "candidate_tile_degrees = 10.0", "candidate_tile_degrees = 7.0"))
            seven = load_spatial_profile(path)
        for resolution, source in ((7, Polygon([(179.99, -0.01), (-179.99, -0.01),
                                                (-179.99, 0.01), (179.99, 0.01)])),
                                   (3, box(179, -1, 181, 1))):
            expected = exact_intersecting_cells_native(source, replace(self.profile, resolution=resolution))
            actual = exact_intersecting_cells_native(source, replace(seven, resolution=resolution))
            np.testing.assert_array_equal(actual.cells, expected.cells)
            if resolution == 7:
                self.assertEqual(actual.cells.size, 5)
                self.assertIn(h3.str_to_int("877eb5720ffffff"), actual.cells)
            else:
                self.assertEqual(actual.cells.size, 12)


if __name__ == "__main__":
    unittest.main()
