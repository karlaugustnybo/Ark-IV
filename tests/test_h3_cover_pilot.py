from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import h3.api.numpy_int as h3
import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import shapely
from shapely.geometry import Polygon, box

from ark_pipeline.cli.h3_cover_pilot import compact_exact_cells, expand_cover, run
from ark_pipeline.spatial.coverage import (
    cell_polygon,
    exact_intersecting_cells_native,
    load_spatial_profile,
)


class H3CoverPilotTests(unittest.TestCase):
    def test_complete_pentagon_and_ordinary_children_compact_exactly(self) -> None:
        ordinary = h3.latlng_to_cell(55.6761, 12.5683, 6)
        pentagon = next(cell for cell in h3.get_pentagons(6))
        for parent in (ordinary, pentagon):
            reference = np.sort(np.asarray(h3.cell_to_children(parent, 7), dtype=np.uint64))
            cover = compact_exact_cells(reference)
            np.testing.assert_array_equal(cover, [parent])
            np.testing.assert_array_equal(expand_cover(cover), reference)
            incomplete = reference[:-1]
            self.assertFalse(np.any(compact_exact_cells(incomplete) == parent))

    def test_boundary_dateline_and_polar_covers_match_exact_kernel(self) -> None:
        profile = load_spatial_profile(
            Path(__file__).resolve().parents[1] / "config/spatial_semantics_any_touch_v2.toml"
        )
        edge_cell = h3.latlng_to_cell(55.6761, 12.5683, 7)
        geometries = (
            cell_polygon(h3.int_to_str(edge_cell)),
            Polygon([(179.8, 10), (-179.8, 10), (-179.8, 10.1), (179.8, 10.1)]),
            box(-45, 89.7, -44, 89.8),
        )
        for geometry in geometries:
            exact = exact_intersecting_cells_native(geometry, profile).cells
            bases = (exact >> np.uint64(45)) & np.uint64(127)
            for base in np.unique(bases):
                reference = exact[bases == base]
                np.testing.assert_array_equal(
                    expand_cover(compact_exact_cells(reference)), reference
                )

    def test_pilot_writes_validated_single_base_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixture = root / "fixture.parquet"
            row = {
                "sample_id": 1,
                "logical_name": "fixture.zip",
                "source_sha256": "a" * 64,
                "source_layer": "range",
                "source_row": 0,
                "iucn_sis_id": 101,
                "presence": 1,
                "origin": 1,
                "seasonal": 1,
                "geometry_wkb": shapely.to_wkb(box(12.4, 55.5, 12.5, 55.6)),
                "size_bin": 10,
            }
            pq.write_table(pa.Table.from_pylist([row]), fixture)
            profile = (
                Path(__file__).resolve().parents[1] / "config/spatial_semantics_any_touch_v2.toml"
            )
            report = run(fixture, 1, root / "pilot", profile, None)
            self.assertEqual(report["status"], "passed")
            self.assertEqual(report["counts"]["missing"], 0)
            self.assertEqual(report["counts"]["extra"], 0)
            self.assertGreater(report["counts"]["base_res7_cells"], 0)
            cover = pq.read_table(next((root / "pilot").glob("*_cover.parquet")))
            self.assertTrue(set(cover["kind"].to_pylist()) <= {"leaf", "subtree"})


if __name__ == "__main__":
    unittest.main()
