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
    resolve_hydrobasin_id_field,
)
from ark_pipeline.runtime.provenance import sha256
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
            with patch("pyogrio.read_info", return_value={"fields": ["HYBAS_ID"]}), patch(
                "pyogrio.read_arrow", return_value=({}, pa.table({"HYBAS_ID": [1080000010] * 2})),
            ), patch(
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
            ), patch("pyogrio.read_info", return_value={"fields": ["HYBAS_ID"]}), patch(
                "pyogrio.open_arrow", return_value=Reader(),
            ):
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
    def archive(root, prefix, identifiers, geometries, *, island=False, column=None, complete=False):
        layer = "HydroBASINS_level08_w_attributes_2023_08" if island else f"{prefix}_af_lev08_v1c"
        folder = root / layer
        folder.mkdir()
        column = column or ("hybas_id" if island else "HYBAS_ID")
        layers = [layer] if island or not complete else [f"{prefix}_af_lev{level:02d}_v1c" for level in range(1, 13)]
        for name in layers:
            pyogrio.write_dataframe(
                gpd.GeoDataFrame({column: identifiers, "geometry": geometries}, crs="EPSG:4326"),
                folder / f"{name}.shp",
                geometry_type="Point" if any(isinstance(geometry, Point) for geometry in geometries) else "Polygon",
            )
        path = root / (f"{layer}.zip" if island else f"{prefix}_af_lev01-12_v1c.zip")
        with zipfile.ZipFile(path, "w") as archive:
            for member in folder.iterdir():
                archive.write(member, member.name)
        return {"logical_name": path.name, "resolved_path": path,
                "source_id": "hydrobasins-iucn-islands" if island else "hydrobasins",
                "release": "synthetic", "bytes": path.stat().st_size, "sha256": sha256(path)}

    @staticmethod
    def register(root, record, identifier):
        relations = root / "archives/fixture/basin_species.parquet"
        relations.parent.mkdir(parents=True)
        pq.write_table(pa.Table.from_pydict({"hybas_id": [identifier], "iucn_sis_id": [1]},
                                          schema=BASIN_RELATION_SCHEMA), relations)
        source = record["source_id"]
        sources = {"hydrobasins": {"validation_status": "passed", "release": "v1c", "files": []}}
        sources[source] = {"validation_status": "passed", "release": record["release"], "files": [{
            key: value for key, value in record.items() if key not in {"resolved_path", "source_id", "release"}
        } | {"path": record["logical_name"]}]}
        (root / "acquisition").mkdir()
        (root / "acquisition/current.json").write_text(json.dumps({"schema_version": 1, "sources": sources}))
        return relations

    def test_accepted_key_casing_works_through_inspector_checker_and_builder(self):
        profile = load_spatial_profile(PROFILE)
        _initialize_worker(profile)
        for source in ("standard", "lake", "island"):
            for column in ("HYBAS_ID", "hybas_id", "HyBaS_Id"):
                with self.subTest(source=source, column=column), tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    island = source == "island"
                    identifier = 100000010 if island else 1080000010
                    record = self.archive(root, "hybas_lake" if source == "lake" else "hybas",
                                          [identifier], [box(12.56, 55.67, 12.57, 55.68)],
                                          island=island, column=column, complete=True)
                    relations = self.register(root, record, identifier)
                    self.assertEqual(inspect_hydrobasin_archive(record, deep=True)["status"], "ready")
                    self.assertEqual(audit(root, root)["unique_matching_ids"], 1)
                    with contextlib.redirect_stdout(io.StringIO()):
                        result = build_hydrobasin_index(
                            [relations], [record], root / "out", profile, "synthetic", None,
                            scratch_dir=root / "scratch", memory_limit="256MB", threads=1, force=False,
                        )
                    self.assertEqual(result["coverage_status"], "complete")
                    cells = pq.read_table(result["path"])
                    self.assertGreater(cells.num_rows, 0)
                    self.assertEqual(set(cells["hybas_id"].to_pylist()), {identifier})

    def test_missing_key_fails_consistently_before_replacing_outputs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            record = self.archive(root, "hybas", [100000010], [box(12.56, 55.67, 12.57, 55.68)],
                                  island=True, column="OTHER_ID")
            relations = self.register(root, record, 100000010)
            report = inspect_hydrobasin_archive(record, deep=True)
            self.assertEqual(report["status"], "failed")
            self.assertEqual(report["errors"], [f"{Path(record['logical_name']).stem}: missing HYBAS_ID"])
            with self.assertRaisesRegex(ValueError, "missing HYBAS_ID"):
                audit(root, root)
            prior = root / "out/hydrobasins"
            valid_root = root / "valid"
            valid_root.mkdir()
            valid_record = self.archive(valid_root, "hybas", [100000010],
                                        [box(12.56, 55.67, 12.57, 55.68)], island=True)
            profile = load_spatial_profile(PROFILE)
            _initialize_worker(profile)
            with contextlib.redirect_stdout(io.StringIO()):
                build_hydrobasin_index([relations], [valid_record], root / "out", profile,
                                      "synthetic", None, scratch_dir=root / "scratch",
                                      memory_limit="256MB", threads=1, force=False)
            paths = [prior / name for name in ("basin_cells.parquet", "basin_audit.parquet", "receipt.json")]
            prior_bytes = {path: path.read_bytes() for path in paths}
            with self.assertRaisesRegex(ValueError, "missing HYBAS_ID"):
                build_hydrobasin_index([relations], [record], root / "out", load_spatial_profile(PROFILE),
                                      "synthetic", None, scratch_dir=root / "scratch",
                                      memory_limit="256MB", threads=1, force=True)
            for path in paths:
                self.assertEqual(path.read_bytes(), prior_bytes[path])
            self.assertEqual(set(prior.iterdir()), set(paths))

    def test_ambiguous_key_casing_is_rejected_by_shared_rule(self):
        with self.assertRaisesRegex(ValueError, "synthetic: ambiguous HYBAS_ID fields"):
            resolve_hydrobasin_id_field(["HYBAS_ID", "hybas_id"], layer="synthetic")

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
