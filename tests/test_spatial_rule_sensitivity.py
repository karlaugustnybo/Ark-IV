from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import h3.api.numpy_int as h3
import numpy as np
import pyarrow.parquet as pq
import shapely
from shapely.geometry import Polygon

from ark_pipeline.cli import spatial_rule_sensitivity as rules
from ark_pipeline.spatial.coverage import (
    _native_cell_polygons,
    direct_any_touch_intersecting_cells_native,
    load_spatial_profile,
)

CORRECT = load_spatial_profile(rules.CORRECT_PROFILE)
FAST = load_spatial_profile(rules.FAST_PROFILE)


def blob(lon, lat, width, height, points, seed):
    angles = np.linspace(0, 2 * np.pi, points, endpoint=False)
    radius = 1 + 0.02 * np.sin(40 * angles) + 0.01 * np.random.default_rng(seed).random(points)
    ring = np.column_stack([lon + width * radius * np.cos(angles), lat + height * radius * np.sin(angles)])
    return rules.canonical_region(shapely.make_valid(Polygon(ring)))


class MembershipRuleTests(unittest.TestCase):
    def test_single_cell_range_separates_contact_from_area_rules(self):
        cell = np.array([h3.latlng_to_cell(-18.0, 48.0, 7)], dtype=np.uint64)
        result, facts = rules.geometry_rules(_native_cell_polygons(cell)[0], CORRECT, FAST, check=True)
        cells = result[""]
        # Its six edge neighbours touch the range but share no area.
        self.assertEqual(cells["any_touch"].size, 7)
        for rule in ("centroid", "overlap_10", "overlap_25"):
            np.testing.assert_array_equal(cells[rule], cell)
        self.assertTrue(facts["kernel_check"])

    def test_rules_nest_and_windows_reproduce_whole_geometry(self):
        geometry = blob(48, -18, 0.84, 0.6, 400, 3)
        whole = rules.geometry_rules(geometry, CORRECT, FAST, check=True)[0][""]
        for rule in ("centroid", "overlap_10", "overlap_25"):
            self.assertTrue(np.isin(whole[rule], whole["any_touch"]).all(), rule)
        self.assertTrue(np.isin(whole["overlap_25"], whole["overlap_10"]).all())
        window = (47.5, -18.5, 48.3, -17.7)
        clipped = rules.geometry_rules(geometry, CORRECT, FAST, {"w": window})[0]["w"]
        for rule in rules.RULES:
            np.testing.assert_array_equal(clipped[rule], rules._in_window(whole[rule], window), err_msg=rule)

    def test_split_measurement_across_antimeridian_matches_unsplit_and_production(self):
        geometry = blob(179.5, 10, 3, 1.5, 6000, 7)
        self.assertEqual(geometry.bounds[0], -180.0)
        whole = rules.geometry_rules(geometry, CORRECT, FAST)[0][""]
        measure = rules._measure
        with patch.object(rules, "_measure", lambda *a, depth=0: measure(*a, depth=99)):
            unsplit = rules.geometry_rules(geometry, CORRECT, FAST)[0][""]
        for rule in rules.RULES[:4]:
            np.testing.assert_array_equal(whole[rule], unsplit[rule], err_msg=rule)
        production = direct_any_touch_intersecting_cells_native(
            geometry, replace(CORRECT, decision_simplification_degrees=0.0)).cells
        np.testing.assert_array_equal(whole["any_touch"], production)


class WindowAnalysisTests(unittest.TestCase):
    def test_omissions_lower_ranks_and_count_lost_species(self):
        cells = np.array([h3.latlng_to_cell(-18.0 + 0.02 * i, 48.0, 7) for i in range(200)], dtype=np.uint64)
        cells = np.unique(cells)
        with tempfile.TemporaryDirectory() as raw:
            output = Path(raw) / "measure"
            output.mkdir()
            records = []
            for number, (sis, members) in enumerate(((1, cells), (2, cells[-3:]))):
                arrays = {f"madagascar_east/{rule}": members for rule in rules.RULES}
                arrays["madagascar_east/centroid"] = members[:0] if sis == 2 else members
                np.savez(output / f"window-{number}.npz", **arrays)
                record = {"id": f"window-{number}", "kind": "window", "status": "passed", "iucn_sis_id": sis}
                (output / f"window-{number}.json").write_text(json.dumps(record))
                records.append(record)
            summary = rules.analyze_windows(records, output, {1: 0.1, 2: 16.0})["madagascar_east"]
            identical = summary["rules"]["overlap_10"]["priority"]
            self.assertEqual(identical["spearman_rank_correlation"], 1.0)
            self.assertEqual(identical["top_percent_overlap"]["1"]["jaccard"], 1.0)
            centroid = summary["rules"]["centroid"]
            self.assertEqual(centroid["species_lost_from_window"], 1)
            # The three high-priority cells drop out of the top 1% without species 2.
            self.assertEqual(centroid["priority"]["top_percent_overlap"]["1"]["common"], 0)
            table = pq.read_table(Path(raw) / "window_cells.parquet")
            self.assertEqual(table.num_rows, cells.size * len(rules.RULES))


if __name__ == "__main__":
    unittest.main()
