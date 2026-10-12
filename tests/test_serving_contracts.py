from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import h3
import pyarrow as pa
import pyarrow.parquet as pq
from litestar import Litestar
from litestar.testing import TestClient

from backend.app import (
    _external_res7_cell_count,
    _partition_count,
    cell_species,
    species_page,
    tiles,
)
from backend.res7_tiles import REQUIRED_PARTITION_COLUMNS
from tests.test_api import RecordingPool

CELL7 = h3.latlng_to_cell(55, 12, 7)
CELL3 = h3.cell_to_parent(CELL7, 3)


class EmptyPool:
    def __init__(self):
        self.calls = []

    async def fetchval(self, *args):
        self.calls.append(args)
        return None


class ServingRequestTests(unittest.TestCase):
    def test_h3_validation_normalization_and_ecosystem_filters_before_lookup(self):
        pool = EmptyPool()
        with (
            patch("backend.app.get_pool", return_value=pool),
            patch("backend.app._cell_boundary_memberships", return_value=[]) as geometry,
        ):
            with TestClient(Litestar([cell_species]), raise_server_exceptions=False) as client:
                for cell in ("xyz", "0", "-1", "f" * 40):
                    response = client.get(f"/api/cells/{cell}/species?resolution=3")
                    self.assertEqual(response.status_code, 404)
                for cell, resolution in ((CELL7, 3), (CELL3, 7)):
                    self.assertEqual(
                        client.get(
                            f"/api/cells/{cell}/species?resolution={resolution}"
                        ).status_code,
                        400,
                    )
                self.assertEqual(
                    client.get(f"/api/cells/{CELL3}/species?system=marinne").status_code, 400
                )
                self.assertEqual(pool.calls, [])
                geometry.assert_not_called()
                for system in ("", "Terrestrial", "Freshwater", "Marine"):
                    response = client.get(
                        f"/api/cells/{CELL3.upper()}/species", params={"system": system}
                    )
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.json()["h3_index"], CELL3)
                    self.assertEqual(response.json()["stats"]["total"], 0)

    def test_all_weights_reject_nonfinite_before_queries_and_defaults_stay_numeric(self):
        pool = RecordingPool()
        with (
            patch("backend.app.get_pool", return_value=pool),
            TestClient(Litestar([species_page]), raise_server_exceptions=False) as client,
        ):
            for weight in (
                "cr",
                "en",
                "vu",
                "nt",
                "dd",
                "lc",
                "sp",
                "gen",
                "fam",
                "gdd",
                "samp",
                "cov",
            ):
                for value in ("nan", "inf", "-inf"):
                    self.assertEqual(
                        client.get("/api/species", params={weight: value}).status_code, 400
                    )
            self.assertEqual(pool.calls, [])
            self.assertEqual(client.get("/api/species", params={"cr":"1e200", "fam":"1e200"}).status_code, 400)
            self.assertEqual(pool.calls, [])
            response = client.get("/api/species")
            self.assertEqual(response.status_code, 200)
            self.assertIsInstance(response.json()["rows"][0]["priority"], float)
            self.assertEqual(
                pool.calls[-1][2][:11], (4.0, 3.0, 2.0, 1.0, 2.0, 0.1, 4.0, 3.0, 2.0, 0.0, 4.0)
            )

    def test_fine_unavailable_is_not_confirmed_empty_and_available_rows_are_read(self):
        pool = EmptyPool()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            base = h3.get_base_cell_number(CELL7)
            settings = SimpleNamespace(res7_parts_dir=None)
            with (
                patch("backend.app.settings", settings),
                patch("backend.app.get_pool", return_value=pool),
                patch("backend.app._cell_boundary_memberships", return_value=[]),
                TestClient(Litestar([cell_species])) as client,
            ):

                def details():
                    return client.get(f"/api/cells/{CELL7}/species?resolution=7").json()

                self.assertTrue(details()["species_data_unavailable"])
                self.assertIsNone(details()["stats"])
                settings.res7_parts_dir = root / "absent"
                self.assertTrue(details()["species_data_unavailable"])
                settings.res7_parts_dir = root
                self.assertTrue(details()["species_data_unavailable"])
                schema = pa.schema(
                    [("h3_cell", pa.uint64()), ("species_ids", pa.list_(pa.int64()))]
                )
                pq.write_table(
                    pa.Table.from_pylist([], schema=schema), root / f"base_{base}.parquet"
                )
                self.assertFalse(details()["species_data_unavailable"])
                self.assertEqual(details()["stats"]["total"], 0)
                pq.write_table(
                    pa.Table.from_pylist(
                        [{"h3_cell": h3.str_to_int(CELL7), "species_ids": [101]}], schema=schema
                    ),
                    root / f"base_{base}.parquet",
                )

                async def fetch(*args):
                    return [
                        dict(
                            gbif_accepted_id="101",
                            iucn_sis_id="101",
                            iucn_assessment_id=None,
                            gbif_taxon_id=None,
                            goat_taxon_id=None,
                            species_name="Synthetic species",
                            family="",
                            redlist_category="Not Assessed",
                            has_dna_species_level=False,
                            genus_has_dna=False,
                            family_has_dna=False,
                            goat_data_deficient=False,
                        )
                    ]

                pool.fetch = fetch
                self.assertEqual(details()["stats"]["total"], 1)


