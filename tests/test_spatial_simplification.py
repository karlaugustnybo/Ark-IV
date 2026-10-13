from __future__ import annotations

import unittest
from unittest.mock import patch

import numpy as np
import shapely
from shapely.affinity import scale
from shapely.geometry import MultiPolygon, Polygon, box

from ark_pipeline.spatial.simplification import simplify_grouped


class GroupedSimplificationTests(unittest.TestCase):
    def test_nearby_holes_nested_islands_and_remote_components_match_geos(self):
        # Hole boundaries and islands can interact despite belonging to
        # different polygons. Grouping must preserve those relationships.
        holes = [shapely.Point(x, y).buffer(.45, quad_segs=12)
                 for x in range(1, 18) for y in range(1, 18)]
        shell = Polygon(box(0, 0, 19, 19).exterior,
                        [hole.exterior for hole in holes])
        islands = [shapely.Point(x, y).buffer(.44, quad_segs=11)
                   for x in range(1, 18, 3) for y in range(1, 18, 3)]
        geometry = MultiPolygon([shell, *islands, box(30, 30, 30.001, 30.001)])
        self.assertTrue(geometry.is_valid)
        with patch("ark_pipeline.spatial.simplification.shapely.STRtree", side_effect=AssertionError("lower band grouped")):
            self.assertTrue(shapely.equals_exact(simplify_grouped(geometry, .01),
                                                shapely.simplify(geometry, .01, preserve_topology=True), 0))
        geometry = scale(geometry, xfact=10, yfact=10, origin=(0, 0))
        for tolerance in (.001, .01, .1, 1.0):
            tolerance *= 10
            with self.subTest(tolerance=tolerance):
                expected = shapely.simplify(geometry, tolerance, preserve_topology=True)
                actual = simplify_grouped(geometry, tolerance)
                self.assertTrue(shapely.equals_exact(expected, actual, 0))

    def test_irregular_ring_clusters_match_geos(self):
        random = np.random.default_rng(1942)
        angles = np.linspace(0, 2 * np.pi, 41)[:-1]
        for trial in range(5):
            polygons = []
            for index in range(270):
                radii = random.uniform(.1, .49, len(angles))
                x, y = index % 18, index // 18
                polygons.append(Polygon(np.column_stack((
                    x + radii * np.cos(angles), y + radii * np.sin(angles),
                ))))
            geometry = MultiPolygon(polygons)
            geometry = scale(geometry, xfact=10, yfact=10, origin=(0, 0))
            tolerance = (.001, .01, .03, .1, .3)[trial] * 10
            expected = shapely.simplify(geometry, tolerance, preserve_topology=True)
            actual = simplify_grouped(geometry, tolerance)
            self.assertTrue(shapely.equals_exact(expected, actual, 0), trial)
