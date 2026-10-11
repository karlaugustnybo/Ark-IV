from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from ark_pipeline.cli.sources_check_hydrobasins import audit
from ark_pipeline.cli.spatial_pairs import (
    BASIN_RELATION_SCHEMA,
    build_hydrobasin_index,
    hydrobasin_files,
    inspect_hydrobasin_archive,
)
from ark_pipeline.spatial.coverage import load_spatial_profile

PROFILE = Path("config/spatial_semantics_iucn_richness_v3.toml")


class HydrobasinSourceTests(unittest.TestCase):
    def test_island_registration_and_crs_are_validated(self):
        manifest = {"sources": {
            "hydrobasins": {"validation_status": "passed", "files": []},
            "hydrobasins-iucn-islands": {"validation_status": "failed", "files": []},
        }}
        with self.assertRaisesRegex(ValueError, "islands.*passed validation"):
            hydrobasin_files(Path("."), manifest)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "fixture.zip"
            path.write_bytes(b"synthetic-placeholder")
            record = {"resolved_path": path, "logical_name": path.name,
                      "bytes": path.stat().st_size, "source_id": "hydrobasins-iucn-islands"}
            info = {"fields": ["hybas_id"], "crs": "EPSG:3857",
                    "features": 1, "geometry_type": "Polygon"}
            with patch("pyogrio.list_layers", return_value=np.array([["fixture", "Polygon"]])), patch(
                "pyogrio.read_info", return_value=info,
            ):
                report = inspect_hydrobasin_archive(record, deep=False)
            self.assertEqual(report["status"], "failed")
            self.assertEqual(report["errors"], ["fixture: expected EPSG:4326, found EPSG:3857"])

    def test_duplicate_requested_ids_fail_before_filling(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            relations = root / "basin_species.parquet"
            pq.write_table(pa.Table.from_pydict({"hybas_id": [1080000010], "iucn_sis_id": [1]},
                                              schema=BASIN_RELATION_SCHEMA), relations)
            record = {"logical_name": "hybas_af_lev01-12_v1c.zip", "resolved_path": root / "fixture.zip",
                      "release": "fixture", "bytes": 1, "sha256": "synthetic"}
            with patch("pyogrio.read_arrow", return_value=({}, pa.table({"HYBAS_ID": [1080000010] * 2}))), patch(
                "ark_pipeline.cli.spatial_pairs._polyfill_wkb", side_effect=AssertionError("filled"),
            ):
                with self.assertRaisesRegex(ValueError, "duplicate HYBAS_ID"):
                    build_hydrobasin_index([relations], [record], root / "out", load_spatial_profile(PROFILE),
                                          "fixture", None, scratch_dir=root / "scratch",
                                          memory_limit="256MB", threads=1, force=False)
            self.assertFalse((root / "out/hydrobasins/receipt.json").exists())

    def test_invalid_missingness_policy_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "profile.toml"
            path.write_text(PROFILE.read_text().replace("[hydrobasin_coverage]",
                            '[hydrobasin_coverage]\nmissing_geometry_policy = "silent-zero"'))
            with self.assertRaisesRegex(ValueError, "missing_geometry_policy"):
                load_spatial_profile(path)

    def test_checker_distinguishes_invalid_polygon_and_unsupported_ids(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "archives/fixture/basin_species.parquet"
            path.parent.mkdir(parents=True)
            pq.write_table(pa.Table.from_pydict({"hybas_id": [1080000010, 1080000010, 42],
                                               "iucn_sis_id": [1, 2, 3]}, schema=BASIN_RELATION_SCHEMA), path)
            record = {"logical_name": "hybas_af_lev01-12_v1c.zip", "source_id": "hydrobasins",
                      "resolved_path": root / "fixture.zip", "sha256": "synthetic", "bytes": 1}
            class Reader:
                def __enter__(self):
                    return {"geometry_name": "wkb_geometry"}, [pa.record_batch({
                        "HYBAS_ID": [1080000010], "wkb_geometry": pa.array([None], type=pa.binary()),
                    })]
                def __exit__(self, *args):
                    return False
            with patch("ark_pipeline.cli.sources_check_hydrobasins.hydrobasin_files", return_value=[record]), patch(
                "ark_pipeline.cli.sources_check_hydrobasins.load_manifest", return_value={},
            ), patch("pyogrio.open_arrow", return_value=Reader()):
                report = audit(root, root)
            self.assertEqual(report["unique_requested_ids"], 2)
            self.assertEqual(report["unique_matching_ids"], 0)
            self.assertEqual(report["missing_distinct_relationships"], 3)
            self.assertEqual(report["all_distinct_basin_species_relationships"], 3)
            self.assertEqual(report["unsupported_ids"], [42])
            self.assertEqual(report["scans"][0]["empty_or_nonpolygon_ids"], [1080000010])
