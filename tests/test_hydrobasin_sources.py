from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import geopandas as gpd
import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pyogrio
from shapely.geometry import Point, Polygon, box

from ark_pipeline.cli.sources_check_hydrobasins import audit, main
from ark_pipeline.cli.spatial_pairs import (
    BASIN_RELATION_SCHEMA,
    _initialize_worker,
    build_hydrobasin_index,
    hydrobasin_files,
    inspect_hydrobasin_archive,
)
from ark_pipeline.spatial.coverage import GeometryCoverageError, load_spatial_profile

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


class HydrobasinCheckerSelectionTests(unittest.TestCase):
    """Exercise real GDAL layers and the checker CLI against strict H3 builds."""

    @staticmethod
    def archive(root, prefix, identifiers, geometries, *, island=False):
        layer = "islands" if island else f"{prefix}_af_lev08_v1c"
        folder = root / layer
        folder.mkdir()
        column = "hybas_id" if island else "HYBAS_ID"
        pyogrio.write_dataframe(
            gpd.GeoDataFrame({column: identifiers, "geometry": geometries}, crs="EPSG:4326"),
            folder / f"{layer}.shp",
            geometry_type="Point" if any(isinstance(geometry, Point) for geometry in geometries) else "Polygon",
        )
        path = root / ("islands.zip" if island else f"{prefix}_af_lev01-12_v1c.zip")
        with zipfile.ZipFile(path, "w") as archive:
            for member in folder.iterdir():
                archive.write(member, member.name)
        return {"logical_name": path.name, "resolved_path": path,
                "source_id": "hydrobasins-iucn-islands" if island else "hydrobasins",
                "release": "synthetic", "bytes": path.stat().st_size, "sha256": prefix}

    def test_cli_and_builder_use_the_same_winning_source(self):
        polygon = box(12.56, 55.67, 12.57, 55.68)
        basin = 1080000010
        # Invalid winning geometry must never trigger fallback to standard.
        cases = [
            ("standard-only", basin, [polygon], None, True),
            ("absent-lake-id", basin, [polygon], [polygon], True),
            ("valid-lake", basin, [None], [polygon], True),
            ("null-lake", basin, [polygon], [None], False),
            ("empty-lake", basin, [polygon], [Polygon()], False),
            ("nonpolygon-lake", basin, [polygon], [Point(12.56, 55.67)], False),
            ("missing-standard-geometry", basin, [None], None, False),
            ("valid-island", 100000010, [polygon], None, True),
            ("missing-island-geometry", 100000010, [None], None, False),
            ("unselected-standard-duplicates", basin, [polygon, polygon], [polygon], True),
        ]
        profile = load_spatial_profile(PROFILE)
        _initialize_worker(profile)
        for name, identifier, standard, lake, usable in cases:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                relations = root / "archives/fixture/basin_species.parquet"
                relations.parent.mkdir(parents=True)
                pq.write_table(pa.Table.from_pydict(
                    {"hybas_id": [identifier] * 3, "iucn_sis_id": [1, 2, 2]},
                    schema=BASIN_RELATION_SCHEMA,
                ), relations)
                records = [self.archive(root, "hybas", [identifier] * len(standard), standard,
                                        island=identifier == 100000010)]
                if lake is not None:
                    lake_id = basin + 1 if name == "absent-lake-id" else basin
                    records.append(self.archive(root, "hybas_lake", [lake_id], lake))
                output = root / "report.json"
                with patch("ark_pipeline.cli.sources_check_hydrobasins.hydrobasin_files", return_value=records), patch(
                    "ark_pipeline.cli.sources_check_hydrobasins.load_manifest", return_value={},
                ), patch.object(sys, "argv", ["checker", "--root", str(root), "--relations-root", str(root),
                                              "--output", str(output)]), contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(main(), 0 if usable else 2)
                report = json.loads(output.read_text())
                self.assertEqual(report["unique_matching_ids"], int(usable))
                self.assertEqual(report["unique_missing_ids"], int(not usable))
                self.assertEqual(report["missing_distinct_relationships"], 0 if usable else 2)
                self.assertEqual(report["all_distinct_basin_species_relationships"], 2)
                if name in {"null-lake", "empty-lake"}:
                    self.assertEqual(report["unique_standard_matching_ids"], 1)
                    self.assertEqual(report["unique_recovered_ids"], 0)
                kwargs = dict(scratch_dir=root / "scratch", memory_limit="256MB", threads=1, force=False)
                if usable:
                    result = build_hydrobasin_index([relations], records, root / "out", profile,
                                                   "synthetic", None, **kwargs)
                    self.assertGreater(pq.read_table(result["path"]).num_rows, 0)
                else:
                    with self.assertRaises(GeometryCoverageError):
                        build_hydrobasin_index([relations], records, root / "out", profile,
                                              "synthetic", None, **kwargs)
                    self.assertFalse((root / "out/hydrobasins/receipt.json").exists())

    def test_selected_duplicates_fail_in_checker_and_builder(self):
        for island in (False, True):
            with self.subTest(island=island), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                identifier = 100000010 if island else 1080000010
                relations = root / "archives/fixture/basin_species.parquet"
                relations.parent.mkdir(parents=True)
                pq.write_table(pa.Table.from_pydict({"hybas_id": [identifier], "iucn_sis_id": [1]},
                                                  schema=BASIN_RELATION_SCHEMA), relations)
                polygon = box(12.56, 55.67, 12.57, 55.68)
                records = [self.archive(root, "hybas_lake", [identifier] * 2, [polygon, polygon], island=island)]
                with patch("ark_pipeline.cli.sources_check_hydrobasins.hydrobasin_files", return_value=records), patch(
                    "ark_pipeline.cli.sources_check_hydrobasins.load_manifest", return_value={},
                ), self.assertRaisesRegex(ValueError, "duplicate HYBAS_ID"):
                    audit(root, root)
                with self.assertRaisesRegex(ValueError, "duplicate HYBAS_ID"):
                    build_hydrobasin_index([relations], records, root / "out", load_spatial_profile(PROFILE),
                                          "synthetic", None, scratch_dir=root / "scratch",
                                          memory_limit="256MB", threads=1, force=False)
