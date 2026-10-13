"""Exercise opt-in seasonal profiles through all spatial evidence routes."""

from __future__ import annotations

import json
import tempfile
import unittest
import zipfile
from pathlib import Path

import geopandas as gpd
import h3
import h3.api.numpy_int as h3_int
import pyarrow.parquet as pq
import pyogrio
from shapely.geometry import box

from ark_pipeline.cli.spatial_pairs import build, pair_stage_identities
from ark_pipeline.runtime.provenance import sha256
from ark_pipeline.spatial.coverage import exact_intersecting_cells_native, load_spatial_profile

CONFIG = Path(__file__).resolve().parents[1] / "config"


class PartialSpatialProfileTests(unittest.TestCase):
    def test_seasonal_membership_and_identity_across_all_evidence_routes(self) -> None:
        profiles = [load_spatial_profile(CONFIG / name) for name in (
            "spatial_semantics_iucn_richness_v3.toml",
            "spatial_semantics_iucn_richness_v3_partial_basins.toml",
            "spatial_semantics_iucn_richness_v3_with_passage_partial_basins.toml",
        )]
        geometry = box(12.5680, 55.6760, 12.5682, 55.6762)
        expected_cells = set(exact_intersecting_cells_native(geometry, profiles[0]).cells)
        self.assertTrue(expected_cells)
        self.assertEqual(len({profile.digest for profile in profiles}), 3)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sources = {}

            def register(source_id: str, archive: Path, format_name: str | None = None) -> None:
                record = {
                    "logical_name": archive.name, "path": archive.name,
                    "bytes": archive.stat().st_size, "sha256": sha256(archive),
                }
                if format_name is not None:
                    record["format"] = format_name
                source = sources.setdefault(source_id, {
                    "release": "v1c" if source_id == "hydrobasins" else "synthetic",
                    "validation_status": "passed", "files": [],
                })
                source["files"].append(record)

            # Every season has its own species, so unioning cannot hide passage.
            for layer, frame, archive_name, source in (
                ("ranges", gpd.GeoDataFrame({
                    "id_no": list(range(101, 106)), "presence": [1] * 5,
                    "origin": [1] * 5, "seasonal": list(range(1, 6)),
                    "geometry": [geometry] * 5,
                }, crs="EPSG:4326"), "ranges.zip", "iucn-spatial"),
                ("hybas_af_lev08_v1c", gpd.GeoDataFrame({
                    "HYBAS_ID": [1080000010], "geometry": [geometry],
                }, crs="EPSG:4326"), "hybas_af_lev01-12_v1c.zip", "hydrobasins"),
            ):
                folder = root / layer
                folder.mkdir()
                pyogrio.write_dataframe(frame, folder / f"{layer}.shp")
                archive = root / archive_name
                with zipfile.ZipFile(archive, "w") as handle:
                    for member in folder.iterdir():
                        handle.write(member, member.name)
                register(source, archive)
            for name, format_name, header, rows in (
                ("points.zip", "point", "id_no,presence,origin,seasonal,dec_lat,dec_long\n",
                 [f"{200 + season},1,1,{season},55.6761,12.5681\n" for season in range(1, 6)]),
                ("basins.zip", "hydrobasin", "hybas_id,id_no,presence,origin,seasonal\n",
                 [f"1080000010,{300 + season},1,1,{season}\n" for season in range(1, 6)]),
            ):
                archive = root / name
                with zipfile.ZipFile(archive, "w") as handle:
                    handle.writestr("fixture.csv", header + "".join(rows))
                register("iucn-spatial-tables", archive, format_name)
            (root / "acquisition").mkdir()
            manifest = {"schema_version": 1, "sources": sources}
            (root / "acquisition/current.json").write_text(json.dumps(manifest))
            identities = []
            expected_seasons = ((1, 2, 3, 5), (1, 2, 3, 5), (1, 2, 3, 4, 5))
            for profile, seasons in zip(profiles, expected_seasons, strict=True):
                with self.subTest(profile=profile.profile_id):
                    self.assertEqual(profile.seasonality, seasons)
                    output = root / profile.profile_id
                    report = build(root, output, profile, set(), force=False, workers=1,
                                   scratch_dir=root / "scratch", memory_limit="256MB", threads=1)
                    records, stage_ids = pair_stage_identities(root, manifest, profile)
                    identities.append(stage_ids)
                    self.assertEqual(report["semantic_profile"], {
                        "id": profile.profile_id, "sha256": profile.digest})
                    for record in records:
                        name = record["logical_name"]
                        stem = Path(name).stem
                        pairs = pq.read_table(output / "archives" / stem / "res7_pairs.parquet").to_pylist()
                        offset = {"ranges.zip": 100, "points.zip": 200, "basins.zip": 300}[name]
                        expected_species = {offset + season for season in seasons}
                        self.assertEqual({row["iucn_sis_id"] for row in pairs}, expected_species)
                        for species in expected_species:
                            cells = {row["h3_index"] for row in pairs if row["iucn_sis_id"] == species}
                            expected = ({h3_int.str_to_int(h3.latlng_to_cell(55.6761, 12.5681, 7))}
                                        if name == "points.zip" else expected_cells)
                            self.assertEqual(cells, expected)
                    reused = build(root, output, profile, set(), force=False, workers=1,
                                   scratch_dir=root / "scratch", memory_limit="256MB", threads=1)
                    self.assertTrue(all(item["status"] == "reused" for item in reused["archives"]))
            self.assertNotEqual(identities[0], identities[1])
            self.assertNotEqual(identities[1], identities[2])
