from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import h3
from shapely.geometry import Polygon

from ark_pipeline.spatial.coverage import (
    cell_polygon,
    exact_intersecting_cells_native,
    load_spatial_profile,
)

CONFIG = Path(__file__).resolve().parents[1] / "config"
CORRECT = CONFIG / "spatial_semantics_iucn_richness_v3.toml"
FAST = CONFIG / "spatial_semantics_iucn_richness_v3_fast.toml"


class CoverageModeTests(unittest.TestCase):
    def test_fast_omits_exact_contacts_that_correct_preserves(self):
        cell = "870b9332affffff"
        polygon = cell_polygon(cell)
        vertex = polygon.exterior.coords[0]
        centre = polygon.centroid
        triangle = Polygon([vertex, (centre.x + 0.0001, centre.y),
                            (centre.x, centre.y + 0.0001)])
        for source, fast_count, correct_count in ((polygon, 5, 7), (triangle, 1, 3)):
            with self.subTest(expected=correct_count):
                expected = {h3.str_to_int(neighbour) for neighbour in h3.grid_disk(cell, 1)
                            if source.intersects(cell_polygon(neighbour))}
                fast = exact_intersecting_cells_native(source, load_spatial_profile(FAST))
                correct = exact_intersecting_cells_native(source, load_spatial_profile(CORRECT))
                self.assertEqual(fast.cells.size, fast_count)
                self.assertEqual(correct.cells.size, correct_count)
                self.assertEqual(set(correct.cells), expected)
                self.assertLess(set(fast.cells), expected)

    def test_modes_agree_for_range_strictly_inside_one_cell(self):
        polygon = cell_polygon("870b9332affffff")
        centre = polygon.centroid
        source = centre.buffer(0.00001)
        correct = exact_intersecting_cells_native(source, load_spatial_profile(CORRECT))
        fast = exact_intersecting_cells_native(source, load_spatial_profile(FAST))
        self.assertEqual(correct.cells.tolist(), fast.cells.tolist())
        self.assertEqual(correct.cells.size, 1)

    def test_mode_alone_changes_profile_identity_and_rejects_unknown_values(self):
        text = CORRECT.read_text()
        correct = load_spatial_profile(CORRECT)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "profile.toml"
            path.write_text(text.replace('coverage_mode = "correct"', 'coverage_mode = "fast"'))
            fast = load_spatial_profile(path)
            self.assertEqual(correct.profile_id, fast.profile_id)
            self.assertNotEqual(correct.digest, fast.digest)
            self.assertEqual(fast.coverage_mode, "fast")
            path.write_text(text.replace('coverage_mode = "correct"', 'coverage_mode = "typo"'))
            with self.assertRaisesRegex(ValueError, "coverage_mode must be fast or correct"):
                load_spatial_profile(path)

    def test_existing_profiles_default_to_correct(self):
        for path in CONFIG.glob("spatial_semantics*.toml"):
            with self.subTest(profile=path.name):
                mode = "fast" if path == FAST else "correct"
                self.assertEqual(load_spatial_profile(path).coverage_mode, mode)


if __name__ == "__main__":
    unittest.main()