class PublicationStateTests(unittest.TestCase):
    def test_footer_cache_replacement_growth_absence_and_absolute_current_switch(self):
        _partition_count.cache_clear()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            selected = root / "current"
            self.assertIsNone(_external_res7_cell_count(selected))
            first, second = root / "first", root / "second"
            first.mkdir()
            second.mkdir()
            selected.symlink_to(first)

            def write(directory, rows, name="base_1.parquet"):
                temporary = directory / "replacement.parquet"
                pq.write_table(pa.table({"cell": list(range(rows))}), temporary)
                os.replace(temporary, directory / name)

            write(first, 1)
            self.assertEqual(_external_res7_cell_count(selected), 1)
            with patch("backend.app.pq.ParquetFile", side_effect=AssertionError("cached footer")):
                self.assertEqual(_external_res7_cell_count(selected), 1)
            write(first, 2)
            self.assertEqual(_external_res7_cell_count(selected), 2)
            write(first, 3, "base_2.parquet")
            self.assertEqual(_external_res7_cell_count(selected), 5)
            write(second, 7)
            alias = root / "next"
            alias.symlink_to(second)
            os.replace(alias, selected)
            self.assertEqual(_external_res7_cell_count(selected), 7)

    def test_dynamic_flags_replace_static_flags_and_recover_as_coverage_grows(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            raw = root / "raw"
            aggregates = root / "aggregates"
            raw.mkdir()
            aggregates.mkdir()
            metadata = root / "map-metadata.json"
            metadata.write_text(
                json.dumps(
                    {
                        "tile_schema_version": 9,
                        "available_resolutions": [3, 7],
                        "complete_resolutions": [3, 7],
                    }
                )
            )
            settings = SimpleNamespace(
                map_metadata_path=metadata,
                pmtiles_path=root / "none",
                res7_parts_dir=raw,
                res7_aggregate_parts_dir=aggregates,
            )
            for base in (15, 16):
                (raw / f"base_{base}.parquet").touch()

            def add(base):
                pq.write_table(
                    pa.table(
                        {
                            name: [CELL7 if name == "h3_index" else 1]
                            for name in REQUIRED_PARTITION_COLUMNS
                        }
                    ),
                    aggregates / f"base_{base}.parquet",
                )

            with patch("backend.app.settings", settings), TestClient(Litestar([tiles])) as client:

                def response():
                    return client.get("/tiles/map-metadata.json").json()

                self.assertEqual(response()["available_resolutions"], [3])
                self.assertEqual(response()["complete_resolutions"], [3])
                add(15)
                self.assertEqual(response()["available_resolutions"], [3, 7])
                self.assertEqual(response()["complete_resolutions"], [3])
                add(16)
                self.assertEqual(response()["complete_resolutions"], [3, 7])
                settings.res7_aggregate_parts_dir = None
                self.assertEqual(response()["complete_resolutions"], [3, 7])
                self.assertNotIn("res7_delivery", response())

    def test_same_schema_archive_revalidation_range_and_profile_identity(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / "priorities.pmtiles"
            metadata = root / "map-metadata.json"
            metadata.write_text('{"tile_schema_version":9}')
            archive.write_bytes(b"PMTiles\x03" + b"A" * 128)
            settings = SimpleNamespace(
                map_metadata_path=metadata, pmtiles_path=archive, res7_aggregate_parts_dir=None
            )
            with patch("backend.app.settings", settings), TestClient(Litestar([tiles])) as client:
                first = client.get("/tiles/map-metadata.json").json()
                url = "/tiles/priorities.pmtiles?v=" + first["archive_version"]
                response = client.get(url, headers={"Range": "bytes=8-15"})
                self.assertEqual(response.content, b"A" * 8)
                etag = response.headers["etag"]
                self.assertEqual(response.headers["cache-control"], "no-cache")
                self.assertEqual(client.get(url, headers={"If-None-Match": etag}).status_code, 304)
                replacement = root / "new"
                replacement.write_bytes(b"PMTiles\x03" + b"B" * 128)
                os.replace(replacement, archive)
                second = client.get("/tiles/map-metadata.json").json()
                self.assertEqual(first["tile_schema_version"], second["tile_schema_version"])
                self.assertNotEqual(first["archive_version"], second["archive_version"])
                self.assertEqual(client.get(url, headers={"Range": "bytes=8-15"}).status_code, 409)
                self.assertEqual(
                    client.get("/tiles/priorities.pmtiles", headers={"If-Match": etag}).status_code,
                    412,
                )
                self.assertEqual(
                    client.get(
                        "/tiles/priorities.pmtiles",
                        headers={"Range": "bytes=8-15", "If-Range": etag},
                    ).status_code,
                    200,
                )
                current = client.get("/tiles/priorities.pmtiles", headers={"Range": "bytes=8-15"})
                self.assertEqual(current.content, b"B" * 8)
                self.assertNotEqual(current.headers["etag"], etag)
                self.assertEqual(
                    client.get(
                        "/tiles/priorities.pmtiles", headers={"Range": "bytes=999-1000"}
                    ).status_code,
                    416,
                )


class MetadataExportTests(unittest.TestCase):
    def test_optional_metadata_exports_preserve_unresolved_and_provenance_with_old_schema_support(self):
        import duckdb

        from ark_pipeline.builders.coarse_cache import export_parquet
        base = dict(gbif_accepted_id='app-101', iucn_sis_id='101', iucn_assessment_id=None,
                    gbif_taxon_id=None, goat_taxon_id=None, species_name='Synthetic species', family='',
                    redlist_category=None, has_dna_species_level=False, genus_has_dna=False,
                    family_has_dna=False, goat_data_deficient=True, edge_group_name=None,
                    has_ebp_criteria_evidence=False)
        optional = dict(metadata_status='spatial_only', spatial_redlist_category='Vulnerable',
                        spatial_metadata_provenance='synthetic spatial evidence', spatial_metadata_conflicts='[]',
                        iucn_metadata_provenance=None, iucn_ecosystem_evidence=None, iucn_assessment_latest=None)
        with tempfile.TemporaryDirectory() as temporary, duckdb.connect() as connection:
            root = Path(temporary)
            table = pa.Table.from_pylist([{**base, **optional}])
            connection.register('fixture', table)
            connection.execute('CREATE TABLE SpecInfo AS SELECT * FROM fixture')
            connection.execute('CREATE TABLE SpecSystems(gbif_accepted_id VARCHAR, system VARCHAR)')
            connection.execute('CREATE TABLE H3Res3Species(h3_index VARCHAR, gbif_ids VARCHAR[])')
            connection.execute('INSERT INTO H3Res3Species VALUES (?, ?)', [CELL3, ['app-101']])
            export_parquet(connection, root/'new', resolutions=(3,), include_expanded_cell_species=False)
            row = pq.read_table(root/'new/species.parquet').to_pylist()[0]
            self.assertEqual({key:row[key] for key in optional}, optional)
            self.assertIsNone(row['redlist_category'])
            self.assertFalse(row['has_dna_species_level'])
            for column in optional:
                connection.execute(f'ALTER TABLE SpecInfo DROP COLUMN {column}')
            export_parquet(connection, root/'old', resolutions=(3,), include_expanded_cell_species=False)
            self.assertTrue(set(optional).isdisjoint(pq.read_table(root/'old/species.parquet').schema.names))

    def test_profile_metadata_archive_and_arrow_urls_select_matching_bytes(self):
        from backend.config import Settings
        from backend.coverage_profiles import CoverageProfileMiddleware
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            profiles = {}
            for name, content in [('resident', b'A'), ('with-passage', b'B')]:
                directory = root/name
                directory.mkdir()
                (directory/'map-metadata.json').write_text('{"tile_schema_version":9}')
                (directory/'priorities.pmtiles').write_bytes(b'PMTiles\x03'+content*128)
                (directory/'res3-priorities-0123456789abcdef.arrow').write_bytes(content*8)
                profiles[name] = Settings(_env_file=None, tile_dir=directory, pmtiles_path=directory/'priorities.pmtiles', map_metadata_path=directory/'map-metadata.json')
            app = Litestar([tiles], middleware=[CoverageProfileMiddleware])
            app.state.coverage_settings = profiles
            with TestClient(app) as client:
                tokens = []
                for name, content in [('resident', b'A'), ('with-passage', b'B')]:
                    metadata = client.get('/tiles/map-metadata.json', params={'coverage_profile':name}).json()
                    self.assertEqual(metadata['coverage_profile'], name)
                    tokens.append(metadata['archive_version'])
                    archive = client.get('/tiles/priorities.pmtiles', params={'coverage_profile':name,'v':tokens[-1]}, headers={'Range':'bytes=8-15'})
                    self.assertEqual(archive.content, content*8)
                    arrow = client.get('/tiles/res3-priorities-0123456789abcdef.arrow', params={'coverage_profile':name})
                    self.assertEqual(arrow.content,content*8)
                    self.assertIn('immutable',arrow.headers['cache-control'])
                self.assertNotEqual(*tokens)
